"""Multi-host session discovery, indexing, search, and transcript parsing.

Collects session metadata across distributed machines without modifying remote state.
Supports Claude Code, Codex, Antigravity (AGY), OpenCode, Pi, Hermes, CodeBuddy, and Qoder.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any
from pydantic import TypeAdapter

from sqlalchemy import delete, func, select, or_
from sqlalchemy.orm import joinedload

from agent_fleet import models
from agent_fleet.db import session_scope
from agent_fleet.services.scanner import build_runner, to_ref
from agent_fleet.settings import get_settings

logger = logging.getLogger("agent_fleet.sessions")

# --------------------------------------------------------------------------
# Remote collector script (POSIX Python)
# --------------------------------------------------------------------------

COLLECTOR_SCRIPT_PY = r'''
import os, json, glob, sqlite3, sys, re
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
except Exception: pass

def extract_git_repo_name(url):
    if not url: return ''
    m = re.search(r'[:/]([^/:]+?)(?:\.git)?$', str(url).strip())
    if m:
        name = m.group(1).strip()
        if name and name not in ('.', '..', '/'):
            return name
    return ''

def get_git_repo_name_from_dir(d):
    if not d or not os.path.isdir(d): return ''
    cfg = os.path.join(d, '.git', 'config')
    if os.path.isfile(cfg):
        try:
            with open(cfg, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                m = re.search(r'url\s*=\s*.*[:/]([^/:]+?)(?:\.git)?$', content, re.M)
                if m:
                    name = m.group(1).strip()
                    if name and name not in ('.', '..', '/'):
                        return name
        except Exception: pass
    return ''

def is_generic_name(name):
    if not name: return True
    n = str(name).strip().lower()
    return n in ('workspace', 'codex-workspace', 'codebuddy-workspace', 'default', 'projects', 'scratch')

def is_generic_path(p):
    if not p: return True
    norm = os.path.normpath(str(p)).rstrip('/\\')
    base = os.path.basename(norm).lower()
    if base in ('workspace', 'projects', 'scratch', 'documents', 'desktop', 'downloads', 'home', ''):
        return True
    parts = [x for x in norm.split(os.sep) if x]
    if len(parts) <= 2:
        return True
    return False

def extract_project_from_slug(slug):
    if not slug: return ''
    clean = str(slug).strip('-')
    patterns = [
        r'^(?:Users|home)-[^-]+-workspace-(?:projects|scratch|references)-',
        r'^(?:Users|home)-[^-]+-(?:projects|scratch|references)-',
        r'^(?:Users|home)-[^-]+-workspace-',
        r'^(?:Users|home)-[^-]+-',
        r'^(?:workspace|repos|repositories)-(?:projects|scratch|references)-',
        r'^(?:workspace|repos|repositories)-',
        r'^(?:projects|scratch|references)-',
    ]
    candidate = clean
    for pat in patterns:
        m = re.sub(pat, '', candidate)
        if m and m != candidate:
            candidate = m
            break
    candidate = re.sub(r'^(?:projects|scratch|references)-', '', candidate)
    return candidate or clean

def detect_codex_project_from_rollout(file_p, base_cwd):
    if not file_p: return '', ''
    target = file_p
    if not os.path.isfile(target):
        if os.path.isfile(target + '.zst'):
            target = target + '.zst'
    if not os.path.isfile(target):
        return '', ''

    lines = []
    if target.endswith('.zst'):
        try:
            import subprocess
            proc = subprocess.Popen(['zstd', '-dc', target], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            for _ in range(80):
                l = proc.stdout.readline().decode('utf-8', 'ignore')
                if not l: break
                lines.append(l)
            proc.terminate()
        except Exception: pass
    else:
        try:
            with open(target, 'r', encoding='utf-8', errors='ignore') as rf:
                for _ in range(80):
                    l = rf.readline()
                    if not l: break
                    lines.append(l)
        except Exception: pass

    for l in lines:
        try:
            rm = json.loads(l)
            pld = rm.get('payload', {}) if isinstance(rm.get('payload'), dict) else rm
            rgit = pld.get('gitOriginUrl') or pld.get('git_origin_url') or rm.get('gitOriginUrl') or rm.get('git_origin_url')
            if rgit:
                g = extract_git_repo_name(rgit)
                if g and not is_generic_name(g):
                    return g, (base_cwd or '')
            rcwd = pld.get('cwd') or pld.get('workingDirectory') or rm.get('cwd')
            if rcwd and not is_generic_path(rcwd):
                g = extract_git_repo_name(get_git_repo_name_from_dir(rcwd)) or os.path.basename(os.path.normpath(rcwd))
                if g and not is_generic_name(g):
                    return g, rcwd
            if isinstance(pld, dict) and pld.get('cwd') and not is_generic_path(pld.get('cwd')):
                tc_cwd = pld.get('cwd')
                g = extract_git_repo_name(get_git_repo_name_from_dir(tc_cwd)) or os.path.basename(os.path.normpath(tc_cwd))
                if g and not is_generic_name(g):
                    return g, tc_cwd
            text_to_search = ''
            if pld.get('type') in ('function_call', 'custom_tool_call'):
                text_to_search += str(pld.get('arguments') or '') + ' ' + str(pld.get('input') or '')
            m_paths = re.findall(r'(?:Add File|Update File|\*\*\* File|workdir[\"\']?\s*:\s*[\"\'])\s*:?\s*([^\s,\"\']+)', text_to_search)
            for cand_path in m_paths:
                if not is_generic_path(cand_path):
                    cand_dir = cand_path if os.path.isdir(cand_path) else os.path.dirname(cand_path)
                    while cand_dir and not is_generic_path(cand_dir):
                        g = extract_git_repo_name(get_git_repo_name_from_dir(cand_dir))
                        if g: return g, cand_dir
                        base_b = os.path.basename(os.path.normpath(cand_dir))
                        if not is_generic_name(base_b):
                            return base_b, cand_dir
                        cand_dir = os.path.dirname(cand_dir)

            m_cmds = re.findall(r'(?:mkdir\s+(?:-p\s+)?|cd\s+[\'\"`]?)([\w\-\.]+)', text_to_search)
            for sub in m_cmds:
                if not is_generic_name(sub) and not sub.startswith('-'):
                    sub_full = os.path.join(base_cwd, sub) if base_cwd else sub
                    g = extract_git_repo_name(get_git_repo_name_from_dir(sub_full))
                    return g or sub, (sub_full if os.path.exists(sub_full) else (base_cwd or ''))

            m_ws = re.findall(r'/(?:workspace|projects|scratch)/([\w\-\.]+)', text_to_search)
            for sub in m_ws:
                if not is_generic_name(sub) and not sub.startswith('-'):
                    sub_full = os.path.join(base_cwd, sub) if base_cwd else sub
                    g = extract_git_repo_name(get_git_repo_name_from_dir(sub_full))
                    return g or sub, (sub_full if os.path.exists(sub_full) else (base_cwd or ''))
        except Exception: pass

    return '', ''

def resolve_slug(slug):
    parts = [p for p in slug.split('-') if p]
    cur = '/'
    i = 0
    while i < len(parts):
        matched = False
        for j in range(len(parts), i, -1):
            candidate = '-'.join(parts[i:j])
            test_path = os.path.join(cur, candidate)
            if os.path.exists(test_path):
                cur = test_path
                i = j
                matched = True
                break
        if not matched:
            cur = os.path.join(cur, parts[i])
            i += 1
    return cur

def collect_all():
    home = os.path.expanduser('~')
    sessions = []

    # 1. Claude Code
    claude_hist = os.path.join(home, '.claude', 'history.jsonl')
    claude_sessions = {}
    if os.path.isfile(claude_hist):
        try:
            with open(claude_hist, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if not line: continue
                    try:
                        obj = json.loads(line)
                        sid = obj.get('sessionId')
                        if not sid: continue
                        if sid not in claude_sessions:
                            proj = obj.get('project') or ''
                            proj_name = extract_git_repo_name(get_git_repo_name_from_dir(proj)) or (os.path.basename(proj) if proj else 'default')
                            claude_sessions[sid] = {
                                'session_key': sid,
                                'agent_id': 'claude',
                                'title': (obj.get('display') or '')[:150],
                                'project_path': proj,
                                'project_name': proj_name,
                                'file_path': '',
                                'updated_at': obj.get('timestamp'),
                                'message_count': 0
                            }
                        claude_sessions[sid]['message_count'] += 1
                        ts = obj.get('timestamp')
                        if ts and ts > (claude_sessions[sid]['updated_at'] or 0):
                            claude_sessions[sid]['updated_at'] = ts
                    except Exception: pass
        except Exception: pass

    # Scan Claude project files
    projects_dir = os.path.join(home, '.claude', 'projects')
    if os.path.isdir(projects_dir):
        try:
            for p in glob.glob(os.path.join(projects_dir, '*', '*.jsonl')):
                base = os.path.basename(p)
                sid = base[:-6] if base.endswith('.jsonl') else base
                parent_dir = os.path.dirname(p)
                folder_slug = os.path.basename(parent_dir)
                rec_path = resolve_slug(folder_slug)
                proj_name = extract_git_repo_name(get_git_repo_name_from_dir(rec_path)) or (os.path.basename(rec_path) if rec_path else 'default')

                if sid in claude_sessions:
                    claude_sessions[sid]['file_path'] = p
                    if claude_sessions[sid]['project_name'] == 'default' and proj_name != 'default':
                        claude_sessions[sid]['project_name'] = proj_name
                        claude_sessions[sid]['project_path'] = rec_path
                else:
                    title = f"Claude Session {sid[:8]}"
                    ts = os.path.getmtime(p)
                    msg_cnt = 0
                    try:
                        with open(p, 'r', encoding='utf-8', errors='ignore') as pf:
                            for l in pf:
                                msg_cnt += 1
                                if msg_cnt == 1:
                                    try:
                                        first_obj = json.loads(l)
                                        if first_obj.get('type') == 'user':
                                            m = first_obj.get('message') or {}
                                            c = m.get('content') or first_obj.get('content') or ''
                                            if isinstance(c, str) and c:
                                                title = c[:150]
                                            elif isinstance(c, list) and c:
                                                for cb in c:
                                                    if isinstance(cb, dict) and cb.get('text'):
                                                        title = cb['text'][:150]
                                                        break
                                        if first_obj.get('timestamp'):
                                            ts = first_obj.get('timestamp')
                                    except Exception: pass
                    except Exception: pass
                    claude_sessions[sid] = {
                        'session_key': sid,
                        'agent_id': 'claude',
                        'title': title,
                        'project_path': rec_path,
                        'project_name': proj_name,
                        'file_path': p,
                        'updated_at': ts,
                        'message_count': msg_cnt or 1
                    }
        except Exception: pass
    sessions.extend(claude_sessions.values())

    # 2. Codex CLI (Extract from state_*.sqlite, session_index.jsonl, and history.jsonl)
    codex_sessions = {}

    # 2.1 First: Query state_*.sqlite which contains full cwd, git_origin_url, rollout_path, and project tables
    sqlite_dbs = sorted(glob.glob(os.path.join(home, '.codex', 'state_*.sqlite')), reverse=True)
    if sqlite_dbs:
        try:
            conn = sqlite3.connect(sqlite_dbs[0], timeout=2.0)
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='projects'")
            has_projects = bool(cur.fetchone())

            if has_projects:
                project_map = {}
                try:
                    cur.execute("SELECT p.id, p.name, pr.path FROM projects p LEFT JOIN project_roots pr ON p.id = pr.project_id")
                    for pid, pname, prpath in cur.fetchall():
                        if pid not in project_map:
                            project_map[pid] = {'name': pname or '', 'paths': []}
                        if prpath and prpath not in project_map[pid]['paths']:
                            project_map[pid]['paths'].append(prpath)
                except Exception: pass

                cur.execute("""
                    SELECT t.id, t.cwd, t.title, t.rollout_path, t.git_origin_url, t.updated_at, t.updated_at_ms, t.first_user_message, t.is_pinned, t.project_id
                    FROM threads t
                """)
                for row in cur.fetchall():
                    sid, cwd, title, rollout_path, git_url, updated_at, updated_at_ms, first_msg, is_pinned, project_id = row
                    if not sid: continue

                    proj_name = extract_git_repo_name(git_url)
                    effective_path = cwd or ''
                    if not proj_name and project_id in project_map:
                        p_info = project_map[project_id]
                        if p_info['name'] and not is_generic_name(p_info['name']):
                            proj_name = p_info['name']
                        if not proj_name and p_info['paths']:
                            for pr_path in p_info['paths']:
                                n = extract_git_repo_name(get_git_repo_name_from_dir(pr_path)) or os.path.basename(os.path.normpath(pr_path))
                                if n and not is_generic_name(n):
                                    proj_name = n
                                    effective_path = pr_path
                                    break
                    if not proj_name and cwd:
                        proj_name = extract_git_repo_name(get_git_repo_name_from_dir(cwd))
                        if not proj_name and not is_generic_path(cwd):
                            proj_name = os.path.basename(os.path.normpath(cwd))

                    file_p = rollout_path or ''
                    if file_p and not os.path.isfile(file_p) and os.path.isfile(file_p + '.zst'):
                        file_p = file_p + '.zst'

                    if is_generic_name(proj_name) or is_generic_path(effective_path):
                        d_name, d_path = detect_codex_project_from_rollout(file_p, effective_path)
                        if d_name and not is_generic_name(d_name):
                            proj_name = d_name
                        if d_path and not is_generic_path(d_path):
                            effective_path = d_path

                    if not proj_name or is_generic_name(proj_name):
                        if effective_path and not is_generic_path(effective_path):
                            proj_name = os.path.basename(os.path.normpath(effective_path))
                        else:
                            proj_name = 'workspace'

                    ts = updated_at_ms if updated_at_ms else updated_at
                    codex_sessions[sid] = {
                        'session_key': sid,
                        'agent_id': 'codex',
                        'title': (title or first_msg or f"Codex Thread {sid[:8]}")[:150],
                        'project_path': effective_path,
                        'project_name': proj_name,
                        'file_path': file_p,
                        'updated_at': ts,
                        'message_count': 1,
                        'is_pinned': bool(is_pinned) if is_pinned is not None else False
                    }
            else:
                cur.execute("""
                    SELECT id, cwd, title, rollout_path, git_origin_url, updated_at, updated_at_ms, first_user_message, is_pinned
                    FROM threads
                """)
                for row in cur.fetchall():
                    sid, cwd, title, rollout_path, git_url, updated_at, updated_at_ms, first_msg, is_pinned = row
                    if not sid: continue

                    proj_name = extract_git_repo_name(git_url)
                    effective_path = cwd or ''
                    if not proj_name and cwd:
                        proj_name = extract_git_repo_name(get_git_repo_name_from_dir(cwd))
                        if not proj_name and not is_generic_path(cwd):
                            proj_name = os.path.basename(os.path.normpath(cwd))

                    file_p = rollout_path or ''
                    if file_p and not os.path.isfile(file_p) and os.path.isfile(file_p + '.zst'):
                        file_p = file_p + '.zst'

                    if is_generic_name(proj_name) or is_generic_path(effective_path):
                        d_name, d_path = detect_codex_project_from_rollout(file_p, effective_path)
                        if d_name and not is_generic_name(d_name):
                            proj_name = d_name
                        if d_path and not is_generic_path(d_path):
                            effective_path = d_path

                    if not proj_name or is_generic_name(proj_name):
                        if effective_path and not is_generic_path(effective_path):
                            proj_name = os.path.basename(os.path.normpath(effective_path))
                        else:
                            proj_name = 'workspace'

                    ts = updated_at_ms if updated_at_ms else updated_at
                    codex_sessions[sid] = {
                        'session_key': sid,
                        'agent_id': 'codex',
                        'title': (title or first_msg or f"Codex Thread {sid[:8]}")[:150],
                        'project_path': effective_path,
                        'project_name': proj_name,
                        'file_path': file_p,
                        'updated_at': ts,
                        'message_count': 1,
                        'is_pinned': bool(is_pinned) if is_pinned is not None else False
                    }
            conn.close()
        except Exception: pass

    # 2.2 Supplement from session_index.jsonl (if sqlite missed any)
    codex_idx = os.path.join(home, '.codex', 'session_index.jsonl')
    if os.path.isfile(codex_idx):
        try:
            with open(codex_idx, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if not line: continue
                    try:
                        item = json.loads(line)
                        sid = item.get('id')
                        if sid:
                            if sid not in codex_sessions:
                                codex_sessions[sid] = {
                                    'session_key': sid,
                                    'agent_id': 'codex',
                                    'title': (item.get('thread_name') or '')[:150],
                                    'project_path': '',
                                    'project_name': 'workspace',
                                    'file_path': '',
                                    'updated_at': item.get('updated_at'),
                                    'message_count': 1
                                }
                            elif item.get('thread_name') and not codex_sessions[sid].get('title'):
                                codex_sessions[sid]['title'] = item['thread_name'][:150]
                    except Exception: pass
        except Exception: pass

    # 2.3 Supplement message count from history.jsonl
    codex_hist = os.path.join(home, '.codex', 'history.jsonl')
    if os.path.isfile(codex_hist):
        try:
            with open(codex_hist, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if not line: continue
                    try:
                        item = json.loads(line)
                        sid = item.get('session_id')
                        if sid and sid in codex_sessions:
                            codex_sessions[sid]['message_count'] += 1
                    except Exception: pass
        except Exception: pass

    # 2.4 Locate rollout file and derive project if missing or generic
    for sid, sinfo in codex_sessions.items():
        if not sinfo['file_path'] or not os.path.isfile(sinfo['file_path']):
            candidates = glob.glob(os.path.join(home, '.codex', 'sessions', '**', f'*{sid}*'), recursive=True)
            if not candidates:
                candidates = glob.glob(os.path.join(home, '.codex', 'archived_sessions', f'*{sid}*'))
            if candidates:
                sinfo['file_path'] = candidates[0]

        if is_generic_name(sinfo['project_name']) or is_generic_path(sinfo['project_path']):
            file_p = sinfo.get('file_path')
            if file_p and os.path.isfile(file_p):
                d_name, d_path = detect_codex_project_from_rollout(file_p, sinfo.get('project_path') or '')
                if d_name and not is_generic_name(d_name):
                    sinfo['project_name'] = d_name
                if d_path and not is_generic_path(d_path):
                    sinfo['project_path'] = d_path

        if sinfo['project_name'] in ('codex-workspace', 'default', '') or is_generic_name(sinfo['project_name']):
            if sinfo['project_path'] and not is_generic_path(sinfo['project_path']):
                sinfo['project_name'] = os.path.basename(os.path.normpath(sinfo['project_path']))
            else:
                sinfo['project_name'] = 'workspace'

    sessions.extend(codex_sessions.values())

    # 3. Antigravity (AGY)
    agy_hist = os.path.join(home, '.gemini', 'antigravity-cli', 'history.jsonl')
    agy_sessions = {}
    if os.path.isfile(agy_hist):
        try:
            with open(agy_hist, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if not line: continue
                    try:
                        item = json.loads(line)
                        cid = item.get('conversationId')
                        if cid:
                            if cid not in agy_sessions:
                                ws = item.get('workspace') or ''
                                brain_log = os.path.join(home, '.gemini', 'antigravity-cli', 'brain', cid, '.system_generated', 'logs', 'transcript.jsonl')
                                proj_name = get_git_repo_name_from_dir(ws) or (os.path.basename(ws) if ws else 'workspace')
                                agy_sessions[cid] = {
                                    'session_key': cid,
                                    'agent_id': 'agy',
                                    'title': (item.get('display') or '')[:150],
                                    'project_path': ws,
                                    'project_name': proj_name,
                                    'file_path': brain_log if os.path.isfile(brain_log) else '',
                                    'updated_at': item.get('timestamp'),
                                    'message_count': 0
                                }
                            agy_sessions[cid]['message_count'] += 1
                            ts = item.get('timestamp')
                            if ts and ts > (agy_sessions[cid]['updated_at'] or 0):
                                agy_sessions[cid]['updated_at'] = ts
                    except Exception: pass
        except Exception: pass
    sessions.extend(agy_sessions.values())

    # 4. OpenCode (SQLite: ~/.local/share/opencode/opencode.db)
    opencode_db = os.path.join(home, '.local', 'share', 'opencode', 'opencode.db')
    if os.path.isfile(opencode_db):
        try:
            conn = sqlite3.connect(opencode_db, timeout=2.0)
            cur = conn.cursor()
            cur.execute("""
                SELECT s.id, s.title, s.directory, s.time_updated, s.tokens_input + s.tokens_output, count(m.id), p.name, p.worktree
                FROM session s
                LEFT JOIN message m ON s.id=m.session_id
                LEFT JOIN project p ON s.project_id=p.id
                GROUP BY s.id
                ORDER BY s.time_updated DESC
                LIMIT 150
            """)
            for row in cur.fetchall():
                sid, title, directory, time_updated, tokens, msg_cnt, p_name, p_worktree = row
                effective_dir = directory or p_worktree or ''
                pname = extract_git_repo_name(get_git_repo_name_from_dir(effective_dir))
                if not pname and p_name and not is_generic_name(p_name):
                    pname = p_name
                if not pname and p_worktree and not is_generic_path(p_worktree):
                    pname = extract_git_repo_name(get_git_repo_name_from_dir(p_worktree)) or os.path.basename(os.path.normpath(p_worktree))
                if not pname and effective_dir and not is_generic_path(effective_dir):
                    pname = os.path.basename(os.path.normpath(effective_dir))
                if not pname or is_generic_name(pname):
                    pname = 'workspace'

                sessions.append({
                    'session_key': sid,
                    'agent_id': 'opencode',
                    'title': (title or f"OpenCode Session {sid[:8]}")[:150],
                    'project_path': effective_dir,
                    'project_name': pname,
                    'file_path': opencode_db,
                    'updated_at': time_updated,
                    'message_count': max(1, msg_cnt or 1),
                    'token_count': tokens or 0
                })
            conn.close()
        except Exception: pass

    # 5. Pi coding agent (~/.pi/agent/sessions/<project_dir>/*.jsonl)
    pi_sessions_dir = os.path.join(home, '.pi', 'agent', 'sessions')
    if os.path.isdir(pi_sessions_dir):
        try:
            for p in sorted(glob.glob(os.path.join(pi_sessions_dir, '*', '*.jsonl')), key=os.path.getmtime, reverse=True)[:150]:
                folder_name = os.path.basename(os.path.dirname(p))
                fname = os.path.basename(p)
                sid = fname[:-6]
                if '_' in sid:
                    sid = sid.split('_', 1)[1]
                mtime = int(os.path.getmtime(p) * 1000)

                clean_folder = folder_name.strip('-')
                proj_path = '/' + clean_folder.replace('-', '/')
                proj_name = extract_git_repo_name(get_git_repo_name_from_dir(proj_path))
                if not proj_name or is_generic_name(proj_name):
                    proj_name = extract_project_from_slug(folder_name)
                if not proj_name or is_generic_name(proj_name):
                    proj_name = 'workspace'

                title = ''
                count = 0
                try:
                    with open(p, 'r', encoding='utf-8', errors='ignore') as fp:
                        for line in fp:
                            line = line.strip()
                            if not line: continue
                            count += 1
                            if not title:
                                try:
                                    obj = json.loads(line)
                                    msg = obj.get('message') or {}
                                    c = msg.get('content')
                                    if isinstance(c, list):
                                        for b in c:
                                            if isinstance(b, dict) and b.get('type') == 'text':
                                                title = b.get('text', '')[:150]
                                                break
                                    elif isinstance(c, str):
                                        title = c[:150]
                                except Exception: pass
                except Exception: pass

                sessions.append({
                    'session_key': sid,
                    'agent_id': 'pi',
                    'title': title or f"Pi Session {sid[:8]}",
                    'project_path': proj_path,
                    'project_name': proj_name,
                    'file_path': p,
                    'updated_at': mtime,
                    'message_count': count or 1
                })
        except Exception: pass

    # 6. Hermes Agent (SQLite: ~/.hermes/state.db)
    hermes_db = os.path.join(home, '.hermes', 'state.db')
    if os.path.isfile(hermes_db):
        try:
            conn = sqlite3.connect(hermes_db, timeout=2.0)
            cur = conn.cursor()
            cur.execute("""
                SELECT id, title, cwd, git_repo_root, last_activity_at, started_at, message_count, pinned, session_key
                FROM sessions
                ORDER BY coalesce(last_activity_at, started_at) DESC
                LIMIT 300
            """)
            for row in cur.fetchall():
                sid, title, cwd, git_root, last_act, started_at, msg_count, pinned, skey = row
                skey = skey or sid
                if not skey: continue

                proj_path = cwd or git_root or ''
                proj_name = ''
                if git_root:
                    proj_name = extract_git_repo_name(get_git_repo_name_from_dir(git_root))
                if not proj_name and cwd:
                    proj_name = extract_git_repo_name(get_git_repo_name_from_dir(cwd)) or os.path.basename(os.path.normpath(cwd))
                if not proj_name:
                    proj_name = 'hermes-workspace'

                ts = last_act or started_at

                sessions.append({
                    'session_key': skey,
                    'agent_id': 'hermes',
                    'title': (title or f"Hermes Session {skey[:8]}")[:150],
                    'project_path': proj_path,
                    'project_name': proj_name,
                    'file_path': hermes_db,
                    'updated_at': ts,
                    'message_count': msg_count or 1,
                    'is_pinned': bool(pinned) if pinned else False
                })
            conn.close()
        except Exception: pass

    # 7. CodeBuddy (Extract cwd from session jsonl and deduce from project slug)
    cb_dir = os.path.join(home, '.codebuddy', 'projects')
    if os.path.isdir(cb_dir):
        try:
            cb_files = sorted(glob.glob(os.path.join(cb_dir, '*', '*.jsonl')), key=os.path.getmtime, reverse=True)[:150]
            for p in cb_files:
                sid = os.path.basename(p)[:-6]
                mtime = int(os.path.getmtime(p) * 1000)
                folder_name = os.path.basename(os.path.dirname(p))
                title = ''
                cwd = ''
                count = 0
                first_real_prompt = ''
                first_command = ''
                try:
                    with open(p, 'r', encoding='utf-8', errors='ignore') as fp:
                        for line_idx, line in enumerate(fp):
                            line = line.strip()
                            if not line: continue
                            count += 1
                            if line_idx < 30:
                                try:
                                    obj = json.loads(line)
                                    if not cwd:
                                        cwd = obj.get('cwd') or obj.get('project') or obj.get('workingDirectory') or ''
                                    if obj.get('role') == 'user' or obj.get('type') in ('message', 'user'):
                                        c = obj.get('content')
                                        txt = ''
                                        if isinstance(c, list):
                                            for b in c:
                                                if isinstance(b, dict) and b.get('type') in ('input_text', 'text'):
                                                    txt = b.get('text', '')
                                                    break
                                        elif isinstance(c, str):
                                            txt = c
                                        if txt and not txt.startswith('<system-reminder') and not txt.startswith('<local-command'):
                                            if '<command-name>' in txt:
                                                if not first_command:
                                                    m_c = re.search(r'<command-name>\s*(.*?)\s*</command-name>', txt)
                                                    if m_c: first_command = m_c.group(1).strip()
                                            elif not first_real_prompt:
                                                first_real_prompt = txt[:150]
                                except Exception: pass
                except Exception: pass

                title = first_real_prompt or (f"⚡ {first_command}" if first_command else f"CodeBuddy Session {sid[:8]}")

                # Deduce project_name and project_path using resolve_slug
                proj_path = cwd
                proj_name = ''

                if cwd:
                    proj_name = extract_git_repo_name(get_git_repo_name_from_dir(cwd))
                    if not proj_name and not is_generic_path(cwd):
                        proj_name = os.path.basename(os.path.normpath(cwd))

                if not proj_name and folder_name:
                    rec_path = resolve_slug(folder_name)
                    if os.path.exists(rec_path):
                        proj_path = rec_path
                        proj_name = extract_git_repo_name(get_git_repo_name_from_dir(rec_path))
                        if not proj_name and not is_generic_path(rec_path):
                            proj_name = os.path.basename(os.path.normpath(rec_path))

                    if not proj_name or is_generic_name(proj_name):
                        proj_name = extract_project_from_slug(folder_name)

                if not proj_name or is_generic_name(proj_name):
                    proj_name = 'workspace'

                sessions.append({
                    'session_key': sid,
                    'agent_id': 'codebuddy',
                    'title': title,
                    'project_path': proj_path or '',
                    'project_name': proj_name,
                    'file_path': p,
                    'updated_at': mtime,
                    'message_count': count or 1
                })
        except Exception: pass

    # 8. Qoder
    qoder_dir = os.path.join(home, '.qoder', 'projects')
    if os.path.isdir(qoder_dir):
        try:
            q_files = sorted(glob.glob(os.path.join(qoder_dir, '*', '*.jsonl')), key=os.path.getmtime, reverse=True)[:50]
            for p in q_files:
                sid = os.path.basename(p)[:-6]
                mtime = int(os.path.getmtime(p) * 1000)
                folder_name = os.path.basename(os.path.dirname(p))
                title = ''
                cwd = ''
                count = 0
                first_real_prompt = ''
                first_command = ''
                try:
                    with open(p, 'r', encoding='utf-8', errors='ignore') as fp:
                        for line_idx, line in enumerate(fp):
                            line = line.strip()
                            if not line: continue
                            count += 1
                            if line_idx < 30:
                                try:
                                    obj = json.loads(line)
                                    if not cwd and obj.get('cwd'):
                                        cwd = obj.get('cwd')
                                    if not sid and obj.get('sessionId'):
                                        sid = obj.get('sessionId')
                                    if obj.get('type') == 'user':
                                        msg = obj.get('message') or {}
                                        c = msg.get('content') or obj.get('content') or ''
                                        if isinstance(c, str) and not c.startswith('<local-command') and not c.startswith('<system-reminder'):
                                            if '<command-name>' in c:
                                                if not first_command:
                                                    m_c = re.search(r'<command-name>\s*(.*?)\s*</command-name>', c)
                                                    if m_c: first_command = m_c.group(1).strip()
                                            elif not first_real_prompt:
                                                first_real_prompt = c[:150]
                                except Exception: pass
                except Exception: pass

                title = first_real_prompt or (f"⚡ {first_command}" if first_command else f"Qoder Session {sid[:8]}")

                pname = ''
                if cwd:
                    pname = extract_git_repo_name(get_git_repo_name_from_dir(cwd))
                    if not pname and not is_generic_path(cwd):
                        pname = os.path.basename(os.path.normpath(cwd))
                if not pname and folder_name:
                    rec_path = resolve_slug(folder_name.strip('-'))
                    if os.path.exists(rec_path):
                        cwd = rec_path
                        pname = extract_git_repo_name(get_git_repo_name_from_dir(rec_path))
                        if not pname and not is_generic_path(rec_path):
                            pname = os.path.basename(os.path.normpath(rec_path))
                    if not pname or is_generic_name(pname):
                        pname = extract_project_from_slug(folder_name)
                if not pname or is_generic_name(pname):
                    pname = 'workspace'

                sessions.append({
                    'session_key': sid,
                    'agent_id': 'qoder',
                    'title': title,
                    'project_path': cwd,
                    'project_name': pname,
                    'file_path': p,
                    'updated_at': mtime,
                    'message_count': count or 1
                })
        except Exception: pass

    print("___SESSIONS_JSON___" + json.dumps(sessions, ensure_ascii=False))

if __name__ == '__main__':
    collect_all()
'''

# --------------------------------------------------------------------------
# PowerShell collector for Windows
# --------------------------------------------------------------------------

COLLECTOR_SCRIPT_WIN = r'''
$ErrorActionPreference = "SilentlyContinue"
$sessions = @()
$homeDir = $HOME

# Claude
$claudeHist = Join-Path $homeDir ".claude\history.jsonl"
if (Test-Path $claudeHist) {
    $claudeMap = @{}
    Get-Content $claudeHist -Tail 300 | ForEach-Object {
        try {
            $obj = $_ | ConvertFrom-Json
            if ($obj.sessionId) {
                $sid = $obj.sessionId
                if (-not $claudeMap.ContainsKey($sid)) {
                    $proj = if ($obj.project) { $obj.project } else { "" }
                    $projName = if ($proj) { Split-Path $proj -Leaf } else { "default" }
                    $claudeMap[$sid] = @{
                        session_key = $sid
                        agent_id = "claude"
                        title = $obj.display
                        project_path = $proj
                        project_name = $projName
                        file_path = ""
                        updated_at = $obj.timestamp
                        message_count = 0
                    }
                }
                $claudeMap[$sid].message_count += 1
                if ($obj.timestamp -and ($obj.timestamp -gt $claudeMap[$sid].updated_at)) {
                    $claudeMap[$sid].updated_at = $obj.timestamp
                }
            }
        } catch {}
    }
    $sessions += $claudeMap.Values
}

# Codex
$codexIdx = Join-Path $homeDir ".codex\session_index.jsonl"
if (Test-Path $codexIdx) {
    $codexMap = @{}
    Get-Content $codexIdx -Tail 100 | ForEach-Object {
        try {
            $obj = $_ | ConvertFrom-Json
            if ($obj.id) {
                $sid = $obj.id
                $projPath = if ($obj.cwd) { $obj.cwd } else { "" }
                $projName = if ($projPath) { Split-Path $projPath -Leaf } else { "workspace" }
                if ($projName -eq "codex-workspace" -or -not $projName) { $projName = "workspace" }
                $codexMap[$sid] = @{
                    session_key = $sid
                    agent_id = "codex"
                    title = $obj.thread_name
                    project_path = $projPath
                    project_name = $projName
                    file_path = ""
                    updated_at = $obj.updated_at
                    message_count = 1
                }
            }
        } catch {}
    }
    $sessions += $codexMap.Values
}

$json = ConvertTo-Json -InputObject $sessions -Compress -Depth 4
Write-Output ("___SESSIONS_JSON___" + $json)
'''


# --------------------------------------------------------------------------
# Sensitive data masking
# --------------------------------------------------------------------------

SECRET_PATTERNS = [
    (re.compile(r"sk-[a-zA-Z0-9_\-]{20,}"), "sk-***MASKED***"),
    (re.compile(r"ghp_[a-zA-Z0-9]{20,}"), "ghp_***MASKED***"),
    (re.compile(r"xox[baprs]-[a-zA-Z0-9_\-]{10,}"), "xox-***MASKED***"),
    (re.compile(r"(bearer\s+)[a-zA-Z0-9_\-\.]{20,}", re.IGNORECASE), r"\1***MASKED***"),
    (
        re.compile(
            r'((?:api[_-]?key|secret|password|auth[_-]?token)["\']?\s*[:=]\s*["\']?)[^"\'\s,;]{8,}',
            re.IGNORECASE,
        ),
        r"\1***MASKED***",
    ),
]


def mask_sensitive_text(text: str) -> tuple[str, int]:
    """Mask credentials and tokens from transcript text. Returns (masked_text, count)."""
    if not text:
        return text, 0
    count = 0
    masked = text
    for pattern, repl in SECRET_PATTERNS:
        matches = len(pattern.findall(masked))
        if matches:
            count += matches
            masked = pattern.sub(repl, masked)
    return masked, count


def clean_transcript_text(text: str) -> str:
    """Strip XML wrappers and internal agent artifacts into clean human-readable conversation text."""
    if not text:
        return ""
    from agent_fleet.services.xml_deconstruct import (
        extract_thinking_from_text,
        strip_agent_harness_wrappers,
    )
    t, _ = extract_thinking_from_text(str(text))
    return strip_agent_harness_wrappers(t)


# --------------------------------------------------------------------------
# Timestamp Normalization
# --------------------------------------------------------------------------


_datetime_adapter = TypeAdapter(datetime)


def parse_timestamp(val: Any) -> datetime | None:
    """Parse timestamps with arbitrary sub-second precision, Epoch ms/s, or ASP.NET formats.

    Uses Pydantic's Rust-backed speedate engine to cleanly handle nanosecond/tick
    precision without manual regex truncation.
    """
    if val is None:
        return None
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return None
        # Handle ASP.NET /Date(1234567890000)/ from PowerShell ConvertTo-Json
        m_asp = re.search(r"/Date\((\d+)\)/", val)
        if m_asp:
            val = int(m_asp.group(1))

    try:
        dt = _datetime_adapter.validate_python(val)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None


# --------------------------------------------------------------------------
# Session Discovery & Sync
# --------------------------------------------------------------------------


async def scan_sessions_for_runtime(rt_ref, runner, settings, db_session) -> int:
    """Run session discovery on one runtime and upsert results into the database."""
    # Skip Windows runtimes for session indexing per user request
    if rt_ref.kind == "windows":
        logger.info(f"Skipping session collection on Windows runtime: {rt_ref.target}")
        return 0

    script = f"""if command -v python3 >/dev/null 2>&1; then
