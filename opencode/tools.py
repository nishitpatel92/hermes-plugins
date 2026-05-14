"""Handler functions for the opencode plugin.

These functions are wired up in `__init__.py:register()` via closures so they
have access to the plugin `ctx` (used to dispatch Hermes' `terminal` and
`process` tools — opencode itself lives inside a sandbox, not in the gateway).

Handler contract: receives `args: dict` and `**kwargs`; returns a JSON string.
Never raises — exceptions become JSON `{"status": "error", "error": ...}`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import time
from typing import Any

from . import formats

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# pre_tool_call enforcement
# ---------------------------------------------------------------------------

# Patterns evaluated in order against the first tokens of `command`.
_BARE_OPENCODE_RUN = re.compile(r"^\s*opencode\s+run\b")
_OPENCODE_PR = re.compile(r"^\s*opencode\s+pr\b")
_OPENCODE_AUTH_LOGIN = re.compile(r"^\s*opencode\s+auth\s+login\b")
_OC_WRAPPER = re.compile(r"^\s*oc(\s+|$)")
_OPENCODE_META = re.compile(
    r"^\s*opencode\s+(?:session(?:\s+(?:list|delete))?|stats|export|import|"
    r"models|debug|--version|-v|--help|-h|auth\s+(?:list|logout))\b"
)


def pre_tool_call_enforce(tool_name, args, task_id=None, **kwargs):
    """Block dangerous or broken opencode invocation shapes.

    Returns a block-dict to veto the call, or None to allow.
    """
    if tool_name != "terminal":
        return None

    command = (args.get("command") or "").lstrip()
    if not command:
        return None

    if _BARE_OPENCODE_RUN.search(command):
        return _block(
            "Bare `opencode run` hits Hermes' 600s foreground cap silently. "
            "Use the structured tool `opencode_run(prompt=..., workdir=..., ...)`, or fall back to "
            "`oc '<prompt>'` with background=true, timeout=3600 and a paired process(action='wait', timeout=3600)."
        )

    if _OPENCODE_PR.search(command):
        return _block(
            "`opencode pr <N>` launches the interactive TUI after `gh pr checkout`; it doesn't "
            "exit in headless mode and will hit the 3600s wrapper kill. For PR review headless: "
            "clone the PR into a tmp dir and call `opencode_run(prompt='review this diff', workdir=...)` "
            "with the diff attached via the `files` param."
        )

    if _OPENCODE_AUTH_LOGIN.search(command):
        return _block(
            "`opencode auth login <url>` executes an arbitrary command declared in "
            "`<url>/.well-known/opencode` — an RCE vector. Auth is configured at image-bake "
            "time and credentials are bind-mounted into the sandbox; this command should not "
            "run from an agent context."
        )

    if _OC_WRAPPER.search(command):
        if args.get("background") is not True:
            return _block(
                "`oc` runs need background=true so they survive the 600s foreground cap. "
                "Set background=true and timeout=3600, then process(action='wait', ...). "
                "Or just call `opencode_run(...)` which handles all this."
            )
        timeout = args.get("timeout")
        if not isinstance(timeout, (int, float)) or timeout < 3600:
            return _block(
                "`oc` wraps `opencode run` in a 3600s kernel timeout. The terminal call's "
                "timeout must be ≥ 3600 to actually wait for completion. Set timeout=3600."
            )
        if not args.get("workdir"):
            logger.warning("[opencode plugin] oc invocation without explicit workdir (task %s)", task_id)
        return None

    # opencode meta commands (auth list, session list, stats, etc.) — allow without enforcement
    if _OPENCODE_META.search(command):
        return None

    # Other opencode subcommands (serve, web, acp, attach) — warn-only
    if command.lstrip().startswith("opencode"):
        logger.warning("[opencode plugin] unrecognized opencode invocation, allowing: %s", command[:80])
    return None


def _block(message: str) -> dict:
    return {"action": "block", "message": message}


# ---------------------------------------------------------------------------
# Tool handler factories (closure over ctx for dispatch_tool access)
# ---------------------------------------------------------------------------


def make_opencode_run(ctx):
    def handler(args: dict, **_kwargs) -> str:
        return _err_wrap("opencode_run", lambda: _opencode_run(ctx, args))
    return handler


def make_session_list(ctx):
    def handler(args: dict, **_kwargs) -> str:
        return _err_wrap("opencode_session_list", lambda: _session_list(ctx, args))
    return handler


def make_session_delete(ctx):
    def handler(args: dict, **_kwargs) -> str:
        return _err_wrap("opencode_session_delete", lambda: _session_delete(ctx, args))
    return handler


def make_session_export(ctx):
    def handler(args: dict, **_kwargs) -> str:
        return _err_wrap("opencode_session_export", lambda: _session_export(ctx, args))
    return handler


def make_session_import(ctx):
    def handler(args: dict, **_kwargs) -> str:
        return _err_wrap("opencode_session_import", lambda: _session_import(ctx, args))
    return handler


def make_stats(ctx):
    def handler(args: dict, **_kwargs) -> str:
        return _err_wrap("opencode_stats", lambda: _stats(ctx, args))
    return handler


def make_models(ctx):
    def handler(args: dict, **_kwargs) -> str:
        return _err_wrap("opencode_models", lambda: _models(ctx, args))
    return handler


def make_debug_config(ctx):
    def handler(args: dict, **_kwargs) -> str:
        return _err_wrap("opencode_debug_config", lambda: _debug_config(ctx, args))
    return handler


def make_version(ctx):
    def handler(args: dict, **_kwargs) -> str:
        return _err_wrap("opencode_version", lambda: _version(ctx, args))
    return handler


def _err_wrap(tool_label: str, fn):
    """Catch handler errors and return JSON; never raise to Hermes."""
    try:
        result = fn()
        if isinstance(result, str):
            return result
        return json.dumps(result)
    except Exception as e:
        logger.exception("[opencode plugin] %s failed", tool_label)
        return json.dumps({"status": "error", "error": f"{type(e).__name__}: {e}"})


# ---------------------------------------------------------------------------
# Hermes tool dispatch helpers
# ---------------------------------------------------------------------------


def _dispatch(ctx, tool_name: str, args: dict) -> dict:
    """Call a Hermes tool via ctx.dispatch_tool, return its parsed result.

    dispatch_tool's return type varies — may be a dict, may be a JSON string,
    may be a string with text — we normalize.
    """
    raw = ctx.dispatch_tool(tool_name, args)
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"output": raw}
    return {"raw": repr(raw)}


def _shell_run(ctx, command: str, workdir: str, timeout: int = 30) -> dict:
    """Foreground shell command via Hermes' terminal tool. For fast meta-commands."""
    return _dispatch(ctx, "terminal", {
        "command": command,
        "workdir": workdir,
        "timeout": timeout,
        "background": False,
    })


