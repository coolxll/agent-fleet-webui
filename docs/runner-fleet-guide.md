# GitHub Actions Runner Fleet 运维与声明式管理指南

本文档介绍如何在私有算力池（如 `runner-node-01`、`runner-node-02` 等）中以**声明式（Desired State）**、**纯出站安全（Outbound Only）**的方式管理多仓库的 GitHub Self-Hosted Runners。

---

## 1. 核心架构与安全原则

1. **纯出站长轮询（Outbound Only，零端口暴露）**：
   - 所有 Runner 进程通过 HTTPS/WSS 主动连接 GitHub 官方接口长轮询任务；
   - 内部主机**绝不需要公网 IP，也绝不向公网开放任何入站端口**，在企业局域网或内网环境下原生安全。
2. **中心代理出口共享（Centralized Proxy Egress）**：
   - `runner-node-01` 具备全局代理能力；
   - 连入 Tailscale 的节点（如 `runner-node-02`、开发笔记本）可通过 `socks5://100.64.0.1:1080` 共享该代理出站；
   - 同局域网节点可通过内网 IP `http://192.168.1.100:8118` 共享出站，免去每台机器单独配置客户端的繁琐。
3. **声明式矩阵驱动（Desired State）**：
   - 在 [`config/runner_fleet.yaml`](../config/runner_fleet.yaml) 中声明仓库与主机的运行拓扑；
   - 通过 `sync` 命令自动计算差集，对缺失或掉线节点自动补齐与唤醒，对多余节点自动清理。

---

## 2. 配置文件格式说明 (`config/runner_fleet.yaml`)

```yaml
# 主机 Profile 定义：一次性固化每台主机的 SSH 用户、代理及标签
hosts:
  runner-node-01:
    description: "高性能内网算力机"
    user: "devuser"
    proxy: "http://127.0.0.1:8118"
    labels: ["self-hosted", "Linux", "X64", "runner-node-01"]
    max_runners: 6

  runner-node-02:
    description: "日常开发工作站 (复用 runner-node-01 Tailscale SOCKS5 代理)"
    user: "devuser"
    proxy: "socks5://100.64.0.1:1080"
    labels: ["self-hosted", "Linux", "X64", "runner-node-02"]
    max_runners: 2

# 期望运行矩阵：声明哪些仓库期望在哪些主机提供算力
pools:
  sample-org/sample-repo:
    - runner-node-01
    - runner-node-02
```

---

## 3. CLI 命令速查

```bash
# 1. 查看全网全局大盘（跨所有仓库与主机的实时在线/离线/繁忙状态）
./scripts/manage_runner.sh overview

# 2. 一键声明式对齐与自愈（按 yaml 声明补齐缺失节点、重新唤醒异常节点）
./scripts/manage_runner.sh sync

# 3. 一键垃圾回收（清理所有关联仓库中已失联的 offline 僵尸 Runner）
./scripts/manage_runner.sh prune

# 4. 单仓库交互指令
./scripts/manage_runner.sh status sample-org/sample-repo
./scripts/manage_runner.sh start sample-org/sample-repo runner-node-01
./scripts/manage_runner.sh stop sample-org/sample-repo runner-node-01
./scripts/manage_runner.sh logs sample-org/sample-repo runner-node-01
```
