"""Diagnostic bundle export (proposal §6 "Diagnostic Bundle").

Produces a bounded ``.tar.gz`` containing manifest, current-run metadata,
event logs, summaries, canonical status, release info, and a redaction
report. Events are already allowlisted at record time; as defense in depth
every line written into a bundle passes through the shared secret rules
again, and the per-rule hit counts land in ``redaction-report.json``.

Never included: session snapshots, memory files, tool artifacts, prompts,
assistant text, tool output, keys, tokens, or environment dumps.
"""

from __future__ import annotations

import io
import json
import platform
import tarfile
import time
from pathlib import Path
from typing import Any

BUNDLE_FORMAT_VERSION = 1


def _scrub(text: str, counts: dict[str, int]) -> str:
    from openharness.memory.team import SECRET_RULES

    for rule_id, _label, pattern in SECRET_RULES:
        text, hits = pattern.subn(f"[redacted:{rule_id}]", text)
        if hits:
            counts[rule_id] = counts.get(rule_id, 0) + hits
    return text


def export_bundle(
    *,
    output: Path | None = None,
    since_seconds: float | None = 24 * 3600.0,
    include_stacks: bool = False,
) -> dict[str, Any]:
    """Build the bundle; returns ``{"path", "files", "redactions", "duration_ms"}``."""
    from openharness.cli import __version__
    from openharness.diagnostics import context as diag_context
    from openharness.diagnostics.recorder import get_recorder
    from openharness.diagnostics.snapshot import build_status, diagnostics_dir

    start = time.perf_counter()
    diag_dir = diagnostics_dir()
    include_logs = True
    try:
        from openharness.config import load_settings

        include_logs = bool(load_settings().diagnostics.export_include_logs)
    except Exception:
        pass

    if output is None:
        exports_dir = diag_dir / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        output = exports_dir / f"openharness-diagnostics-{stamp}.tar.gz"
    output.parent.mkdir(parents=True, exist_ok=True)

    # Flush so today's events are on disk before we read them back.
    get_recorder().flush()

    redaction_counts: dict[str, int] = {}
    members: list[tuple[str, str]] = []  # (arcname, content)

    cutoff_date = ""
    if since_seconds is not None:
        cutoff_date = time.strftime("%Y-%m-%d", time.gmtime(time.time() - since_seconds))

    current_run = diag_dir / "current-run.json"
    if current_run.exists():
        try:
            members.append(("current-run.json", _scrub(current_run.read_text(encoding="utf-8"), redaction_counts)))
        except OSError:
            pass

    if include_logs:
        for path in sorted((diag_dir / "events").glob("*.jsonl")):
            if cutoff_date and path.stem < cutoff_date:
                continue
            try:
                members.append((f"events/{path.name}", _scrub(path.read_text(encoding="utf-8"), redaction_counts)))
            except OSError:
                continue

    for path in sorted((diag_dir / "summaries").glob("*.json")):
        if cutoff_date and path.stem < cutoff_date:
            continue
        try:
            members.append((f"summaries/{path.name}", _scrub(path.read_text(encoding="utf-8"), redaction_counts)))
        except OSError:
            continue

    if include_stacks:
        for path in sorted((diag_dir / "stacks").glob("*.txt")):
            try:
                members.append((f"stacks/{path.name}", _scrub(path.read_text(encoding="utf-8"), redaction_counts)))
            except OSError:
                continue

    status = build_status(probe=False)
    members.append(("status.json", _scrub(json.dumps(status, ensure_ascii=False, indent=2), redaction_counts)))
    members.append(
        (
            "release-info.json",
            json.dumps(
                {
                    "version": __version__,
                    "python": platform.python_version(),
                    "platform": platform.platform(),
                },
                indent=2,
            ),
        )
    )
    members.append(
        (
            "redaction-report.json",
            json.dumps(
                {"rules": redaction_counts, "total": sum(redaction_counts.values())},
                indent=2,
            ),
        )
    )
    manifest = {
        "bundle_format_version": BUNDLE_FORMAT_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "openharness_version": __version__,
        "run_id": diag_context.run_id(),
        "since_seconds": since_seconds,
        "include_stacks": include_stacks,
        "event_logs_included": include_logs,
        "files": [name for name, _ in members] + ["manifest.json"],
    }
    members.insert(0, ("manifest.json", json.dumps(manifest, indent=2)))

    with tarfile.open(output, "w:gz") as archive:
        for arcname, content in members:
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=arcname)
            info.size = len(data)
            info.mtime = int(time.time())
            archive.addfile(info, io.BytesIO(data))

    duration_ms = (time.perf_counter() - start) * 1000.0
    from openharness.diagnostics import record

    record(
        "diagnostics",
        "export",
        "completed",
        duration_ms=duration_ms,
        attrs={"file": output.name},
        counters={"redactions": sum(redaction_counts.values())},
    )
    return {
        "path": str(output),
        "files": manifest["files"],
        "redactions": {"rules": redaction_counts, "total": sum(redaction_counts.values())},
        "duration_ms": round(duration_ms, 2),
    }