# ---------------------------------------------------------------------------
# opencode_run — the primary tool
# ---------------------------------------------------------------------------


def _opencode_run(ctx, args: dict) -> dict:
    prompt = args.get("prompt") or ""
    workdir = args.get("workdir") or ""
    slash_command = args.get("slash_command")

    if not workdir:
        return {"status": "error", "error": "workdir is required"}
    if not prompt and not slash_command and not args.get("files"):
        return {"status": "error", "error": "prompt is required (or use slash_command / files)"}

    # Validate file attachments exist upfront for clearer errors than opencode's exit 1.
    files = args.get("files") or []
    missing = [f for f in files if not os.path.isabs(f) and not _exists_in_workdir(workdir, f)
               or os.path.isabs(f) and not os.path.exists(f)]
    if missing:
        return {"status": "error", "error": f"file attachments not found: {missing}"}

    # Build the oc invocation
    cmd_parts: list[str] = ["oc"]

    if slash_command:
        cmd_parts += ["--command", shlex.quote(slash_command)]

    cmd_parts += ["--dir", shlex.quote(workdir)]

    if args.get("agent"):
        cmd_parts += ["--agent", shlex.quote(args["agent"])]
    if args.get("model"):
        cmd_parts += ["--model", shlex.quote(args["model"])]
    if args.get("variant"):
        cmd_parts += ["--variant", shlex.quote(args["variant"])]
    if args.get("thinking"):
        cmd_parts.append("--thinking")
    if "title" in args and args["title"] is not None:
        cmd_parts += ["--title", shlex.quote(args["title"])]
    if args.get("share"):
        cmd_parts.append("--share")

    # Session continuation
    if args.get("session_id"):
        cmd_parts += ["-s", shlex.quote(args["session_id"])]
    elif args.get("continue_session"):
        cmd_parts.append("--continue")
    if args.get("fork"):
        if not (args.get("session_id") or args.get("continue_session")):
            return {"status": "error", "error": "fork requires continue_session or session_id"}
        cmd_parts.append("--fork")

    fmt = args.get("format", "default")
    if fmt == "json":
        cmd_parts += ["--format", "json"]

    if args.get("attach_url"):
        cmd_parts += ["--attach", shlex.quote(args["attach_url"])]

    # Permissions default: skip in headless so the agent can actually do work
    if args.get("dangerously_skip_permissions", True):
        cmd_parts.append("--dangerously-skip-permissions")

    for f in files:
        cmd_parts += ["-f", shlex.quote(f)]

    # Positional prompt last (slash_command makes this $ARGUMENTS instead)
    if prompt:
        cmd_parts.append(shlex.quote(prompt))

    for extra in args.get("extra_args") or []:
        cmd_parts.append(shlex.quote(str(extra)))

    command = " ".join(cmd_parts)
    logger.info("[opencode plugin] dispatching: %s", _truncate_for_log(command))

    # Run foreground. `oc` itself wraps in `timeout --kill-after=10s 3600`,
    # so the kernel-level ceiling is already enforced; we just need the
    # Hermes terminal call to wait long enough for it.
    #
    # We deliberately do NOT use background=true + process(action="wait").
    # That pattern was the original plan to dodge the default 600s
    # TERMINAL_MAX_FOREGROUND_TIMEOUT cap, but it triggers a bug in Hermes'
    # persistent-shell process registry: the spawn returns "Background
    # process started" synchronously, but the wrapper that should write
    # the exit-code file never runs, so process(wait) reports exit_code=-1
    # with empty output a few seconds later. Foreground works correctly.
    #
    # Required env (set in hermes' compose + config.yaml + .env):
    #   TERMINAL_MAX_FOREGROUND_TIMEOUT=3600
    #   TERMINAL_TIMEOUT=3600
    #   TERMINAL_LIFETIME_SECONDS=3600
    # See compose_files/hermes/CLAUDE.md invariant 9.
    started_at = time.monotonic()
    result = _dispatch(ctx, "terminal", {
        "command": command,
        "workdir": workdir,
        "timeout": 3600,
    })

    duration_ms = int((time.monotonic() - started_at) * 1000)
    exit_code = result.get("exit_code")
    output = result.get("output") or ""
    session_id_terminal = (
        result.get("session_id") or result.get("sessionId") or ""
    )

    status, exit_meaning = formats.decode_exit_code(exit_code)

    result: dict[str, Any] = {
        "status": status,
        "exit_code": exit_code,
        "exit_meaning": exit_meaning,
        "duration_ms": duration_ms,
        "session_id_terminal": session_id_terminal,
        "session_id_opencode": formats.extract_session_id(output),
        "output_tail": formats.tail_output(output),
    }

    # Diff summary on success — orchestrator usually wants to know what changed
    if status == "ok" and args.get("include_diff_summary", True):
        diff = formats.summarize_diff(workdir)
        result["files_changed"] = diff["files_changed"]
        result["diff_stat"] = diff["diff_stat"]
        if diff["error"]:
            result["diff_summary_error"] = diff["error"]

    # Optional event-stream parsing
    if fmt == "json" and args.get("parse_events"):
        parsed = formats.parse_event_stream(output)
        result["events"] = parsed["events"]
        result["events_truncated"] = parsed["truncated"]

    return result


