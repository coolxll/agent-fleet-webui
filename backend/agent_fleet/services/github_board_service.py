"""GitHub-driven Task Board Service.

Integrates GitHub Issues, Pull Requests, and Actions (CI Workflow Runs)
into a unified lifecycle pipeline board.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


def _run_gh_json(args: list[str], timeout: int = 15) -> Any:
    """Execute a gh CLI command and parse JSON output."""
    gh_bin = shutil.which("gh")
    if not gh_bin:
        raise RuntimeError("GitHub CLI ('gh') is not installed or not in PATH.")

    cmd = [gh_bin] + args
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode != 0:
            err = proc.stderr.strip() or f"gh exited with code {proc.returncode}"
            logger.warning("gh command failed: %s -> %s", " ".join(cmd), err)
            raise RuntimeError(err)
        return json.loads(proc.stdout or "[]")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"GitHub command timed out after {timeout}s: {' '.join(cmd)}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to parse gh output as JSON: {exc}")


def get_default_repo() -> str:
    """Detect current repository owner/name from git remote or gh."""
    try:
        res = _run_gh_json(["repo", "view", "--json", "nameWithOwner"])
        if isinstance(res, dict) and res.get("nameWithOwner"):
            return res["nameWithOwner"]
    except Exception:
        pass
    return "coolxll/agent-fleet-webui"


def extract_linked_issues(text: str) -> list[int]:
    """Extract issue numbers referenced by Closes/Fixes/Refs or branch patterns."""
    issues = set()
    if not text:
        return []

    # Pattern 1: Closes #12, Fixes #12, Refs #12
    for m in re.finditer(r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|refs?)\s*#(\d+)", text, re.IGNORECASE):
        issues.add(int(m.group(1)))

    # Pattern 2: issue-12 or issue/12
    for m in re.finditer(r"issue[-_/](\d+)", text, re.IGNORECASE):
        issues.add(int(m.group(1)))

    return sorted(issues)


def fetch_github_board(repo: str | None = None) -> dict[str, Any]:
    """Fetch aggregated GitHub issues, PRs, and Actions for the board."""
    target_repo = repo or get_default_repo()

    repo_arg = ["--repo", target_repo]

    # 1. Fetch Issues
    issues_raw = []
    try:
        issues_raw = _run_gh_json(
            [
                "issue",
                "list",
                *repo_arg,
                "--state",
                "all",
                "--limit",
                "40",
                "--json",
                "number,title,state,labels,assignees,updatedAt,createdAt,url,body",
            ]
        )
    except Exception as exc:
        logger.warning("Failed to fetch issues: %s", exc)

    # 2. Fetch Pull Requests
    prs_raw = []
    try:
        prs_raw = _run_gh_json(
            [
                "pr",
                "list",
                *repo_arg,
                "--state",
                "all",
                "--limit",
                "30",
                "--json",
                "number,title,state,headRefName,baseRefName,updatedAt,createdAt,url,body,isDraft",
            ]
        )
    except Exception as exc:
        logger.warning("Failed to fetch PRs: %s", exc)

    # 3. Fetch Workflow Runs (Actions)
    actions_raw = []
    try:
        actions_raw = _run_gh_json(
            [
                "run",
                "list",
                *repo_arg,
                "--limit",
                "25",
                "--json",
                "databaseId,name,status,conclusion,headBranch,event,updatedAt,createdAt,url",
            ]
        )
    except Exception as exc:
        logger.warning("Failed to fetch workflow runs: %s", exc)

    # Map latest workflow run by branch
    branch_to_action: dict[str, dict[str, Any]] = {}
    for run in actions_raw:
        branch = run.get("headBranch")
        if branch and branch not in branch_to_action:
            branch_to_action[branch] = run

    # Map PRs by branch and linked issues
    issue_to_prs: dict[int, list[dict[str, Any]]] = {}
    enriched_prs = []
    for pr in prs_raw:
        head_branch = pr.get("headRefName") or ""
        pr_text = f"{pr.get('title', '')} {pr.get('body', '')} {head_branch}"
        linked_issue_nums = extract_linked_issues(pr_text)
        
        # Attach CI action if available
        action = branch_to_action.get(head_branch)
        pr_obj = {
            **pr,
            "linked_issue_numbers": linked_issue_nums,
            "latest_action": action,
        }
        enriched_prs.append(pr_obj)

        for num in linked_issue_nums:
            issue_to_prs.setdefault(num, []).append(pr_obj)

    # Enrich Issues with linked PRs and Actions
    enriched_issues = []
    for iss in issues_raw:
        num = iss.get("number")
        linked_prs = issue_to_prs.get(num, [])
        latest_action = None
        for p in linked_prs:
            if p.get("latest_action"):
                latest_action = p["latest_action"]
                break

        enriched_issues.append(
            {
                **iss,
                "linked_prs": linked_prs,
                "latest_action": latest_action,
            }
        )

    return {
        "ok": True,
        "repo": target_repo,
        "issues": enriched_issues,
        "pull_requests": enriched_prs,
        "actions": actions_raw,
        "counts": {
            "total_issues": len(enriched_issues),
            "open_issues": sum(1 for i in enriched_issues if i.get("state") == "OPEN"),
            "open_prs": sum(1 for p in enriched_prs if p.get("state") == "OPEN"),
            "actions_count": len(actions_raw),
        },
    }


def create_github_issue(title: str, body: str, labels: list[str] | None = None, repo: str | None = None) -> dict[str, Any]:
    """Create a new GitHub issue via gh CLI."""
    target_repo = repo or get_default_repo()
    gh_bin = shutil.which("gh")
    if not gh_bin:
        raise RuntimeError("GitHub CLI ('gh') is not installed or not in PATH.")

    cmd = [gh_bin, "issue", "create", "--repo", target_repo, "--title", title, "--body", body]
    if labels:
        for lbl in labels:
            if lbl.strip():
                cmd.extend(["--label", lbl.strip()])

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"gh issue create failed with code {proc.returncode}")

    url = proc.stdout.strip()
    issue_number = None
    m = re.search(r"/issues/(\d+)", url)
    if m:
        issue_number = int(m.group(1))

    return {
        "ok": True,
        "url": url,
        "number": issue_number,
        "repo": target_repo,
    }


def rerun_workflow_run(run_id: str | int, repo: str | None = None) -> dict[str, Any]:
    """Rerun a failed workflow run via gh CLI."""
    target_repo = repo or get_default_repo()
    gh_bin = shutil.which("gh")
    if not gh_bin:
        raise RuntimeError("GitHub CLI ('gh') is not installed or not in PATH.")

    cmd = [gh_bin, "run", "rerun", str(run_id), "--repo", target_repo]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"gh run rerun failed with code {proc.returncode}")

    return {
        "ok": True,
        "run_id": str(run_id),
        "message": proc.stdout.strip() or "Workflow rerun requested",
    }
