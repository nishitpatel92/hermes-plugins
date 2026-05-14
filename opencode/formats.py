"""Output post-processing for opencode tool results.

- `decode_exit_code` — turns a wrapper/kernel exit code into human-readable meaning
- `summarize_diff` — runs `git status --short` + `git diff --stat` in a workdir
- `parse_event_stream` — extracts a structured event list from opencode's --format json output
- `extract_session_id` — pulls the ses_… id from opencode's output (best-effort)
- `tail_output` — last N bytes of a stream, with truncation marker
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any

# opencode prints lines like "session: ses_abc..." or includes the id in JSON events
_SESSION_RE = re.compile(r"\bses_[A-Za-z0-9]{20,40}\b")
_MAX_EVENTS = 500
_OUTPUT_TAIL_BYTES = 4096


def decode_exit_code(exit_code: int | None) -> tuple[str, str]:
    """Return (status, human-readable meaning) for an exit code.

    Status is one of: ok, error, timeout, killed, unknown.
    """
    if exit_code == 0:
        return "ok", "completed cleanly"
    if exit_code == 1:
        return "error", "opencode reported an error; check output_tail"
    if exit_code == 124:
        return "timeout", (
            "hit the 3600s kernel timeout in the `oc` wrapper; the task is too big for one run — "
            "split it, or use --agent plan to scope it first"
        )
    if exit_code == 137:
        return "killed", (
            "SIGKILL from outside the wrapper (sandbox reaper, OOM, manual kill); environment "
            "issue, not opencode's fault"
        )
    if exit_code is None:
        return "unknown", "no exit code reported"
    return "error", f"unexpected exit code {exit_code}"


def summarize_diff(workdir: str) -> dict[str, Any]:
    """Run `git status --short` + `git diff --stat` in workdir.

    Returns {files_changed: [...], diff_stat: "...", error: None|str}.
    Failures (not a git repo, git missing) become a non-fatal error string;
    the caller decides whether to surface it.
    """
    if not shutil.which("git"):
        return {"files_changed": [], "diff_stat": "", "error": "git not in PATH"}

    try:
        status = subprocess.run(
            ["git", "-C", workdir, "status", "--short"],
            capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        return {"files_changed": [], "diff_stat": "", "error": "git status timed out"}

    if status.returncode != 0:
        # Not a git repo, or other git failure — return a short note instead of stack
        msg = (status.stderr or status.stdout or "").strip().splitlines()
        return {"files_changed": [], "diff_stat": "", "error": msg[0] if msg else "git status failed"}

    files_changed = []
    for line in status.stdout.splitlines():
        # `git status --short` format: "XY path" where XY is 2-char state
        if len(line) >= 4:
            files_changed.append(line[3:].strip())

    try:
        stat = subprocess.run(
            ["git", "-C", workdir, "diff", "--stat"],
            capture_output=True, text=True, timeout=10,
        )
        diff_stat = stat.stdout if stat.returncode == 0 else ""
    except subprocess.TimeoutExpired:
        diff_stat = ""

    return {"files_changed": files_changed, "diff_stat": diff_stat, "error": None}


def parse_event_stream(raw_output: str, max_events: int = _MAX_EVENTS) -> dict[str, Any]:
    """Parse opencode's --format json output into a structured event list.

    Each non-empty line is expected to be one JSON object with at least `type`.
    Returns {events: [...], truncated: bool, error: None|str}. Drops step_start /
    step_finish events by default (they're high-volume noise); keeps tool_use,
    text, reasoning, error.
    """
    keep_types = {"tool_use", "text", "reasoning", "error"}
    events: list[dict] = []
    truncated = False

    for line in raw_output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            # Non-JSON lines mixed into the stream (e.g. early human output before --format kicks in)
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("type") not in keep_types:
            continue
        events.append(ev)
        if len(events) >= max_events:
            truncated = True
            break

    return {"events": events, "truncated": truncated, "error": None}


def extract_session_id(output: str) -> str | None:
    """Best-effort pull of the first ses_… id from opencode's output."""
    match = _SESSION_RE.search(output)
    return match.group(0) if match else None


def tail_output(output: str, max_bytes: int = _OUTPUT_TAIL_BYTES) -> str:
    """Return the last ~N bytes of output, with a truncation marker if cut."""
    if not output:
        return ""
    raw = output.encode("utf-8", errors="replace")
    if len(raw) <= max_bytes:
        return output
    tail = raw[-max_bytes:].decode("utf-8", errors="replace")
    return f"[…truncated {len(raw) - max_bytes} bytes…]\n{tail}"
