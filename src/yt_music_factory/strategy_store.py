from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .asset_strategy import deep_merge, resolve_asset_strategy, strategy_preset
from .utils import ensure_dir, project_root


def default_strategy_file() -> Path:
    if path := os.getenv("YMF_ASSET_STRATEGY_FILE"):
        return Path(path)
    if Path("/app").exists():
        return Path("/app/.secrets/asset_strategy.json")
    return project_root() / ".secrets" / "asset_strategy.json"


def load_strategy_store(path: Path | None = None) -> dict[str, Any]:
    path = path or default_strategy_file()
    if not path.exists():
        return {"global": strategy_preset("standard"), "channels": {}, "categories": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "global": resolve_asset_strategy(payload.get("global") or {}),
        "channels": payload.get("channels") or {},
        "categories": payload.get("categories") or {},
    }


def save_strategy_store(payload: dict[str, Any], path: Path | None = None) -> None:
    path = path or default_strategy_file()
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_effective_strategy(
    *,
    path: Path | None = None,
    channel_id: str | None = None,
    category_key: str | None = None,
    override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    store = load_strategy_store(path)
    policy = resolve_asset_strategy(store.get("global") or {})
    if channel_id and channel_id in (store.get("channels") or {}):
        policy = deep_merge(policy, store["channels"][channel_id])
    if category_key and category_key in (store.get("categories") or {}):
        policy = deep_merge(policy, store["categories"][category_key])
    if override:
        policy = deep_merge(policy, override)
    return resolve_asset_strategy(policy)


def set_strategy_profile(profile: str, *, path: Path | None = None, scope: str = "global", key: str | None = None) -> dict[str, Any]:
    store = load_strategy_store(path)
    policy = strategy_preset(profile)
    if scope == "channel" and key:
        store.setdefault("channels", {})[key] = policy
    elif scope == "category" and key:
        store.setdefault("categories", {})[key] = policy
    else:
        store["global"] = policy
    save_strategy_store(store, path)
    return policy