def _exists_in_workdir(workdir: str, rel: str) -> bool:
    try:
        return os.path.exists(os.path.join(workdir, rel))
    except OSError:
        return False


def _truncate_for_log(s: str, n: int = 160) -> str:
    return s if len(s) <= n else s[:n] + f"…({len(s) - n} more)"


# ---------------------------------------------------------------------------
# Session / stats / diagnostic tools
# ---------------------------------------------------------------------------


def _session_list(ctx, args: dict) -> dict:
    workdir = _require(args, "workdir")
    max_count = args.get("max_count") or 20
    cmd = f"opencode session list --format json -n {int(max_count)}"
    out = _shell_run(ctx, cmd, workdir, timeout=30)
    text = (out.get("output") or "").strip()
    try:
        sessions = json.loads(text) if text else []
    except json.JSONDecodeError:
        return {"status": "error", "error": "session list returned non-JSON output",
                "output_tail": formats.tail_output(text)}
    return {"status": "ok", "exit_code": out.get("exit_code"), "sessions": sessions, "count": len(sessions)}


def _session_delete(ctx, args: dict) -> dict:
    workdir = _require(args, "workdir")
    session_id = _require(args, "session_id")
    cmd = f"opencode session delete {shlex.quote(session_id)}"
    out = _shell_run(ctx, cmd, workdir, timeout=15)
    if out.get("exit_code") == 0:
        return {"status": "ok", "deleted": True, "session_id": session_id}
    return {"status": "error", "exit_code": out.get("exit_code"),
            "output_tail": formats.tail_output(out.get("output") or "")}


