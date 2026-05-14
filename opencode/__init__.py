"""opencode hermes plugin

First-class tools for invoking opencode from Hermes with the discipline the
bare CLI doesn't enforce. Replaces the previous two-step pattern
(`terminal(background=true) + process(action="wait", timeout=3600)`) with a
single `opencode_run(prompt, workdir, ...)` tool call that returns structured
results, plus sibling tools for sessions / stats / diagnostics, plus a
pre_tool_call hook that blocks the known-bad shapes (bare `opencode run`,
`opencode pr` from headless, `opencode auth login`) and enforces the
background+timeout discipline on raw `oc` calls.

See DESIGN.md for the full research log, current scope, and roadmap.
"""

from __future__ import annotations

import logging

from . import schemas, tools

logger = logging.getLogger(__name__)


def register(ctx):
    """Register tools and the pre_tool_call hook."""

    # Primary: one-shot opencode invocation
    ctx.register_tool(
        name="opencode_run",
        toolset="opencode",
        schema=schemas.OPENCODE_RUN,
        handler=tools.make_opencode_run(ctx),
    )

    # Session management
    ctx.register_tool(
        name="opencode_session_list",
        toolset="opencode",
        schema=schemas.OPENCODE_SESSION_LIST,
        handler=tools.make_session_list(ctx),
    )
    ctx.register_tool(
        name="opencode_session_delete",
        toolset="opencode",
        schema=schemas.OPENCODE_SESSION_DELETE,
        handler=tools.make_session_delete(ctx),
    )
    ctx.register_tool(
        name="opencode_session_export",
        toolset="opencode",
        schema=schemas.OPENCODE_SESSION_EXPORT,
        handler=tools.make_session_export(ctx),
    )
    ctx.register_tool(
        name="opencode_session_import",
        toolset="opencode",
        schema=schemas.OPENCODE_SESSION_IMPORT,
        handler=tools.make_session_import(ctx),
    )

    # Telemetry + diagnostics
    ctx.register_tool(
        name="opencode_stats",
        toolset="opencode",
        schema=schemas.OPENCODE_STATS,
        handler=tools.make_stats(ctx),
    )
    ctx.register_tool(
        name="opencode_models",
        toolset="opencode",
        schema=schemas.OPENCODE_MODELS,
        handler=tools.make_models(ctx),
    )
    ctx.register_tool(
        name="opencode_debug_config",
        toolset="opencode",
        schema=schemas.OPENCODE_DEBUG_CONFIG,
        handler=tools.make_debug_config(ctx),
    )
    ctx.register_tool(
        name="opencode_version",
        toolset="opencode",
        schema=schemas.OPENCODE_VERSION,
        handler=tools.make_version(ctx),
    )

    # Enforcement: block bad opencode invocation shapes from any `terminal` call
    ctx.register_hook("pre_tool_call", tools.pre_tool_call_enforce)

    logger.info("[opencode plugin] registered 9 tools + 1 pre_tool_call hook")