python3 - << 'PY_EOF'
{COLLECTOR_SCRIPT_PY}
PY_EOF
fi
"""

    timeout = (settings.ssh_connect_timeout if settings else 10) + 15
    result = await runner.run_script(script, timeout=timeout)
    if not result.ok:
        logger.warning(
            f"Failed to scan sessions on {rt_ref.target}: {result.failure_reason()}"
        )
        return 0

    stdout = result.stdout or ""
    marker = "___SESSIONS_JSON___"
    idx = stdout.find(marker)
    if idx == -1:
        return 0

    json_str = stdout[idx + len(marker) :].strip().splitlines()[0]
    try:
        raw_sessions = json.loads(json_str)
    except Exception as e:
        logger.error(f"Error parsing sessions JSON from {rt_ref.target}: {e}")
        return 0

    if not isinstance(raw_sessions, list):
        return 0

    now = datetime.utcnow()
    upserted_count = 0

    for item in raw_sessions:
        agent_id = str(item.get("agent_id") or "unknown")
        session_key = str(item.get("session_key") or "")
        if not session_key:
            continue

        session_id = f"{rt_ref.id}:{agent_id}:{session_key}"
        title = (item.get("title") or "Untitled Session").strip()
        project_path = item.get("project_path") or None
        project_name = item.get("project_name") or (
            os.path.basename(project_path) if project_path else None
        )
        file_path = item.get("file_path") or None
        updated_at = parse_timestamp(item.get("updated_at"))
        message_count = int(item.get("message_count") or 1)
        token_count = item.get("token_count")

        existing = db_session.get(models.Session, session_id)
        if existing:
            if title and (not existing.title or existing.title == "Untitled Session"):
                existing.title = title
            if file_path:
                existing.file_path = file_path
            if project_path:
                existing.project_path = project_path
            if project_name:
                existing.project_name = project_name
            if updated_at is not None:
                existing.updated_at = updated_at
            existing.message_count = max(existing.message_count, message_count)
            if token_count:
                existing.token_count = token_count
            existing.last_synced_at = now
        else:
            new_s = models.Session(
                id=session_id,
                runtime_id=rt_ref.id,
                agent_id=agent_id,
                session_key=session_key,
                title=title,
                project_path=project_path,
                project_name=project_name,
                file_path=file_path,
                updated_at=updated_at,
                message_count=message_count,
                token_count=token_count,
                created_at=updated_at or now,
                last_synced_at=now,
            )
            db_session.add(new_s)

        upserted_count += 1

    db_session.flush()
    return upserted_count


async def sync_all_sessions(settings=None) -> dict[str, Any]:
    """Scan and synchronize sessions across all enabled runtimes."""
    settings = settings or get_settings()
    total_found = 0
    errors = []

    with session_scope() as s:
        # Prune any sessions from Windows runtimes per user request
        win_rt_ids = s.execute(
            select(models.Runtime.id).where(models.Runtime.kind == "windows")
        ).scalars().all()
        if win_rt_ids:
            s.execute(
                delete(models.Session).where(models.Session.runtime_id.in_(win_rt_ids))
            )

        runtimes = (
            s.execute(
                select(models.Runtime)
                .join(models.Host)
                .where(
                    models.Host.enabled.is_(True),
                    models.Runtime.enabled.is_(True),
                    models.Runtime.kind != "windows",
                )
            )
            .scalars()
            .all()
        )
        refs = [to_ref(rt) for rt in runtimes]

    for ref in refs:
        runner = build_runner(ref, settings)
        try:
            with session_scope() as s:
                count = await scan_sessions_for_runtime(ref, runner, settings, s)
                total_found += count
        except Exception as exc:
            errors.append(f"{ref.target}: {exc}")

    return {
        "ok": True,
        "total_sessions": total_found,
        "runtimes_scanned": len(refs),
        "errors": errors,
    }


# --------------------------------------------------------------------------
# Session Queries & Listing
# --------------------------------------------------------------------------


def list_sessions(
    db_session,
    q: str | None = None,
    runtime_id: str | None = None,
    host_name: str | None = None,
    agent_id: str | None = None,
    project: str | None = None,
    pinned_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Search and filter sessions with pagination and facets."""
    query = (
        select(models.Session)
        .join(models.Runtime)
        .join(models.Host)
        .options(
            joinedload(models.Session.runtime).joinedload(models.Runtime.host),
            joinedload(models.Session.transcript),
        )
    )

    if runtime_id:
        query = query.where(models.Session.runtime_id == runtime_id)
    if host_name:
        query = query.where(models.Host.name == host_name)
    if agent_id:
        query = query.where(models.Session.agent_id == agent_id)
    if project:
        query = query.where(
            or_(
                models.Session.project_name == project,
                models.Session.project_path.ilike(f"%{project}%"),
            )
        )
    if pinned_only:
        query = query.where(models.Session.is_pinned.is_(True))

    if q and q.strip():
        term = f"%{q.strip()}%"
        query = query.where(
            or_(
                models.Session.title.ilike(term),
                models.Session.project_name.ilike(term),
                models.Session.project_path.ilike(term),
                models.Session.summary.ilike(term),
                models.Session.agent_id.ilike(term),
            )
        )

    # Count total matching
    count_query = select(func.count()).select_from(query.subquery())
    total_count = db_session.execute(count_query).scalar_one()

    # Order by pinned first, then updated_at desc
    ordered_query = (
        query.order_by(
            models.Session.is_pinned.desc(),
            models.Session.updated_at.desc().nullslast(),
            models.Session.created_at.desc(),
        )
        .offset(offset)
        .limit(limit)
    )

    items = db_session.execute(ordered_query).unique().scalars().all()

    # Build facets for easy UI dropdown filtering
    agents = [
        r[0]
        for r in db_session.execute(
            select(models.Session.agent_id).distinct().order_by(models.Session.agent_id)
        ).all()
        if r[0]
    ]

    hosts = [
        r[0]
        for r in db_session.execute(
            select(models.Host.name).distinct().order_by(models.Host.name)
        ).all()
        if r[0]
    ]

    projects = [
        r[0]
        for r in db_session.execute(
            select(models.Session.project_name)
            .distinct()
            .where(models.Session.project_name.isnot(None))
            .order_by(models.Session.project_name)
        ).all()
        if r[0]
    ]

    serialized = []
    for s in items:
        h_name = s.runtime.host.name if s.runtime and s.runtime.host else "unknown"
        transport = s.runtime.transport if s.runtime else "ssh"
        serialized.append(
            {
                "id": s.id,
                "runtime_id": s.runtime_id,
                "host_name": h_name,
                "agent_id": s.agent_id,
                "session_key": s.session_key,
                "title": s.title or "Untitled Session",
                "project_path": s.project_path,
                "project_name": s.project_name or "workspace",
                "file_path": s.file_path,
                "started_at": models.iso_utc(s.started_at),
                "updated_at": models.iso_utc(s.updated_at),
                "message_count": s.message_count,
                "token_count": s.token_count,
                "is_pinned": s.is_pinned,
                "summary": s.summary,
                "resume_command": generate_resume_command(s, h_name, transport),
                "has_cached_transcript": s.transcript is not None,
            }
        )

    return {
        "items": serialized,
        "total": total_count,
        "facets": {
            "agents": agents,
            "hosts": hosts,
            "projects": projects,
        },
    }


