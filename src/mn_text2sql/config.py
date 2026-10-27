"""Тохиргоо ачаалах: config/default.yaml + (сонголттой) нэмэлт YAML + орчны хувьсагч."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]

ENV_OVERRIDES = {
    "T2S_ADMIN_DSN": ("db", "admin_dsn"),
    "T2S_BOT_DSN": ("db", "bot_dsn"),
    "T2S_DB_NAME": ("db", "name"),
    "T2S_LLM_BASE_URL": ("llm", "base_url"),
    "T2S_LLM_MODEL": ("llm", "model"),
    "T2S_LLM_FAMILY": ("llm", "family"),
    "T2S_EMBED_MODEL": ("retrieval", "embedding_model"),
    "T2S_EMBED_DEVICE": ("retrieval", "embedding_device"),
}


class Config(dict):
    """Цэгээр хандах боломжтой dict (cfg.db.name)."""

    def __getattr__(self, key: str) -> Any:
        try:
            value = self[key]
        except KeyError as exc:  # pragma: no cover - алдааны мессеж
            raise AttributeError(key) from exc
        return Config(value) if isinstance(value, dict) and not isinstance(value, Config) else value

    def __setattr__(self, key: str, value: Any) -> None:
        self[key] = value

    def get_path(self, dotted: str, default: Any = None) -> Any:
        node: Any = self
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _to_config(obj: Any) -> Any:
    if isinstance(obj, dict):
        return Config({k: _to_config(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [_to_config(v) for v in obj]
    return obj


def load_config(path: str | os.PathLike | None = None, overrides: dict | None = None) -> Config:
    """default.yaml-г ачаалж, нэмэлт файл, орчны хувьсагч, overrides-оор дарна."""
    with open(ROOT / "config" / "default.yaml", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    extra = path or os.environ.get("T2S_CONFIG")
    if extra:
        extra_path = Path(extra)
        if not extra_path.is_absolute():
            extra_path = ROOT / extra_path
        with open(extra_path, encoding="utf-8") as fh:
            data = deep_merge(data, yaml.safe_load(fh) or {})
    for env, (section, key) in ENV_OVERRIDES.items():
        if os.environ.get(env):
            data.setdefault(section, {})[key] = os.environ[env]
    if overrides:
        data = deep_merge(data, overrides)
    return _to_config(data)


def resolve_path(p: str | os.PathLike) -> Path:
    """Тохиргоон дахь харьцангуй замыг төслийн үндсэн хавтаснаас тооцно."""
    path = Path(p)
    return path if path.is_absolute() else ROOT / path


def load_models(path: str | os.PathLike = "config/models.yaml") -> dict:
    with open(resolve_path(path), encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}
