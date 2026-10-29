# Agent Fleet - SQLite 一致性备份指南

本指南介绍 Agent Fleet 的 SQLite 数据库一致性备份机制、CLI 命令使用、WAL 处理原理及元数据校验规范。

---

## 1. 概述与核心原理

Agent Fleet 使用 SQLite 作为本地持久化数据库（默认路径为 `data/fleet.db`），并在连接池中启用了 **WAL (Write-Ahead Logging)** 模式以保证高并发读写性能。

在 WAL 模式下，直接使用文件系统命令（如 `cp`、`rsync`）复制活动的 `.db` 文件存在严重数据损坏风险：
- 可能会产生未提交事务的脏读或撕裂页（torn pages）；
- 最新的修改通常驻留在 `-wal` 与 `-shm` 文件中，单独复制 `.db` 会导致数据丢失或索引不一致。

Agent Fleet 采用 **SQLite 官方 Online Backup API** (`sqlite3.Connection.backup()`)：
- **热备份能力**：服务无需停止或断开连接，在数据库处于活跃读写状态时即可执行；
- **强一致性**：在页级锁定机制保护下生成原子、完整、自包含的目标快照数据库；
- **自动校验**：完成快照后自动执行 `PRAGMA integrity_check` 与 SHA-256 校验哈希生成；
- **安全权限控制**：生成的备份文件与同名元数据文件均默认设置 `0600` 权限（仅属主读写）。

---

## 2. CLI 命令使用

Agent Fleet CLI 提供了 `backup` 子命令：

```bash
# 基本备份（使用默认目录 data/backups/ 及带时间戳的默认文件名）
python -m agent_fleet.cli backup

# 指定输出目录与自定义备份文件名
python -m agent_fleet.cli backup --output-dir /var/backups/fleet --filename fleet_prod.db

# 若目标备份已存在，强制覆盖
python -m agent_fleet.cli backup --output-dir data/backups --filename fleet_prod.db --overwrite

# 查看帮助
python -m agent_fleet.cli backup --help
```

### 参数说明

| 参数 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `--output-dir` | `data/backups/` (即 `settings.data_dir / "backups"`) | 备份文件及元数据输出目录 |
| `--filename` | `fleet_backup_YYYYMMDD_HHMMSS.db` | 目标备份文件名（若未提供 `.db` 后缀将自动补齐） |
| `--overwrite` | `False` | 默认当目标文件或元数据已存在时阻断退出；指定此项则允许原子替换 |

### 执行输出示例

命令执行成功后，终端标准输出格式化的 JSON 元数据：

```json
{
  "backup_name": "fleet_prod.db",
  "created_at": "2026-10-29T09:58:00.123456+00:00",
  "app_version": "0.2.0",
  "schema_version": 1,
  "sha256": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
  "file_size_bytes": 147456,
  "integrity_ok": true,
  "table_counts": {
    "agent_configurations": 12,
    "agent_installations": 24,
    "hosts": 5,
    "runtimes": 6,
    "schema_migrations": 1
  }
}
```

---

## 3. 伴随元数据规范 (`<backup_name>.meta.json`)

每个生成的备份数据库文件（例如 `fleet_prod.db`）同级目录下均会同时生成一份同名伴随元数据文件（例如 `fleet_prod.db.meta.json`）。

元数据字段定义如下：

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `backup_name` | `string` | 备份文件名称（含扩展名） |
| `created_at` | `string` | ISO 8601 UTC 备份创建时间戳 |
| `app_version` | `string` | 当前 Agent Fleet 服务版本号 |
| `schema_version` | `integer` | SQLite 模式版本号（对应 `PRAGMA user_version` 或已应用的最新迁移版本） |
| `sha256` | `string` | 备份数据库文件的 SHA-256 校验和（小写十六进制 64 位） |
| `file_size_bytes` | `integer` | 备份文件字节大小 |
| `integrity_ok` | `boolean` | `PRAGMA integrity_check` 校验结果是否完全通过 |
| `table_counts` | `object` | 各业务表与系统迁移表的行数统计字典（排除内部 `sqlite_%` 表） |

---

## 4. 备份恢复与可信校验流程

恢复备份前，建议校验备份文件与伴随元数据的一致性：

```bash
# 1. 验证 SHA-256 校验和
shasum -a 256 data/backups/fleet_prod.db

# 2. 验证 SQLite 完整性
sqlite3 data/backups/fleet_prod.db "PRAGMA integrity_check;"
# 预期输出: ok
```

在 Python 代码中，亦可直接使用内置服务校验：

```python
from pathlib import Path
from agent_fleet.services.backup_service import verify_backup

result = verify_backup(Path("data/backups/fleet_prod.db"))
assert result["integrity_ok"] is True
assert result["checksum_ok"] is True
```

---

## 5. 备份范围与边界说明 (重要)

> [!WARNING]
> **以下凭证与配额文件不属于 SQLite 备份范围**：
>
> 1. **配额与账号凭据**：存储在 `data/quota_accounts.json`、`data/credentials.json` 等独立 JSON 文件中的敏感凭据；
> 2. **环境配置与密钥**：项目根目录 `.env` 环境变量文件；
> 3. **SSH 密钥**：用户主机使用的 SSH 私钥文件（通常在 `~/.ssh/`）。
>
> **建议运维策略**：
> 如需完整灾备，请将 SQLite 一致性备份产物与加密备份的 `data/*.json` 凭据配置配合归档。
