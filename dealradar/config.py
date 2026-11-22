"""Lädt config.yaml, sources.yaml und .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
RISK_ORDER = {"niedrig": 0, "mittel": 1, "hoch": 2}


@dataclass
class Config:
    raw: dict[str, Any]
    sources: dict[str, dict[str, Any]]
    root: Path = ROOT
    env: dict[str, str] = field(default_factory=dict)

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    def path(self, key: str) -> Path:
        p = Path(os.path.expanduser(self.raw["paths"][key]))
        return p if p.is_absolute() else self.root / p

    @property
    def vault_dir(self) -> Path:
        return Path(os.path.expanduser(self.raw["notify"]["vault_dir"]))


def load_config(root: Path | None = None, config_file: str = "config.yaml",
                sources_file: str = "sources.yaml") -> Config:
    root = root or ROOT
    load_dotenv(root / ".env", override=False)
    raw = yaml.safe_load((root / config_file).read_text(encoding="utf-8"))
    sources = yaml.safe_load((root / sources_file).read_text(encoding="utf-8")) or {}
    if raw.get("risk_max") not in RISK_ORDER:
        raise ValueError(f"risk_max muss eines von {list(RISK_ORDER)} sein")
    # Überschreibungen per Umgebung, z. B. im Cloud-Lauf (GitHub Actions):
    # DEALRADAR_LLM_PROVIDER=gemini, DEALRADAR_CHANNELS=email
    if os.environ.get("DEALRADAR_LLM_PROVIDER"):
        raw["llm"]["provider"] = os.environ["DEALRADAR_LLM_PROVIDER"].strip()
    if os.environ.get("DEALRADAR_CHANNELS"):
        raw["notify"]["channels"] = [c.strip() for c in os.environ["DEALRADAR_CHANNELS"].split(",") if c.strip()]
    if os.environ.get("DEALRADAR_VAULT_DIR"):          # persönlicher Pfad bleibt in .env, nicht im Repo
        raw["notify"]["vault_dir"] = os.environ["DEALRADAR_VAULT_DIR"].strip()
    env = {k: v for k, v in os.environ.items()
           if k.startswith(("SMTP_", "MAIL_", "NTFY_")) or k in ("GITHUB_TOKEN", "GITHUB_REPOSITORY")}
    return Config(raw=raw, sources=sources, root=root, env=env)
