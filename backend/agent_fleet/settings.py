from __future__ import annotations

from pathlib import Path
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENT_FLEET_", extra="ignore")

    # Storage
    db_path: Path = ROOT / "data" / "fleet.db"

    # Inventory sources. Later entries fill gaps for the same host name.
    # Hosts come only from these files; ~/.ssh/config is not auto-imported.
    inventory_paths: list[Path] = [
        ROOT / "inventory.yaml",
    ]
    workspace_root: Path = ROOT.parent

    # Scanning
    scan_concurrency: int = 4
    ssh_connect_timeout: int = 10
    command_timeout: int = 25
    scan_ttl_seconds: int = 6 * 3600

    # Security: remote raw config bytes are parsed in memory and dropped after
    # sanitization; they are never written to disk.
    persist_raw_config: bool = False

    # WSL distros are normally reachable through their own SSH endpoint.
    # When that is not the case, turn this on to discover them through the
    # Windows host (wsl.exe -l -q) and probe each distro separately.
    discover_wsl: bool = False

    # The machine running the backend can be probed without SSH.
    include_local_runtime: bool = True
    local_host_label: str = "local-controller"

    api_prefix: str = "/api"

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