def toggle_pin_session(db_session, session_id: str) -> bool:
    """Toggle is_pinned state on a session."""
    s = db_session.get(models.Session, session_id)
    if not s:
        raise ValueError(f"Session '{session_id}' not found")
    s.is_pinned = not s.is_pinned
    db_session.flush()
    return s.is_pinned


# --------------------------------------------------------------------------
# Resume Command Generation
# --------------------------------------------------------------------------


def generate_resume_command(
    session: models.Session, host_name: str, transport: str
) -> str:
    """Generate the exact one-liner command to resume this session in a terminal."""
    agent = session.agent_id
    sid = session.session_key
    proj_path = session.project_path
    dir_part = f"cd '{proj_path}' && " if proj_path else ""

    if agent == "claude":
        cmd = f"{dir_part}claude --resume {sid}"
    elif agent == "codex":
        cmd = f"{dir_part}codex resume {sid}"
    elif agent == "agy":
        cmd = f"{dir_part}agy --resume {sid}"
    elif agent == "opencode":
        cmd = f"{dir_part}opencode --session {sid}"
    elif agent == "pi":
        if session.file_path and not session.file_path.endswith((".db", ".sqlite")):
            cmd = f'{dir_part}pi --session "{session.file_path}"'
        else:
            cmd = f"{dir_part}pi --resume {sid}"
    elif agent == "codebuddy":
        cmd = f"{dir_part}codebuddy --resume {sid}"
    elif agent == "hermes":
        cmd = f"{dir_part}hermes --resume {sid}"
    elif agent == "qoder":
        cmd = f"{dir_part}qoder --resume {sid}"
    else:
        cmd = f"{dir_part}{agent} --resume {sid}"

    if transport == "local" or host_name in ("macbook-air", "local-controller", "localhost"):
        return cmd
    return f'ssh -t {host_name} "{cmd}"'


