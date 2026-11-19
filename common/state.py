from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def default_state() -> dict[str, Any]:
    """Generates an empty default state structure."""
    return {
        "version": 1,
        "seen_ids": [],
        "history": [],
        "last_run_at": None,
        "timestamps": {},
    }


def load_state(state_file: Path | str, default: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Loads JSON state safely from disk. Returns default state if file doesn't exist or is corrupt.
    """
    path = Path(state_file)
    fallback = default if default is not None else default_state()

    if not path.is_file():
        return fallback.copy()

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            logger.warning("State file %s does not contain a JSON dict. Using default.", path)
            return fallback.copy()
    except Exception as exc:
        logger.warning("Error reading state file %s (%s). Using default state.", path, exc)
        return fallback.copy()


def save_state(state_file: Path | str, state: dict[str, Any]) -> None:
    """
    Atomically writes state to disk using a temporary file and atomic replace.
    Ensures parent directories exist.
    """
    path = Path(state_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f"{path.name}.tmp"

    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        temp_path.replace(path)
    except Exception as exc:
        logger.error("Failed to save state to %s: %s", path, exc)
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise


def is_seen(state: dict[str, Any], item_id: str | int, key: str = "seen_ids") -> bool:
    """Checks whether an item ID or URL string has already been processed."""
    seen_list = state.get(key, [])
    # Convert item_id to string representation for consistent comparison
    str_id = str(item_id)
    return str_id in seen_list or item_id in seen_list


def mark_seen(
    state: dict[str, Any],
    item_id: str | int,
    key: str = "seen_ids",
    max_items: int = 2000,
) -> None:
    """Marks an item ID as seen and caps list size to prevent unbounded memory growth."""
    seen_list = state.setdefault(key, [])
    str_id = str(item_id)
    if str_id not in seen_list:
        seen_list.append(str_id)

    # Prune oldest entries if size exceeds max_items
    if len(seen_list) > max_items:
        state[key] = seen_list[-max_items:]


def record_action(
    state: dict[str, Any],
    action_type: str,
    payload: dict[str, Any],
    history_key: str = "history",
    max_history: int = 500,
) -> None:
    """Appends an event record with UTC timestamp to the state history."""
    now_iso = datetime.now(timezone.utc).isoformat()
    record = {
        "action": action_type,
        "timestamp": now_iso,
        **payload,
    }
    history = state.setdefault(history_key, [])
    history.append(record)

    state.setdefault("timestamps", {})[action_type] = now_iso
    state["last_run_at"] = now_iso

    if len(history) > max_history:
        state[history_key] = history[-max_history:]


def get_last_run_time(state: dict[str, Any], action_type: str | None = None) -> str | None:
    """Retrieves ISO timestamp of last run for a given action or overall."""
    if action_type:
        return state.get("timestamps", {}).get(action_type)
    return state.get("last_run_at")
