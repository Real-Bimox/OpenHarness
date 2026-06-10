"""CLAUDE.md discovery and loading."""

from __future__ import annotations

from pathlib import Path


def discover_claude_md_files(cwd: str | Path) -> list[Path]:
    """Discover relevant CLAUDE.md instruction files from the cwd upward."""
    current = Path(cwd).resolve()
    results: list[Path] = []
    seen: set[Path] = set()

    for directory in [current, *current.parents]:
        for candidate in (
            directory / "CLAUDE.md",
            directory / ".claude" / "CLAUDE.md",
        ):
            if candidate.exists() and candidate not in seen:
                results.append(candidate)
                seen.add(candidate)

        rules_dir = directory / ".claude" / "rules"
        if rules_dir.is_dir():
            for rule in sorted(rules_dir.glob("*.md")):
                if rule not in seen:
                    results.append(rule)
                    seen.add(rule)

        if directory.parent == directory:
            break

    return results


# Assembled prompt sections keyed on (cwd, limit); valid while the stat
# fingerprint of the discovered files matches. Discovery itself stays live
# (it is stat-only), so new/removed files are picked up immediately.
_CLAUDE_MD_CACHE: dict[tuple[str, int], tuple[tuple, str | None]] = {}


def load_claude_md_prompt(cwd: str | Path, *, max_chars_per_file: int = 12000) -> str | None:
    """Load discovered instruction files into one prompt section."""
    files = discover_claude_md_files(cwd)
    if not files:
        return None

    fingerprint_parts: list[tuple[str, int, int]] = []
    for path in files:
        try:
            stat = path.stat()
            fingerprint_parts.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            fingerprint_parts.append((str(path), -1, -1))
    fingerprint = tuple(fingerprint_parts)
    key = (str(Path(cwd).resolve()), max_chars_per_file)
    cached = _CLAUDE_MD_CACHE.get(key)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]

    lines = ["# Project Instructions"]
    for path in files:
        content = path.read_text(encoding="utf-8", errors="replace")
        if len(content) > max_chars_per_file:
            content = content[:max_chars_per_file] + "\n...[truncated]..."
        lines.extend(["", f"## {path}", "```md", content.strip(), "```"])
    result = "\n".join(lines)
    if len(_CLAUDE_MD_CACHE) > 16:
        _CLAUDE_MD_CACHE.clear()
    _CLAUDE_MD_CACHE[key] = (fingerprint, result)
    return result
