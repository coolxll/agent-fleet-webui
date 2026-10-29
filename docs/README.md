# Agent Fleet WebUI 文档导航与索引库

本文档索引了项目的系统设计、技术架构、需求规范与运维指南。

---

## 📑 核心文档分类

### 1. 系统架构与设计
- **[architecture.md](architecture.md)**：系统分层架构设计、数据流向模型、探针通信协议与状态机定义。
- **[domain-models.md](domain-models.md)**：系统领域建模与完整 Mermaid E-R 实体关系图、实体字典及五大可扩展性设计契约。
- **[requirements.md](requirements.md)**：各平台（macOS、Linux、Windows、WSL）与主流 Agent 的原始探测规范与验收要求。
- **[research.md](research.md)**：各 Agent（Claude Code, Codex, OpenCode, Pi, Hermes 等）配置文件路径、发现规则与版本管理机制调研。

### 2. 算力与 Runner Fleet 运维
- **[runner-fleet-guide.md](runner-fleet-guide.md)**：跨多机声明式 GitHub Actions Runner Fleet 编排与代理出口共享管理指南。
- **[../config/runner_fleet.yaml](../config/runner_fleet.yaml)**：声明式主机 Profile（代理、用户、标签）与仓库期望运行矩阵配置。

### 3. 工程管理与进度
- **[../AGENTS.md](../AGENTS.md)**：Agent 协作规范、GitHub Issue & PR 交付闭环标准与 Git Worktree 管理原则。
- **[progress.md](progress.md)**：项目迭代历史、各阶段特性里程碑与演进日志。
