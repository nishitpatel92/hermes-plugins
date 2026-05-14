"""Tool schemas — what the LLM sees when picking opencode tools."""


OPENCODE_RUN = {
    "name": "opencode_run",
    "description": (
        "Run opencode headless for a one-shot coding task. Wraps `oc` (`timeout 3600 opencode "
        "run …`) with background execution and waits up to one hour for completion. Returns a "
        "structured result: status, exit code, opencode session id, files changed, diff stat, "
        "and the output tail. Replaces the previous two-step pattern (terminal + process(wait)) "
        "with a single tool call. Default permission policy auto-approves opencode's permission "
        "requests so build agents can actually edit/run — flip dangerously_skip_permissions to "
        "false only for read-only plan-mode runs."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The task for opencode. Passed as the positional message to `opencode run`.",
            },
            "workdir": {
                "type": "string",
                "description": "Absolute path to opencode's working directory. Used for both `--dir` and the shell cwd. Should be a git repository so diff tracking works.",
            },
            "agent": {
                "type": "string",
                "description": "opencode agent name (e.g. 'build', 'plan', 'scout'). Falls back to the configured default if omitted.",
            },
            "model": {
                "type": "string",
                "description": "Model override as 'provider/model' (e.g. 'openai/gpt-5.3-codex', 'whitebox/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf').",
            },
            "variant": {
                "type": "string",
                "description": "Reasoning-effort hint passed to the provider (e.g. 'minimal', 'high', 'max'). Provider-specific; ignored by providers that don't support it.",
            },
            "thinking": {
                "type": "boolean",
                "description": "Surface model reasoning/thinking blocks in opencode's output. Defaults to false in headless mode.",
            },
            "title": {
                "type": "string",
                "description": "Session title. Pass empty string to derive from the first 50 chars of the prompt.",
            },
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Files (or directories) to attach to the prompt. Resolved relative to workdir. Missing paths produce a clear error before opencode is invoked.",
            },
            "continue_session": {
                "type": "boolean",
                "description": "Resume the most recent root session in this sandbox. Mutually exclusive with session_id.",
            },
            "session_id": {
                "type": "string",
                "description": "Resume a specific session by id (ses_...). Mutually exclusive with continue_session.",
            },
            "fork": {
                "type": "boolean",
                "description": "Fork the resumed session into a new sibling. Requires continue_session or session_id.",
            },
            "share": {
                "type": "boolean",
                "description": "Force-share the session regardless of opencode.json's share setting.",
            },
            "slash_command": {
                "type": "string",
                "description": "Invoke a user-defined slash command from opencode.json (e.g. '/review'). The prompt becomes the command's $ARGUMENTS.",
            },
            "format": {
                "type": "string",
                "enum": ["default", "json"],
                "description": "'default' for human-readable output, 'json' for opencode's ndjson event stream (tool_use/text/reasoning/error events).",
            },
            "parse_events": {
                "type": "boolean",
                "description": "If format='json', parse the event stream into a structured array in the result (capped at 500 events).",
            },
            "attach_url": {
                "type": "string",
                "description": "URL of a running `opencode serve` to attach to. When set, runs against the remote server instead of an in-process worker.",
            },
            "dangerously_skip_permissions": {
                "type": "boolean",
                "description": "Auto-approve every opencode permission request. REQUIRED for autonomous build agents — opencode auto-rejects permission asks in headless mode otherwise, which silently breaks edits/bash/grep. Defaults to true.",
            },
            "include_diff_summary": {
                "type": "boolean",
                "description": "Append `git status --short` + `git diff --stat` to the result after a successful run so you can see what changed without an extra round trip. Defaults to true.",
            },
            "extra_args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Additional raw arguments appended to the opencode invocation. Escape hatch for flags this tool doesn't expose by name.",
            },
        },
        "required": ["prompt", "workdir"],
    },
}


OPENCODE_SESSION_LIST = {
    "name": "opencode_session_list",
    "description": "List recent opencode sessions in the sandbox. Always uses --format json so the pager doesn't grab stdin.",
    "parameters": {
        "type": "object",
        "properties": {
            "workdir": {"type": "string", "description": "Working directory (project context)."},
            "max_count": {"type": "integer", "description": "Maximum number of sessions to return. Default 20."},
        },
        "required": ["workdir"],
    },
}


OPENCODE_SESSION_DELETE = {
    "name": "opencode_session_delete",
    "description": "Delete an opencode session by id.",
    "parameters": {
        "type": "object",
        "properties": {
            "workdir": {"type": "string", "description": "Working directory."},
            "session_id": {"type": "string", "description": "Session id to delete (ses_...)."},
        },
        "required": ["workdir", "session_id"],
    },
}


OPENCODE_SESSION_EXPORT = {
    "name": "opencode_session_export",
    "description": "Export an opencode session's transcript as JSON. Optional sanitize redacts message text and filenames.",
    "parameters": {
        "type": "object",
        "properties": {
            "workdir": {"type": "string", "description": "Working directory."},
            "session_id": {"type": "string", "description": "Session id to export. If omitted, exports the latest."},
            "sanitize": {"type": "boolean", "description": "Redact transcript text and filenames before returning."},
        },
        "required": ["workdir"],
    },
}


OPENCODE_SESSION_IMPORT = {
    "name": "opencode_session_import",
    "description": "Import an opencode session from a file path or a share URL (https://opncd.ai/s/<slug>).",
    "parameters": {
        "type": "object",
        "properties": {
            "workdir": {"type": "string", "description": "Working directory."},
            "source": {"type": "string", "description": "Local file path or share URL."},
        },
        "required": ["workdir", "source"],
    },
}


OPENCODE_STATS = {
    "name": "opencode_stats",
    "description": "Token/cost telemetry from opencode. Filterable by recency, top tools/models, and project.",
    "parameters": {
        "type": "object",
        "properties": {
            "workdir": {"type": "string", "description": "Working directory."},
            "days": {"type": "integer", "description": "Window in days. 0 = since midnight today. Omit for all-time."},
            "top_tools": {"type": "integer", "description": "Top N tools to surface."},
            "top_models": {"type": "integer", "description": "Top N models to surface."},
            "project": {"type": "string", "description": "Project filter. Empty string = current project; omitted = all."},
        },
        "required": ["workdir"],
    },
}


OPENCODE_MODELS = {
    "name": "opencode_models",
    "description": "List models opencode can route to. Optionally filter by provider.",
    "parameters": {
        "type": "object",
        "properties": {
            "workdir": {"type": "string", "description": "Working directory."},
            "provider": {"type": "string", "description": "Provider id (e.g. 'openai', 'whitebox')."},
            "verbose": {"type": "boolean", "description": "Include per-model metadata (context window, cost)."},
            "refresh": {"type": "boolean", "description": "Refresh the models.dev cache."},
        },
        "required": ["workdir"],
    },
}


OPENCODE_DEBUG_CONFIG = {
    "name": "opencode_debug_config",
    "description": "Dump opencode's resolved config (merged from global, project, env). Diagnostic.",
    "parameters": {
        "type": "object",
        "properties": {
            "workdir": {"type": "string", "description": "Working directory."},
        },
        "required": ["workdir"],
    },
}


OPENCODE_VERSION = {
    "name": "opencode_version",
    "description": "Report the installed opencode version.",
    "parameters": {
        "type": "object",
        "properties": {
            "workdir": {"type": "string", "description": "Working directory."},
        },
        "required": ["workdir"],
    },
}
