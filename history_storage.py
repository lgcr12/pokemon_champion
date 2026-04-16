"""Persistent PKHeX run history with writable-path fallback."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DEFAULT_HISTORY_FILE = DATA_DIR / "pkhex_history.json"

# Cap to keep file size reasonable; oldest entries dropped.
MAX_ENTRIES = 2000


def _history_candidates() -> List[Path]:
    candidates = [DEFAULT_HISTORY_FILE]

    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.append(Path(local_appdata) / "pk_champion" / "data" / "pkhex_history.json")

    candidates.append(Path(tempfile.gettempdir()) / "pk_champion" / "pkhex_history.json")

    out: List[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _load_history_from(path: Path) -> List[Dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return []
    return [x for x in raw if isinstance(x, dict)]


def _first_existing_history_file() -> Optional[Path]:
    for path in _history_candidates():
        if not path.is_file():
            continue
        try:
            _load_history_from(path)
            return path
        except (json.JSONDecodeError, OSError):
            continue
    return None


def _preferred_history_file() -> Path:
    return _first_existing_history_file() or _history_candidates()[0]


def load_history() -> List[Dict[str, Any]]:
    path = _first_existing_history_file()
    if path is None:
        return []
    try:
        raw = _load_history_from(path)
        changed = False
        for item in raw:
            if isinstance(item, dict) and "id" not in item:
                item["id"] = uuid.uuid4().hex
                changed = True
        if changed:
            save_history(raw)
        return [x for x in raw if isinstance(x, dict)]
    except (json.JSONDecodeError, OSError):
        return []


def save_history(items: List[Dict[str, Any]]) -> None:
    payload = json.dumps(items, ensure_ascii=False, indent=2)
    last_error: Optional[OSError] = None
    for path in _history_candidates():
        try:
            _ensure_dir(path)
            path.write_text(payload, encoding="utf-8")
            return
        except OSError as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error


def append_entry(
    label: str,
    blocks_en: List[str],
    blocks_zh: List[str],
    titles: List[str],
    full_text: str,
) -> List[Dict[str, Any]]:
    items = load_history()
    entry: Dict[str, Any] = {
        "id": uuid.uuid4().hex,
        "at": datetime.now().isoformat(timespec="seconds"),
        "label": label,
        "blocks_en": blocks_en,
        "blocks_zh": blocks_zh,
        "titles": titles,
        "full": full_text,
    }
    items.insert(0, entry)
    if len(items) > MAX_ENTRIES:
        items = items[:MAX_ENTRIES]
    save_history(items)
    return items


def delete_entry(entry_id: str) -> List[Dict[str, Any]]:
    items = [x for x in load_history() if x.get("id") != entry_id]
    save_history(items)
    return items


def clear_all() -> None:
    save_history([])


def history_file_path() -> Path:
    return _preferred_history_file()
