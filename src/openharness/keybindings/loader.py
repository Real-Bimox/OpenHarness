"""Load keybindings from config."""

from __future__ import annotations

from pathlib import Path

from openharness.config.paths import get_config_dir
from openharness.keybindings.parser import parse_keybindings
from openharness.keybindings.resolver import resolve_keybindings


def get_keybindings_path() -> Path:
    """Return the user keybindings path."""
    return get_config_dir() / "keybindings.json"


_KEYBINDINGS_CACHE: tuple[tuple[str, int, int], dict[str, str]] | None = None


def load_keybindings() -> dict[str, str]:
    """Load and merge keybindings, cached on the file's mtime/size."""
    global _KEYBINDINGS_CACHE
    path = get_keybindings_path()
    try:
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        key = (str(path), -1, -1)
    if _KEYBINDINGS_CACHE is not None and _KEYBINDINGS_CACHE[0] == key:
        return dict(_KEYBINDINGS_CACHE[1])
    if key[1] == -1:
        result = resolve_keybindings()
    else:
        result = resolve_keybindings(parse_keybindings(path.read_text(encoding="utf-8")))
    _KEYBINDINGS_CACHE = (key, result)
    return dict(result)
