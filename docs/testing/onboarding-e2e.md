# Onboarding End-to-End Integration & Acceptance Testing

## 1. 概述 (Overview)

本测试套件 (`tests/test_onboarding_e2e.py`) 针对 **Issue #54** 交付，建立从全新干净环境（零历史配置与空白 SQLite 数据库）到完成首次远端主机添加、SSH 连通性诊断、全量探测扫描以及异常容错自愈的端到端自动化验收机制。

测试贯穿核心五步旅程：
1. **Fresh State Verification (初始空白环境核验)**：验证空库初始化时系统不残留冗余数据，Overview 与 Attention 处于干净无预警状态。
2. **Authentication Enforcement (安全鉴权边界核验)**：验证启用 `AGENT_FLEET_API_SECRET` 后，写操作强制要求凭证，未授权与非法凭证均返回 401 并包含标准 `WWW-Authenticate` 响应头，合法 Bearer Token 顺利通行。
3. **Host Onboarding & SSH Testing (主机接入与 SSH 诊断)**：通过 API 接入新主机，并在模拟网络/凭证故障时核验结构化故障归类 (`permission_denied` / `timeout` 等) 与可执行的修复指引 (#47)，成功时记录握手时延与内核信息。
4. **First Fleet Probe Scan (首次主机探测扫描)**：跟踪状态从「从未扫描 (`never_scanned`)」→「扫描中」→「扫描成功 (`completed`)」，核查实际扫描出的 Agent 事实（版本、配置模型）持久化落地，且 Attention 预警项自动闭环消除 (#59)。
5. **Error Recovery (配置出错与快速自愈)**：验证主机配置被改错或网络中断导致扫描失败时，历史已收集的 Agent 事实无损保留；在修正配置并重新测试 SSH 后，再次扫描自动自愈并清除 Attention 异常。

---

## 2. 本地快速运行 (Local Execution)

在项目根目录下，确保已安装依赖并激活虚拟环境：

### 运行全量 Onboarding E2E 测试
```bash
PYTHONPATH=backend .venv/bin/python -m pytest tests/test_onboarding_e2e.py -v
```

### 运行单个步骤针对性用例
```bash
# 仅运行端到端完整串联链路测试
PYTHONPATH=backend .venv/bin/python -m pytest tests/test_onboarding_e2e.py -k "test_complete_onboarding_journey" -v

# 仅测试鉴权拦截与 Bearer Token 边界
PYTHONPATH=backend .venv/bin/python -m pytest tests/test_onboarding_e2e.py -k "test_authentication_enforcement" -v

# 仅测试 SSH 连通性故障归类与修复指引
PYTHONPATH=backend .venv/bin/python -m pytest tests/test_onboarding_e2e.py -k "test_host_onboarding_and_ssh_diagnostics" -v

# 仅测试首次探测扫描与 Attention 异常闭环
PYTHONPATH=backend .venv/bin/python -m pytest tests/test_onboarding_e2e.py -k "test_fleet_probe_scan_and_attention_resolution" -v
```

---

## 3. 测试用例矩阵与核心断言 (Test Matrix & Assertions)

| 测试用例 | 覆盖阶段 | 验证目标与断言清单 |
| :--- | :--- | :--- |
| `test_complete_onboarding_journey_from_clean_state` | 完整五步闭环 | 贯穿 Step 1 至 Step 5 的全生命周期状态流转与数据一致性。 |
| `test_fresh_state_verification` | Step 1: 初始态 | `GET /api/inventory/hosts` 结果为空；`GET /api/attention` total 为 0；`GET /api/overview` 主机/运行时/安装实例全为 0。 |
| `test_authentication_enforcement` | Step 2: 鉴权隔离 | 未携带 Token 请求 `POST /api/inventory/hosts` 返回 401；错误 Token 返回 401；正确 Bearer Token 返回 200 成功入库。 |
| `test_host_onboarding_and_ssh_diagnostics` | Step 3: SSH 诊断 | 模拟 Publickey 拒绝返回 `failure_category="permission_denied"` 并提示 `authorized_keys`；模拟超时返回 `failure_category="timeout"`；模拟成功返回 `latency_ms` 与 `uname`。 |
| `test_fleet_probe_scan_and_attention_resolution` | Step 4: 首次扫描 | 扫描前 `GET /api/attention` 包含 `never_scanned`；扫描后 `ScanRun` 状态为 `completed`；`GET /api/installations` 存入 Codex 事实；Attention 归零。 |
| `test_complete_onboarding_journey` (Step 5) | Step 5: 故障自愈 | 端口改错后扫描失败，Attention 亮起 `scan_failed`；历史安装事实保持完整不丢失；端口修正并重新扫描后，Attention 重新归零自愈。 |

---

## 4. 持续集成 (CI Integration)

在 `.github/workflows/ci.yml` 的 `backend` 作业中：
- 步骤 `Run Onboarding E2E Verification` 会自动以 `-v` 模式执行 `tests/test_onboarding_e2e.py`。
- 全量单元测试 (`tests/` 目录) 亦会通过 `pytest-xdist` 自动发现并执行该套件。
- 日志中严禁打印明文敏感 Token，任何未授权访问在服务端均仅记录客户端来源与路由，不泄露内部密钥。