def extract_git_repo_name(url: str | None) -> str:
    """Extract clean git repository name from remote URL."""
    if not url:
        return ""
    m = re.search(r"[:/]([^/:]+?)(?:\.git)?$", str(url).strip())
    if m:
        name = m.group(1).strip()
        if name and name not in (".", "..", "/"):
            return name
    return ""


def get_git_repo_name_from_dir(d: str | None) -> str:
    """Read git origin URL from directory's .git/config and extract repo name."""
    if not d or not os.path.isdir(d):
        return ""
    cfg = os.path.join(d, ".git", "config")
    if os.path.isfile(cfg):
        try:
            with open(cfg, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                m = re.search(r"url\s*=\s*.*[:/]([^/:]+?)(?:\.git)?$", content, re.M)
                if m:
                    name = m.group(1).strip()
                    if name and name not in (".", "..", "/"):
                        return name
        except Exception:
            pass
    return ""


def is_generic_name(name: str | None) -> bool:
    """Check if project name is a generic placeholder."""
    if not name:
        return True
    n = str(name).strip().lower()
    return n in (
        "workspace",
        "codex-workspace",
        "codebuddy-workspace",
        "default",
        "projects",
        "scratch",
    )


def is_generic_path(p: str | None) -> bool:
    """Check if path is a generic root or workspace folder."""
    if not p:
        return True
    norm = os.path.normpath(str(p)).rstrip("/\\")
    base = os.path.basename(norm).lower()
    if base in (
        "workspace",
        "projects",
        "scratch",
        "documents",
        "desktop",
        "downloads",
        "home",
        "",
    ):
        return True
    parts = [x for x in norm.split(os.sep) if x]
    if len(parts) <= 2:
        return True
    return False


def extract_project_from_slug(slug: str | None) -> str:
    """Extract clean project repo slug from hierarchical or hyphenated folder names."""
    if not slug:
        return ""
    clean = str(slug).strip("-")
    patterns = [
        r"^(?:Users|home)-[^-]+-workspace-(?:projects|scratch|references)-",
        r"^(?:Users|home)-[^-]+-(?:projects|scratch|references)-",
        r"^(?:Users|home)-[^-]+-workspace-",
        r"^(?:Users|home)-[^-]+-",
        r"^(?:workspace|repos|repositories)-(?:projects|scratch|references)-",
        r"^(?:workspace|repos|repositories)-",
        r"^(?:projects|scratch|references)-",
    ]
    candidate = clean
    for pat in patterns:
        m = re.sub(pat, "", candidate)
        if m and m != candidate:
            candidate = m
            break
    candidate = re.sub(r"^(?:projects|scratch|references)-", "", candidate)
    return candidate or clean


def detect_codex_project_from_rollout(
    file_p: str | None, base_cwd: str = ""
) -> tuple[str, str]:
    """Inspect rollout file for payload.cwd, payload.gitOriginUrl, or subfolders."""
    if not file_p:
        return "", ""
    target = file_p
    if not os.path.isfile(target):
        if os.path.isfile(target + ".zst"):
            target = target + ".zst"
    if not os.path.isfile(target):
        return "", ""

    lines: list[str] = []
    if target.endswith(".zst"):
        try:
            import subprocess

            proc = subprocess.Popen(
                ["zstd", "-dc", target],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            for _ in range(80):
                l = proc.stdout.readline().decode("utf-8", "ignore")
                if not l:
                    break
                lines.append(l)
            proc.terminate()
        except Exception:
            pass
    else:
        try:
            with open(target, "r", encoding="utf-8", errors="ignore") as rf:
                for _ in range(80):
                    l = rf.readline()
                    if not l:
                        break
                    lines.append(l)
        except Exception:
            pass

    for l in lines:
        try:
            rm = json.loads(l)
            pld = rm.get("payload", {}) if isinstance(rm.get("payload"), dict) else rm
            rgit = (
                pld.get("gitOriginUrl")
                or pld.get("git_origin_url")
                or rm.get("gitOriginUrl")
                or rm.get("git_origin_url")
            )
            if rgit:
                g = extract_git_repo_name(rgit)
                if g and not is_generic_name(g):
                    return g, (base_cwd or "")
            rcwd = pld.get("cwd") or pld.get("workingDirectory") or rm.get("cwd")
            if rcwd and not is_generic_path(rcwd):
                g = extract_git_repo_name(get_git_repo_name_from_dir(rcwd)) or os.path.basename(os.path.normpath(rcwd))
                if g and not is_generic_name(g):
                    return g, rcwd
            if isinstance(pld, dict) and pld.get("cwd") and not is_generic_path(pld.get("cwd")):
                tc_cwd = pld.get("cwd")
                g = extract_git_repo_name(get_git_repo_name_from_dir(tc_cwd)) or os.path.basename(os.path.normpath(tc_cwd))
                if g and not is_generic_name(g):
                    return g, tc_cwd
            text_to_search = ""
            if pld.get("type") in ("function_call", "custom_tool_call"):
                text_to_search += str(pld.get("arguments") or "") + " " + str(pld.get("input") or "")
            m_paths = re.findall(
                r'(?:Add File|Update File|\*\*\* File|workdir[\"\']?\s*:\s*[\"\'])\s*:?\s*([^\s,\"\']+)',
                text_to_search,
            )
            for cand_path in m_paths:
                if not is_generic_path(cand_path):
                    cand_dir = cand_path if os.path.isdir(cand_path) else os.path.dirname(cand_path)
                    while cand_dir and not is_generic_path(cand_dir):
                        g = extract_git_repo_name(get_git_repo_name_from_dir(cand_dir))
                        if g:
                            return g, cand_dir
                        base_b = os.path.basename(os.path.normpath(cand_dir))
                        if not is_generic_name(base_b):
                            return base_b, cand_dir
                        cand_dir = os.path.dirname(cand_dir)

            m_cmds = re.findall(
                r'(?:mkdir\s+(?:-p\s+)?|cd\s+[\'\"`]?)([\w\-\.]+)', text_to_search
            )
            for sub in m_cmds:
                if not is_generic_name(sub) and not sub.startswith("-"):
                    sub_full = os.path.join(base_cwd, sub) if base_cwd else sub
                    g = extract_git_repo_name(get_git_repo_name_from_dir(sub_full))
                    return g or sub, (sub_full if os.path.exists(sub_full) else (base_cwd or ""))

            m_ws = re.findall(
                r"/(?:workspace|projects|scratch)/([\w\-\.]+)", text_to_search
            )
            for sub in m_ws:
                if not is_generic_name(sub) and not sub.startswith("-"):
                    sub_full = os.path.join(base_cwd, sub) if base_cwd else sub
                    g = extract_git_repo_name(get_git_repo_name_from_dir(sub_full))
                    return g or sub, (sub_full if os.path.exists(sub_full) else (base_cwd or ""))
        except Exception:
            pass

    return "", ""


def aggregate_opencode_parts(
    rows: list[tuple[Any, ...]]
) -> list[dict[str, Any]]:
    """Group OpenCode message and part rows by message ID into structured conversation turns."""
    msgs: dict[str, dict[str, Any]] = {}
    msg_order: list[str] = []

    for row in rows:
        mid = row[0]
        m_ts = row[1] if len(row) > 1 else None
        mdata_str = row[2] if len(row) > 2 else None
        pdata_str = row[3] if len(row) > 3 else None

        if mid not in msgs:
            msg_order.append(mid)
            mdata: dict[str, Any] = {}
            if mdata_str:
                try:
                    mdata = (
                        json.loads(mdata_str)
                        if isinstance(mdata_str, str)
                        else mdata_str
                    )
                except Exception:
                    pass
            msgs[mid] = {
                "role": mdata.get("role") or "user",
                "ts": mdata.get("time", {}).get("created") or m_ts,
                "content_parts": [],
                "thinking_parts": [],
                "tool_calls": [],
            }

        if pdata_str:
            try:
                pdata = (
                    json.loads(pdata_str)
                    if isinstance(pdata_str, str)
                    else pdata_str
                )
                ptype = pdata.get("type")
                if ptype == "text" and pdata.get("text"):
                    msgs[mid]["content_parts"].append(pdata["text"])
                elif ptype == "reasoning" and pdata.get("text"):
                    msgs[mid]["thinking_parts"].append(pdata["text"])
                elif ptype == "tool":
                    state = pdata.get("state") or {}
                    t_name = pdata.get("tool") or "tool"
                    t_input = state.get("input") or {}
                    t_out = state.get("output")
                    call_dict: dict[str, Any] = {"name": t_name, "input": t_input}
                    if pdata.get("callID"):
                        call_dict["id"] = pdata.get("callID")
                    if t_out is not None:
                        call_dict["output"] = str(t_out)
                    msgs[mid]["tool_calls"].append(call_dict)
                elif ptype == "patch":
                    call_dict = {
                        "name": "patch",
                        "input": {
                            "hash": pdata.get("hash"),
                            "files": pdata.get("files") or [],
                        },
                    }
                    if pdata.get("diff"):
                        call_dict["input"]["diff"] = pdata.get("diff")
                    if pdata.get("files"):
                        call_dict["input"]["path"] = pdata["files"][0]
                    msgs[mid]["tool_calls"].append(call_dict)
            except Exception:
                pass

    turns: list[dict[str, Any]] = []
    for mid in msg_order:
        m = msgs[mid]
        role = m["role"]
        content = "\n\n".join(c for c in m["content_parts"] if c).strip()
        thinking = "\n\n".join(t for t in m["thinking_parts"] if t).strip()
        tools = m["tool_calls"]
        if role == "user":
            if content:
                turns.append(
                    {"role": "user", "content": content, "timestamp": m["ts"]}
                )
        else:
            if content or thinking or tools:
                turns.append(
                    {
                        "role": "assistant",
                        "content": content,
                        "thinking": thinking or None,
                        "tool_calls": tools or None,
                        "timestamp": m["ts"],
                    }
                )
    return turns


# --------------------------------------------------------------------------
# Transcript Retrieval & Parsing
# --------------------------------------------------------------------------


def parse_raw_transcript_lines(
    agent_id: str, lines: list[str]
) -> list[dict[str, Any]]:
    """Parse raw agent transcript lines into normalized turns."""
    turns: list[dict[str, Any]] = []

    if agent_id == "claude":
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            rtype = rec.get("type")
            msg = rec.get("message") or {}
            role = msg.get("role") or rtype

            if role in ("user", "human"):
                raw_c = msg.get("content") or rec.get("content") or ""
                content = ""
                if isinstance(raw_c, str):
                    content = raw_c
                elif isinstance(raw_c, list):
                    content = "\n".join(
                        b.get("text", "")
                        for b in raw_c
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                if content.strip():
                    turns.append(
                        {
                            "role": "user",
                            "content": content.strip(),
                            "timestamp": rec.get("timestamp"),
                        }
                    )
            elif role in ("assistant", "model"):
                raw_c = msg.get("content") or rec.get("content") or []
                content = ""
                thinking = ""
                tools = []
                if isinstance(raw_c, str):
                    content = raw_c
                elif isinstance(raw_c, list):
                    for b in raw_c:
                        if not isinstance(b, dict):
                            continue
                        btype = b.get("type")
                        if btype == "text":
                            content += b.get("text", "")
                        elif btype == "thinking":
                            thinking += b.get("thinking", "")
                        elif btype == "tool_use":
                            tools.append(
                                {
                                    "id": b.get("id"),
                                    "name": b.get("name"),
                                    "input": b.get("input"),
                                }
                            )
                if content or thinking or tools:
                    turns.append(
                        {
                            "role": "assistant",
                            "content": content.strip(),
                            "thinking": thinking.strip() or None,
                            "tool_calls": tools or None,
                            "timestamp": rec.get("timestamp"),
                        }
                    )
    elif agent_id == "agy":
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            stype = rec.get("type")
            source = rec.get("source")

            if source == "USER_INPUT" or stype == "USER_INPUT":
                content = rec.get("content") or ""
                if content:
                    turns.append(
                        {
                            "role": "user",
                            "content": str(content).strip(),
                            "timestamp": rec.get("created_at"),
                        }
                    )
            elif source == "MODEL" or stype == "PLANNER_RESPONSE":
                content = rec.get("content") or ""
                thinking = rec.get("thinking") or None
                raw_tc = rec.get("tool_calls") or None
                tool_calls = None
                if raw_tc and isinstance(raw_tc, list):
                    tool_calls = []
                    for tc in raw_tc:
                        if isinstance(tc, dict):
                            c_name = tc.get("name") or "tool"
                            c_input = tc.get("input") or tc.get("args") or tc.get("parameters") or tc.get("arguments")
                            tool_calls.append({"name": c_name, "input": c_input})
                turns.append(
                    {
                        "role": "assistant",
                        "content": str(content).strip() if content else "",
                        "thinking": str(thinking).strip() if thinking else None,
                        "tool_calls": tool_calls,
                        "timestamp": rec.get("created_at"),
                    }
                )
    elif agent_id == "codex":
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if not isinstance(rec, dict):
                continue

            # Support payload structure in rollout-*.jsonl
            payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else rec
            rtype = payload.get("type") or rec.get("type")
            rrole = payload.get("role") or rec.get("role")

            if rtype == "message" or rrole in ("user", "assistant"):
                role = rrole or ("user" if rtype == "user" else "assistant")
                content = ""
                raw_c = payload.get("content") or rec.get("content")
                if isinstance(raw_c, list):
                    for b in raw_c:
                        if isinstance(b, dict):
                            txt = b.get("text") or b.get("output_text") or ""
                            if not txt.startswith("<environment_context>") and not txt.startswith("<permissions instructions>") and not txt.startswith("<skills_instructions>"):
                                content += txt
                elif isinstance(raw_c, str):
                    if not raw_c.startswith("<environment_context>") and not raw_c.startswith("<permissions instructions>") and not raw_c.startswith("<skills_instructions>"):
                        content = raw_c

                if content.strip():
                    turns.append(
                        {
                            "role": role,
                            "content": content.strip(),
                            "timestamp": rec.get("timestamp") or payload.get("timestamp"),
                        }
                    )
            elif rtype == "reasoning":
                summary = payload.get("summary")
                thinking_text = ""
                if isinstance(summary, list):
                    thinking_text = "\n".join(str(s) for s in summary if s)
                elif isinstance(summary, str):
                    thinking_text = summary
                if not thinking_text:
                    thinking_text = str(payload.get("content") or payload.get("text") or "")
                if thinking_text.strip():
                    turns.append(
                        {
                            "role": "assistant",
                            "content": "",
                            "thinking": thinking_text.strip(),
                            "timestamp": rec.get("timestamp") or payload.get("timestamp"),
                        }
                    )
            elif rtype in ("function_call", "custom_tool_call"):
                t_name = payload.get("name") or payload.get("tool") or "tool"
                t_args = payload.get("arguments") or payload.get("input") or {}
                if isinstance(t_args, str):
                    try:
                        t_args = json.loads(t_args)
                    except Exception:
                        pass
                call_id = payload.get("id") or payload.get("call_id")
                turns.append(
                    {
                        "role": "tool",
                        "content": "",
                        "tool_calls": [{"id": call_id, "name": t_name, "input": t_args}],
                        "timestamp": rec.get("timestamp") or payload.get("timestamp"),
                    }
                )
            elif rtype in ("function_call_output", "custom_tool_call_output"):
                out = payload.get("output")
                out_text = ""
                if isinstance(out, list):
                    for b in out:
                        if isinstance(b, dict):
                            out_text += b.get("text") or b.get("output") or ""
                        else:
                            out_text += str(b)
                elif out is not None:
                    out_text = str(out)
                turns.append(
                    {
                        "role": "tool",
                        "content": out_text.strip(),
                        "timestamp": rec.get("timestamp") or payload.get("timestamp"),
                    }
                )
            elif "text" in rec and isinstance(rec["text"], str):
                turns.append(
                    {
                        "role": "user",
                        "content": str(rec["text"]).strip(),
                        "timestamp": rec.get("ts"),
                    }
                )
    elif agent_id == "pi":
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if not isinstance(rec, dict):
                continue
            if rec.get("type") == "message":
                msg = rec.get("message") or {}
                role = msg.get("role")
                if role in ("user", "human"):
                    raw_c = msg.get("content")
                    text = ""
                    if isinstance(raw_c, list):
                        for b in raw_c:
                            if isinstance(b, dict) and b.get("type") == "text":
                                text += b.get("text", "")
                    elif isinstance(raw_c, str):
                        text = raw_c
                    if text.strip():
                        turns.append(
                            {
                                "role": "user",
                                "content": text.strip(),
                                "timestamp": msg.get("timestamp") or rec.get("timestamp"),
                            }
                        )
                elif role in ("assistant", "model"):
                    raw_c = msg.get("content") or []
                    content = ""
                    thinking = ""
                    tools = []
                    if isinstance(raw_c, list):
                        for b in raw_c:
                            if not isinstance(b, dict):
                                continue
                            btype = b.get("type")
                            if btype == "text":
                                t_val = b.get("text", "")
                                m_th = re.search(r"<thought>(.*?)</thought>", t_val, re.DOTALL)
                                if m_th:
                                    thinking += m_th.group(1).strip() + "\n"
                                    t_val = re.sub(r"<thought>.*?</thought>\s*", "", t_val, flags=re.DOTALL)
                                content += t_val
                            elif btype == "thinking":
                                thinking += b.get("thinking", "")
                            elif btype in ("toolCall", "tool_call"):
                                t_args = b.get("arguments") or b.get("input") or {}
                                if isinstance(t_args, str):
                                    try:
                                        t_args = json.loads(t_args)
                                    except Exception:
                                        pass
                                tools.append({"id": b.get("id"), "name": b.get("name"), "input": t_args})
                    elif isinstance(raw_c, str):
                        content = raw_c
                    if content.strip() or thinking.strip() or tools:
                        turns.append(
                            {
                                "role": "assistant",
                                "content": content.strip(),
                                "thinking": thinking.strip() or None,
                                "tool_calls": tools or None,
                                "timestamp": msg.get("timestamp") or rec.get("timestamp"),
                            }
                        )
                elif role == "toolResult":
                    tool_name = msg.get("toolName") or "tool"
                    raw_c = msg.get("content") or []
                    out_text = ""
                    if isinstance(raw_c, list):
                        for b in raw_c:
                            if isinstance(b, dict):
                                out_text += b.get("text") or b.get("output") or ""
                            else:
                                out_text += str(b)
                    elif raw_c is not None:
                        out_text = str(raw_c)
                    turns.append(
                        {
                            "role": "tool",
                            "content": out_text.strip(),
                            "tool_calls": [{"name": tool_name, "input": {}}],
                            "timestamp": msg.get("timestamp") or rec.get("timestamp"),
                        }
                    )
    elif agent_id == "hermes":
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if not isinstance(rec, dict):
                continue
            role = rec.get("role") or "assistant"
            content = rec.get("content") or ""
            if content:
                turns.append(
                    {
                        "role": role,
                        "content": str(content).strip(),
                        "timestamp": rec.get("timestamp"),
                    }
                )
    elif agent_id == "codebuddy":
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if not isinstance(rec, dict):
                continue
            rtype = rec.get("type")
            if rtype == "session-meta":
                continue
            if rtype == "message":
                role = rec.get("role") or "user"
                raw_c = rec.get("content") or ""
                content = ""
                thinking = ""
                tools = []
                if isinstance(raw_c, str):
                    content = raw_c
                elif isinstance(raw_c, list):
                    for b in raw_c:
                        if not isinstance(b, dict):
                            continue
                        btype = b.get("type")
                        if btype in ("text", "input_text", "output_text"):
                            txt = b.get("text", "")
                            if not txt.startswith("<system-reminder"):
                                content += txt
                        elif btype == "thinking":
                            thinking += b.get("thinking", "")
                        elif btype in ("tool_use", "tool_call"):
                            tools.append({"id": b.get("id"), "name": b.get("name"), "input": b.get("input")})
                if content.strip() or thinking.strip() or tools:
                    turns.append(
                        {
                            "role": role,
                            "content": content.strip(),
                            "thinking": thinking.strip() or None,
                            "tool_calls": tools or None,
                            "timestamp": rec.get("timestamp"),
                        }
                    )
            elif rtype == "reasoning":
                raw_c = rec.get("rawContent") or []
                thinking = ""
                if isinstance(raw_c, list):
                    for b in raw_c:
                        if isinstance(b, dict) and b.get("type") == "reasoning_text":
                            thinking += b.get("text", "")
                if thinking.strip():
                    turns.append(
                        {
                            "role": "assistant",
                            "content": "",
                            "thinking": thinking.strip(),
                            "timestamp": rec.get("timestamp"),
                        }
                    )
            elif rtype == "function_call":
                name = rec.get("name")
                args = rec.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        pass
                turns.append(
                    {
                        "role": "tool",
                        "content": "",
                        "tool_calls": [{"name": name, "input": args}],
                        "timestamp": rec.get("timestamp"),
                    }
                )
            elif rtype == "function_call_result":
                out = rec.get("output")
                out_txt = ""
                if isinstance(out, dict):
                    out_txt = out.get("text") or (json.dumps(out) if out else "")
                elif isinstance(out, str):
                    out_txt = out
                if out_txt.strip():
                    turns.append(
                        {
                            "role": "tool",
                            "content": out_txt.strip(),
                            "timestamp": rec.get("timestamp"),
                        }
                    )
    elif agent_id == "qoder":
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if not isinstance(rec, dict):
                continue
            rtype = rec.get("type")
            msg = rec.get("message") or {}
            role = msg.get("role") or rtype or "assistant"
            raw_c = msg.get("content") or rec.get("content") or ""
            content = ""
            thinking = ""
            tools = []
            if isinstance(raw_c, str):
                if not raw_c.startswith("<local-command-caveat"):
                    content = raw_c
            elif isinstance(raw_c, list):
                for b in raw_c:
                    if not isinstance(b, dict):
                        continue
                    btype = b.get("type")
                    if btype == "text":
                        txt = b.get("text", "")
                        if not txt.startswith("<local-command-caveat"):
                            content += txt
                    elif btype == "thinking":
                        thinking += b.get("thinking", "")
                    elif btype in ("tool_use", "tool_call"):
                        tools.append({"id": b.get("id"), "name": b.get("name"), "input": b.get("input")})
            if content.strip() or thinking.strip() or tools:
                turns.append(
                    {
                        "role": role,
                        "content": content.strip(),
                        "thinking": thinking.strip() or None,
                        "tool_calls": tools or None,
                        "timestamp": rec.get("timestamp"),
                    }
                )
    else:
        # Generic fallback
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if isinstance(rec, dict):
                    role = rec.get("role", "assistant")
                    content = rec.get("content", str(rec))
                    turns.append({"role": role, "content": str(content)})
                else:
                    turns.append({"role": "user", "content": str(rec)})
            except Exception:
                turns.append({"role": "user", "content": line})

    return turns


async def get_session_transcript(
    db_session, session_id: str, settings=None
) -> dict[str, Any]:
    """Fetch and return parsed conversation turns with sensitive data masked."""
    settings = settings or get_settings()

    session_obj = (
        db_session.execute(
            select(models.Session)
            .where(models.Session.id == session_id)
            .options(
                joinedload(models.Session.runtime).joinedload(models.Runtime.host),
                joinedload(models.Session.transcript),
            )
        )
        .unique()
        .scalar_one_or_none()
    )

    if not session_obj:
        raise ValueError(f"Session '{session_id}' not found")

    # If cached, return immediately
    if session_obj.transcript and session_obj.transcript.turns:
        return {
            "session_id": session_id,
            "turns": session_obj.transcript.turns,
            "has_secrets_masked": session_obj.transcript.has_secrets_masked,
            "masked_count": session_obj.transcript.masked_count,
            "cached": True,
            "cached_at": models.iso_utc(session_obj.transcript.cached_at),
        }

    # Fetch from remote runtime
    rt = session_obj.runtime
    if not rt:
        raise ValueError(f"Runtime not associated with session '{session_id}'")

    file_path = session_obj.file_path
    turns: list[dict[str, Any]] = []
    total_masked = 0
    ref = to_ref(rt)
    runner = build_runner(ref, settings)
    agent = session_obj.agent_id
    skey = session_obj.session_key

    # Helper to run python script via heredoc cleanly without quoting issues
    async def _run_py(script_body: str, timeout_sec: int) -> str | None:
        cmd = f"""python3 - << 'PY_EOF'
{script_body}
PY_EOF
"""
        res = await runner.run_script(cmd, timeout=timeout_sec)
        return res.stdout if res.ok else None

    # 1. Hermes: query ~/.hermes/state.db directly via python
    if agent == "hermes":
        script = f"""import sqlite3, json, os, sys
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
except Exception: pass
p = os.path.expanduser('~/.hermes/state.db')
if os.path.isfile(p):
    con = sqlite3.connect(p)
    cur = con.cursor()
    cur.execute('SELECT role, content, reasoning, tool_calls, timestamp FROM messages WHERE session_id=? OR session_id=? ORDER BY id ASC', ({repr(skey)}, {repr(session_id.split(':')[-1])}))
    rows = cur.fetchall()
    res = []
    for r in rows:
        role = r[0] or 'assistant'
        content = r[1] or ''
        thinking = r[2] or None
        tc_norm = None
        if r[3]:
            try:
                raw_tc = json.loads(r[3]) if isinstance(r[3], str) else r[3]
                if isinstance(raw_tc, list):
                    tc_norm = []
                    for c in raw_tc:
                        if isinstance(c, dict):
                            fn = c.get('function') or {{}}
                            t_name = fn.get('name') or c.get('name') or 'tool'
                            args = fn.get('arguments') or c.get('input') or c.get('arguments')
                            if isinstance(args, str):
                                try: args = json.loads(args)
                                except Exception: pass
                            tc_norm.append({{'id': c.get('id'), 'name': t_name, 'input': args}})
            except Exception: pass
        res.append({{'role': role, 'content': content, 'thinking': thinking, 'tool_calls': tc_norm, 'timestamp': r[4]}})
    print('___HERMES_JSON___' + json.dumps(res, ensure_ascii=False))
else:
    print('___HERMES_JSON___[]')
"""
        stdout = await _run_py(script, settings.ssh_connect_timeout + 15)
        if stdout and "___HERMES_JSON___" in stdout:
            part = stdout.split("___HERMES_JSON___")[-1].strip()
            try:
                turns = json.loads(part)
            except Exception: pass

    # 2. OpenCode: query ~/.local/share/opencode/opencode.db directly
    elif agent == "opencode":
        script = f"""import sqlite3, json, os, sys
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
except Exception: pass

p = os.path.expanduser('~/.local/share/opencode/opencode.db')
if os.path.isfile(p):
    con = sqlite3.connect(p)
    cur = con.cursor()
    cur.execute('SELECT m.id, m.time_created, m.data, p.data FROM message m LEFT JOIN part p ON m.id=p.message_id WHERE m.session_id=? ORDER BY m.time_created ASC, p.time_created ASC, p.id ASC', ({repr(skey)},))
    rows = cur.fetchall()
    msgs = {{}}
    msg_order = []
    for mid, m_ts, mdata_str, pdata_str in rows:
        if mid not in msgs:
            msg_order.append(mid)
            mdata = json.loads(mdata_str) if mdata_str else {{}}
            msgs[mid] = {{
                'role': mdata.get('role') or 'user',
                'ts': mdata.get('time', {{}}).get('created') or m_ts,
                'content_parts': [],
                'thinking_parts': [],
                'tool_calls': []
            }}
        if pdata_str:
            try:
                pdata = json.loads(pdata_str)
                ptype = pdata.get('type')
                if ptype == 'text' and pdata.get('text'):
                    msgs[mid]['content_parts'].append(pdata['text'])
                elif ptype == 'reasoning' and pdata.get('text'):
                    msgs[mid]['thinking_parts'].append(pdata['text'])
                elif ptype == 'tool':
                    state = pdata.get('state') or {{}}
                    t_name = pdata.get('tool') or 'tool'
                    t_input = state.get('input') or {{}}
                    t_out = state.get('output')
                    call_dict = {{'name': t_name, 'input': t_input}}
                    if pdata.get('callID'):
                        call_dict['id'] = pdata.get('callID')
                    if t_out is not None:
                        call_dict['output'] = str(t_out)
                    msgs[mid]['tool_calls'].append(call_dict)
                elif ptype == 'patch':
                    call_dict = {{
                        'name': 'patch',
                        'input': {{
                            'hash': pdata.get('hash'),
                            'files': pdata.get('files') or []
                        }}
                    }}
                    if pdata.get('diff'):
                        call_dict['input']['diff'] = pdata.get('diff')
                    if pdata.get('files'):
                        call_dict['input']['path'] = pdata['files'][0]
                    msgs[mid]['tool_calls'].append(call_dict)
            except Exception: pass

    turns = []
    for mid in msg_order:
        m = msgs[mid]
        role = m['role']
        content = '\\n\\n'.join(c for c in m['content_parts'] if c).strip()
        thinking = '\\n\\n'.join(t for t in m['thinking_parts'] if t).strip()
        tools = m['tool_calls']
        if role == 'user':
            if content:
                turns.append({{'role': 'user', 'content': content, 'timestamp': m['ts']}})
        else:
            if content or thinking or tools:
                turns.append({{
                    'role': 'assistant',
                    'content': content,
                    'thinking': thinking or None,
                    'tool_calls': tools or None,
                    'timestamp': m['ts']
                }})

    print('___OPENCODE_JSON___' + json.dumps(turns, ensure_ascii=False))
else:
    print('___OPENCODE_JSON___[]')
"""
        stdout = await _run_py(script, settings.ssh_connect_timeout + 15)
        if stdout and "___OPENCODE_JSON___" in stdout:
            part = stdout.split("___OPENCODE_JSON___")[-1].strip()
            try:
                turns = json.loads(part)
            except Exception: pass

    # 3. Codex: first try thread_history_*.sqlite for instant turn projection
    elif agent == "codex":
        script = f"""import sqlite3, json, os, glob, sys
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
except Exception: pass
home = os.path.expanduser('~')
dbs = sorted(glob.glob(os.path.join(home, '.codex', 'thread_history_*.sqlite')), reverse=True)
turns = []
sid = {repr(skey)}
if dbs and os.path.isfile(dbs[0]):
    try:
        con = sqlite3.connect(dbs[0])
        cur = con.cursor()
        rows = cur.execute('SELECT item_type, item_json, created_at_ms FROM thread_items WHERE thread_id=? ORDER BY rollout_ordinal ASC', (sid,)).fetchall()
        for itype, ijson_str, ts in rows:
            try:
                ijson = json.loads(ijson_str)
                if itype == 'userMessage':
                    c = ijson.get('content') or []
                    txt = ''.join(b.get('text', '') for b in c if isinstance(b, dict) and b.get('type') == 'text')
                    if txt: turns.append({{'role': 'user', 'content': txt, 'timestamp': ts}})
                elif itype == 'agentMessage':
                    txt = ijson.get('text') or ''
                    if txt: turns.append({{'role': 'assistant', 'content': txt, 'timestamp': ts}})
                elif itype in ('thought', 'reasoning'):
                    txt = ijson.get('text') or ijson.get('content') or ''
                    if txt: turns.append({{'role': 'assistant', 'content': '', 'thinking': txt, 'timestamp': ts}})
                elif itype == 'commandExecution':
                    cmd = ijson.get('command')
                    cwd = ijson.get('cwd')
                    out = ijson.get('aggregatedOutput') or ''
                    turns.append({{'role': 'tool', 'content': out, 'tool_calls': [{{'name': 'bash', 'input': {{'command': cmd, 'cwd': cwd}}}}], 'timestamp': ts}})
                elif itype == 'fileChange':
                    turns.append({{'role': 'tool', 'content': '', 'tool_calls': [{{'name': 'file_change', 'input': {{'changes': ijson.get('changes')}}}}], 'timestamp': ts}})
                elif itype == 'webSearch':
                    turns.append({{'role': 'tool', 'content': '', 'tool_calls': [{{'name': 'web_search', 'input': {{'query': ijson.get('query')}}}}], 'timestamp': ts}})
                elif itype == 'mcpToolCall':
                    turns.append({{'role': 'tool', 'content': '', 'tool_calls': [{{'name': ijson.get('tool'), 'input': ijson.get('arguments')}}], 'timestamp': ts}})
            except Exception: pass
    except Exception: pass
print('___CODEX_JSON___' + json.dumps(turns, ensure_ascii=False))
"""
        stdout = await _run_py(script, settings.ssh_connect_timeout + 15)
        if stdout and "___CODEX_JSON___" in stdout:
            part = stdout.split("___CODEX_JSON___")[-1].strip()
            try:
                turns = json.loads(part)
            except Exception: pass

        # Fallback to rollout decompression if sqlite had no items
        if not turns and file_path and not file_path.endswith((".db", ".sqlite")):
            if file_path.endswith(".zst"):
                read_cmd = f"zstd -dc {json.dumps(file_path)} 2>/dev/null | head -c 2000000"
            else:
                read_cmd = f"head -c 2000000 {json.dumps(file_path)}"
            exec_res = await runner.run_script(read_cmd, timeout=settings.ssh_connect_timeout + 15)
            if exec_res.ok and exec_res.stdout:
                raw_lines = exec_res.stdout.splitlines()
                turns = parse_raw_transcript_lines("codex", raw_lines)

    # 4. Standard file-based agents (Claude, AGY, CodeBuddy, Qoder, Pi)
    if not turns and file_path and not file_path.endswith((".db", ".sqlite")):
        read_cmd = f"head -c 2000000 {json.dumps(file_path)}"
        exec_res = await runner.run_script(read_cmd, timeout=settings.ssh_connect_timeout + 15)
        if exec_res.ok and exec_res.stdout:
            raw_lines = exec_res.stdout.splitlines()
            turns = parse_raw_transcript_lines(session_obj.agent_id, raw_lines)

    # 5. Fallback: if no turns, query agent history.jsonl for user prompts
    if not turns:
        if agent == "claude":
            script = f"""import json, os
hist = os.path.expanduser('~/.claude/history.jsonl')
sid = {repr(skey)}
turns = []
if os.path.isfile(hist):
    with open(hist, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            if sid in line:
                try:
                    obj = json.loads(line)
                    if obj.get('sessionId') == sid:
                        turns.append({{'role': 'user', 'content': obj.get('display', ''), 'timestamp': obj.get('timestamp')}})
                except Exception: pass
print('___HIST_JSON___' + json.dumps(turns))
"""
            stdout = await _run_py(script, settings.ssh_connect_timeout + 10)
            if stdout and "___HIST_JSON___" in stdout:
                part = stdout.split("___HIST_JSON___")[-1].strip()
                try:
                    turns = json.loads(part)
                except Exception: pass
        elif agent == "codex":
            script = f"""import json, os
hist = os.path.expanduser('~/.codex/history.jsonl')
sid = {repr(skey)}
turns = []
if os.path.isfile(hist):
    with open(hist, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            if sid in line:
                try:
                    obj = json.loads(line)
                    if obj.get('session_id') == sid:
                        turns.append({{'role': 'user', 'content': obj.get('text', ''), 'timestamp': (obj.get('ts') or 0)*1000}})
                except Exception: pass
print('___HIST_JSON___' + json.dumps(turns))
"""
            stdout = await _run_py(script, settings.ssh_connect_timeout + 10)
            if stdout and "___HIST_JSON___" in stdout:
                part = stdout.split("___HIST_JSON___")[-1].strip()
                try:
                    turns = json.loads(part)
                except Exception: pass

    # 6. Ultimate fallback: session title
    if not turns and session_obj.title:
        turns = [
            {
                "role": "user",
                "content": session_obj.title,
                "timestamp": models.iso_utc(session_obj.updated_at),
            }
        ]

    # Clean XML tags and agent artifacts, extract residual thinking/tool_calls, then apply secret masking to every turn
    from agent_fleet.services.xml_deconstruct import (
        extract_thinking_from_text,
        extract_xml_tool_calls,
        strip_agent_harness_wrappers,
    )
    for turn in turns:
        c_raw = str(turn.get("content") or "")
        if c_raw:
            c_no_th, extracted_th = extract_thinking_from_text(c_raw)
            if extracted_th and not turn.get("thinking"):
                turn["thinking"] = extracted_th

            c_clean_tools, extracted_tc = extract_xml_tool_calls(c_no_th)
            if extracted_tc:
                if turn.get("tool_calls") is None:
                    turn["tool_calls"] = []
                turn["tool_calls"].extend(extracted_tc)

            c_clean = strip_agent_harness_wrappers(c_clean_tools)
        else:
            c_clean = ""

        c, count1 = mask_sensitive_text(c_clean)
        turn["content"] = c
        total_masked += count1

        if turn.get("thinking"):
            th_clean = clean_transcript_text(turn["thinking"])
            th, count2 = mask_sensitive_text(th_clean)
            turn["thinking"] = th
            total_masked += count2

    # Save to SessionTranscript cache
    now = datetime.utcnow()
    tr = models.SessionTranscript(
        id=models.new_id(),
        session_id=session_id,
        turns=turns,
        has_secrets_masked=total_masked > 0,
        masked_count=total_masked,
        cached_at=now,
    )
    db_session.merge(tr)
    db_session.flush()

    return {
        "session_id": session_id,
        "turns": turns,
        "has_secrets_masked": total_masked > 0,
        "masked_count": total_masked,
        "cached": False,
        "cached_at": models.iso_utc(now),
    }