def _session_export(ctx, args: dict) -> dict:
    workdir = _require(args, "workdir")
    parts = ["opencode", "export"]
    if args.get("session_id"):
        parts.append(shlex.quote(args["session_id"]))
    if args.get("sanitize"):
        parts.append("--sanitize")
    out = _shell_run(ctx, " ".join(parts), workdir, timeout=60)
    text = (out.get("output") or "").strip()
    try:
        return {"status": "ok", "exit_code": out.get("exit_code"), "export": json.loads(text)}
    except json.JSONDecodeError:
        return {"status": "error", "error": "export did not produce JSON",
                "output_tail": formats.tail_output(text)}


def _session_import(ctx, args: dict) -> dict:
    workdir = _require(args, "workdir")
    source = _require(args, "source")
    cmd = f"opencode import {shlex.quote(source)}"
    out = _shell_run(ctx, cmd, workdir, timeout=60)
    text = out.get("output") or ""
    session_id = formats.extract_session_id(text)
    if out.get("exit_code") == 0 and session_id:
        return {"status": "ok", "session_id": session_id}
    return {"status": "error", "exit_code": out.get("exit_code"),
            "output_tail": formats.tail_output(text)}


def _stats(ctx, args: dict) -> dict:
    workdir = _require(args, "workdir")
    parts = ["opencode", "stats"]
    if args.get("days") is not None:
        parts += ["--days", str(int(args["days"]))]
    if args.get("top_tools") is not None:
        parts += ["--tools", str(int(args["top_tools"]))]
    if args.get("top_models") is not None:
        parts += ["--models", str(int(args["top_models"]))]
    if args.get("project") is not None:
        parts += ["--project", shlex.quote(args["project"])]
    out = _shell_run(ctx, " ".join(parts), workdir, timeout=30)
    return {"status": "ok" if out.get("exit_code") == 0 else "error",
            "exit_code": out.get("exit_code"),
            "output": out.get("output") or ""}


def _models(ctx, args: dict) -> dict:
    workdir = _require(args, "workdir")
    parts = ["opencode", "models"]
    if args.get("provider"):
        parts.append(shlex.quote(args["provider"]))
    if args.get("verbose"):
        parts.append("--verbose")
    if args.get("refresh"):
        parts.append("--refresh")
    out = _shell_run(ctx, " ".join(parts), workdir, timeout=30)
    return {"status": "ok" if out.get("exit_code") == 0 else "error",
            "exit_code": out.get("exit_code"),
            "output": out.get("output") or ""}


def _debug_config(ctx, args: dict) -> dict:
    workdir = _require(args, "workdir")
    out = _shell_run(ctx, "opencode debug config", workdir, timeout=15)
    text = (out.get("output") or "").strip()
    try:
        return {"status": "ok", "config": json.loads(text)}
    except json.JSONDecodeError:
        return {"status": "error", "error": "debug config did not produce JSON",
                "output_tail": formats.tail_output(text)}


def _version(ctx, args: dict) -> dict:
    workdir = _require(args, "workdir")
    out = _shell_run(ctx, "opencode --version", workdir, timeout=10)
    version = (out.get("output") or "").strip().splitlines()[-1] if out.get("output") else ""
    return {"status": "ok" if out.get("exit_code") == 0 else "error",
            "version": version}


def _require(args: dict, key: str):
    val = args.get(key)
    if val is None or val == "":
        raise ValueError(f"missing required arg: {key}")
    return val
