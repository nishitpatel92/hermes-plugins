"""host-logs hermes plugin

Exposes two read-only tools:

- ``host_logs(container, tail=200, stdout=True, stderr=True, since=0)`` —
  fetch recent stdout/stderr from a container running on the homelab host.
- ``host_containers()`` — list host containers (name, image, state, status).

Both call a docker-socket-proxy on the same docker network as hermes
(default ``http://socket-proxy:2375``). The proxy is configured for
read-only operations (CONTAINERS=1, LOGS=1, INFO=1, …; POST=0), so even a
fully compromised hermes can only list/inspect/log — not start, stop, exec,
or destroy host containers. See the plugin README for proxy setup.

Override the proxy URL via the HERMES_HOST_LOGS_PROXY env var if you run
the proxy at a different address.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

PROXY_URL = os.environ.get(
    "HERMES_HOST_LOGS_PROXY", "http://socket-proxy:2375"
).rstrip("/")
DEFAULT_TAIL = 200
MAX_RESULT_BYTES = 100_000  # cap returned log text to avoid blowing the agent's context
HTTP_TIMEOUT = 15


# ---------------------------------------------------------------------------
# Docker logs frame demuxer
# ---------------------------------------------------------------------------

def _demux(raw: bytes) -> str:
    """Demultiplex docker logs API output (frame format).

    For containers without TTY, the daemon emits a multiplexed stream where
    each frame is an 8-byte header + payload:
      header = [stream(1), 0, 0, 0, size(big-endian uint32, 4 bytes)]
    stream: 0=stdin, 1=stdout, 2=stderr.
    """
    chunks: list[str] = []
    i = 0
    n = len(raw)
    while i + 8 <= n:
        header = raw[i : i + 8]
        size = int.from_bytes(header[4:8], "big")
        if size == 0 or i + 8 + size > n:
            # Either a degenerate frame or the buffer is truncated mid-frame.
            # Decode whatever's left as raw text — better than dropping it.
            break
        payload = raw[i + 8 : i + 8 + size]
        chunks.append(payload.decode("utf-8", errors="replace"))
        i += 8 + size
    if not chunks:
        # The daemon may also emit non-multiplexed text for TTY containers.
        return raw.decode("utf-8", errors="replace")
    return "".join(chunks)


# ---------------------------------------------------------------------------
# Proxy calls
# ---------------------------------------------------------------------------

def _http_get(path: str, *, query: dict | None = None, max_bytes: int | None = None) -> bytes:
    qs = "?" + urllib.parse.urlencode(query) if query else ""
    url = f"{PROXY_URL}{path}{qs}"
    with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as resp:
        if max_bytes is not None:
            return resp.read(max_bytes + 16)
        return resp.read()


def _fetch_logs(container: str, *, tail: int, stdout: bool, stderr: bool, since: int) -> str:
    raw = _http_get(
        f"/containers/{urllib.parse.quote(container, safe='')}/logs",
        query={
            "stdout": "1" if stdout else "0",
            "stderr": "1" if stderr else "0",
            "tail": str(tail),
            "since": str(since),
            "follow": "0",
            "timestamps": "0",
        },
        max_bytes=MAX_RESULT_BYTES,
    )
    text = _demux(raw)
    if len(text) > MAX_RESULT_BYTES:
        text = text[:MAX_RESULT_BYTES] + f"\n…(truncated to {MAX_RESULT_BYTES} bytes)"
    return text


def _list_containers(all: bool) -> list[dict[str, Any]]:
    raw = _http_get("/containers/json", query={"all": "1" if all else "0"})
    return json.loads(raw.decode())


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def _err(msg: str, **extra: Any) -> str:
    return json.dumps({"success": False, "error": msg, **extra})


def _handle_host_logs(params: dict[str, Any], **_: Any) -> str:
    container = (params.get("container") or "").strip()
    if not container:
        return _err("`container` is required")
    try:
        tail = int(params.get("tail", DEFAULT_TAIL))
    except (TypeError, ValueError):
        return _err("`tail` must be an integer")
    stdout = bool(params.get("stdout", True))
    stderr = bool(params.get("stderr", True))
    if not (stdout or stderr):
        return _err("at least one of `stdout` or `stderr` must be true")
    try:
        since = int(params.get("since", 0))
    except (TypeError, ValueError):
        return _err("`since` must be an integer (seconds ago)")

    try:
        text = _fetch_logs(
            container, tail=tail, stdout=stdout, stderr=stderr, since=since
        )
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return _err(
                f"container '{container}' not found on the host. "
                "Use the `host_containers` tool to list available names."
            )
        return _err(f"HTTP {e.code} {e.reason}")
    except urllib.error.URLError as e:
        return _err(
            f"network: {e.reason}. "
            "Is the docker-socket-proxy reachable at "
            f"{PROXY_URL}?"
        )
    except Exception as e:  # pragma: no cover
        logger.exception("host_logs failure")
        return _err(f"{type(e).__name__}: {e}")

    return json.dumps(
        {
            "success": True,
            "container": container,
            "tail": tail,
            "stdout": stdout,
            "stderr": stderr,
            "since": since,
            "logs": text,
        }
    )


def _handle_host_containers(params: dict[str, Any], **_: Any) -> str:
    include_stopped = bool(params.get("all", False))
    try:
        cs = _list_containers(all=include_stopped)
    except urllib.error.HTTPError as e:
        return _err(f"HTTP {e.code} {e.reason}")
    except urllib.error.URLError as e:
        return _err(
            f"network: {e.reason}. "
            f"Is the docker-socket-proxy reachable at {PROXY_URL}?"
        )
    except Exception as e:  # pragma: no cover
        logger.exception("host_containers failure")
        return _err(f"{type(e).__name__}: {e}")

    simplified = [
        {
            "name": (c.get("Names") or ["?"])[0].lstrip("/"),
            "image": c.get("Image"),
            "state": c.get("State"),
            "status": c.get("Status"),
        }
        for c in cs
    ]
    return json.dumps({"success": True, "containers": simplified})


# ---------------------------------------------------------------------------
# Plugin entry
# ---------------------------------------------------------------------------

LOGS_SCHEMA = {
    "name": "host_logs",
    "description": (
        "Read recent stdout/stderr from a container running on the homelab "
        "host (NOT a sandbox container). Use this when debugging host-side "
        "services like worklane-api, n8n, caddy, hermes itself, etc. "
        "Read-only — cannot start/stop/exec the container. The agent should "
        "call `host_containers` first to discover valid container names."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "container": {
                "type": "string",
                "description": (
                    "Container name (e.g. 'worklane-api', 'caddy'). Run the "
                    "`host_containers` tool to list valid names."
                ),
            },
            "tail": {
                "type": "integer",
                "description": (
                    f"Number of trailing lines to return. Default {DEFAULT_TAIL}. "
                    "Larger tails are capped to ~100KB of returned text."
                ),
                "default": DEFAULT_TAIL,
            },
            "stdout": {
                "type": "boolean",
                "description": "Include stdout. Default true.",
                "default": True,
            },
            "stderr": {
                "type": "boolean",
                "description": "Include stderr. Default true.",
                "default": True,
            },
            "since": {
                "type": "integer",
                "description": (
                    "Only show logs newer than N seconds ago. "
                    "Default 0 = no time filter (use with `tail` instead)."
                ),
                "default": 0,
            },
        },
        "required": ["container"],
    },
}

CONTAINERS_SCHEMA = {
    "name": "host_containers",
    "description": (
        "List containers running on the homelab host. Use to discover the "
        "container name for `host_logs`. Returns name, image, state, status."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "all": {
                "type": "boolean",
                "description": (
                    "Include stopped/exited containers too. "
                    "Default false (only running)."
                ),
                "default": False,
            }
        },
    },
}


def register(ctx: Any) -> None:
    ctx.register_tool(
        name="host_logs",
        toolset="host_logs",
        schema=LOGS_SCHEMA,
        handler=_handle_host_logs,
        description=(
            "Read recent logs from a homelab host container "
            "(read-only via docker-socket-proxy)."
        ),
    )
    ctx.register_tool(
        name="host_containers",
        toolset="host_logs",
        schema=CONTAINERS_SCHEMA,
        handler=_handle_host_containers,
        description="List containers on the homelab host (read-only).",
    )
    logger.info("host-logs plugin registered (proxy=%s)", PROXY_URL)
