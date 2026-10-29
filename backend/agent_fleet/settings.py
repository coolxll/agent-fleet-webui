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
    auto_seed_legacy: bool = True
    workspace_root: Path = ROOT.parent

    @property
    def resolved_inventory_paths(self) -> list[Path]:
        """Return inventory paths, auto-discovering inventory.yml if inventory.yaml doesn't exist."""
        resolved: list[Path] = []
        for p in self.inventory_paths:
            path = Path(p)
            if not path.is_absolute():
                path = ROOT / path
            if path.exists():
                resolved.append(path)
            elif path.name == "inventory.yaml":
                alt = path.with_name("inventory.yml")
                if alt.exists():
                    resolved.append(alt)
                else:
                    resolved.append(path)
            elif path.name == "inventory.yml":
                alt = path.with_name("inventory.yaml")
                if alt.exists():
                    resolved.append(alt)
                else:
                    resolved.append(path)
            else:
                resolved.append(path)
        return resolved

    @property
    def primary_inventory_path(self) -> Path:
        """The main inventory file path for read/write in UI/CLI."""
        resolved = self.resolved_inventory_paths
        for p in resolved:
            if p.exists() and p.suffix.lower() in (".yaml", ".yml"):
                return p
        if resolved:
            return resolved[0]
        yml = ROOT / "inventory.yml"
        if yml.exists():
            return yml
        return ROOT / "inventory.yaml"

    # Scanning
    scan_concurrency: int = 4
    ssh_connect_timeout: int = 10
    command_timeout: int = 25
    scan_ttl_seconds: int = 6 * 3600

    # Read-only source; never run magpie or write to its authentication store.
    magpie_config_dir: Path = Path.home() / ".config" / "magpie"

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

    # Access Control & Security
    api_secret: str | None = None
    allow_anonymous_readonly: bool = True

    @property
    def is_auth_enabled(self) -> bool:
        return bool(self.api_secret and self.api_secret.strip())

    def safe_dict(self) -> dict:
        """Return public settings dictionary without sensitive secrets."""
        fields = [
            "db_path",
            "inventory_paths",
            "auto_seed_legacy",
            "workspace_root",
            "scan_concurrency",
            "ssh_connect_timeout",
            "command_timeout",
            "scan_ttl_seconds",
            "discover_wsl",
            "include_local_runtime",
            "local_host_label",
            "api_prefix",
            "allow_anonymous_readonly",
        ]
        data = {}
        for f in fields:
            val = getattr(self, f, None)
            if isinstance(val, Path):
                data[f] = str(val)
            elif isinstance(val, list):
                data[f] = [str(x) if isinstance(x, Path) else x for x in val]
            else:
                data[f] = val
        data["auth_required"] = self.is_auth_enabled
        return data

    def __repr__(self) -> str:
        attrs = []
        for k in type(self).model_fields:
            if k == "api_secret":
                val = "'***'" if self.api_secret else "None"
            else:
                val = repr(getattr(self, k))
            attrs.append(f"{k}={val}")
        return f"Settings({', '.join(attrs)})"

    def __str__(self) -> str:
        return self.__repr__()

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
