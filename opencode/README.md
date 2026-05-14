# opencode hermes plugin

First-class tools for invoking [opencode](https://opencode.ai) from Hermes — `opencode_run` plus session/stats/diagnostic siblings — with a `pre_tool_call` hook that enforces the `oc`-wrapper discipline.

## Why this exists

Hermes can already run opencode through a raw `terminal()` + `process(action="wait")` pair, but the call has to be shaped precisely:

- The command must go through the `oc` wrapper (`timeout --kill-after=10s 3600 opencode run "$@"`), never bare `opencode run`.
- `background=true` and `timeout=3600` are mandatory — anything less and Hermes' 600s foreground cap (`TERMINAL_MAX_FOREGROUND_TIMEOUT`) silently truncates the run.
- `opencode pr <N>` launches an interactive TUI and never exits in headless mode.
- `opencode auth login <url>` executes an arbitrary command from `<url>/.well-known/opencode` — an RCE vector.
- Headless `opencode run` auto-**rejects** every permission request unless `--dangerously-skip-permissions` is passed — silently breaking every edit/bash/grep for a build agent.

The skill at `/opt/data/skills/.../opencode/SKILL.md` documents all of this, but it's still advice the model has to follow. This plugin turns the advice into runtime enforcement and bundles the two-step invocation pattern into a single structured tool.

## What it ships

| Tool | Purpose |
|---|---|
| `opencode_run` | One-shot coding task. Background+wait built in. Returns structured result with status, exit code, files changed, diff stat, output tail. |
| `opencode_session_list` | List recent sessions (always `--format json` to dodge the pager). |
| `opencode_session_delete` | Delete a session by id. |
| `opencode_session_export` | Export a session's transcript (optional `--sanitize`). |
| `opencode_session_import` | Import a session from a file or `https://opncd.ai/s/<slug>` URL. |
| `opencode_stats` | Token/cost telemetry. |
| `opencode_models` | List available models. |
| `opencode_debug_config` | Resolved opencode config (diagnostic). |
| `opencode_version` | Installed opencode version. |

Plus a `pre_tool_call` hook that blocks four known-bad invocation shapes (bare `opencode run`, `opencode pr` in headless, `opencode auth login`, and `oc` calls missing `background=true` or `timeout=3600`).

For the full design rationale, research log, and roadmap, see [DESIGN.md](./DESIGN.md).

## Install

```bash
git clone https://github.com/nishitpatel92/hermes-plugins.git
cp -r hermes-plugins/opencode ~/.hermes/plugins/
hermes plugins enable opencode
```

Restart hermes (or recreate the gateway container) so `register()` runs and the tools become available:

```bash
docker compose -f compose_files/hermes/compose.yml up -d --force-recreate hermes
hermes plugins list
# opencode … enabled
```

Verify:

```bash
hermes tools --summary | grep opencode_
```

## Usage

### Primary: `opencode_run`

```
opencode_run(
    prompt="Refactor apps/web/components/ProjectList.tsx to fix the …",
    workdir="/root/projects/worklane",
    agent="build",                         # optional; falls back to opencode.json default
    model="openai/gpt-5.3-codex",          # optional
    files=["apps/web/components/ProjectList.tsx"],   # optional attachments
    include_diff_summary=True,             # default; runs git diff --stat after success
)
```

Returns:

```jsonc
{
  "status": "ok" | "error" | "timeout" | "killed" | "unknown",
  "exit_code": 0,
  "exit_meaning": "completed cleanly",
  "duration_ms": 312418,
  "session_id_terminal": "term_xyz789",
  "session_id_opencode": "ses_abc...",
  "output_tail": "<last ~4KB of stdout>",
  "files_changed": ["apps/web/components/ProjectList.tsx"],
  "diff_stat": " ProjectList.tsx | 12 ++++++++----"
}
```

### Continuation and forks

```
opencode_run(prompt="Now also handle the 401 retry path",
             workdir="/root/projects/worklane",
             continue_session=True)               # most recent root session

opencode_run(prompt="Same task, try a different approach",
             workdir="/root/projects/worklane",
             session_id="ses_abc...",
             fork=True)                            # fork into a sibling
```

### Slash commands

If `opencode.json` defines custom commands (e.g. `/review`):

```
opencode_run(prompt="<branch>", workdir="...", slash_command="/review")
```

The prompt becomes the command's `$ARGUMENTS`.

### Event-stream output

```
opencode_run(prompt="...", workdir="...", format="json", parse_events=True)
```

`format="json"` makes opencode emit ndjson events; `parse_events=True` parses them and returns a structured `events` array (capped at 500, with `step_start`/`step_finish` filtered out as noise). Useful when you need to know which tools opencode invoked.

### Permission policy

The plugin defaults to `dangerously_skip_permissions=true` because **opencode auto-rejects every permission request in headless mode** otherwise — every `edit`, `bash`, `grep` silently fails. The only case to flip this off is read-only plan-mode work:

```
opencode_run(prompt="...", workdir="...", agent="plan",
             dangerously_skip_permissions=False)
```

The `plan` agent denies edits and bash by design, so permission gating doesn't matter there.

## What the pre_tool_call hook blocks

Any `terminal()` call whose command matches any of:

| Pattern | Block reason |
|---|---|
| `opencode run …` (bare) | Hits the 600s foreground cap; use `opencode_run` or `oc` |
| `opencode pr …` | TUI launcher; never exits headless |
| `opencode auth login …` | RCE risk from `<url>/.well-known/opencode` |
| `oc …` without `background=true` | Same 600s cap |
| `oc …` with `timeout < 3600` | Terminal call must wait long enough for the wrapper |

Fast meta commands (`opencode stats`, `opencode session list`, `opencode --version`, etc.) pass through without enforcement. Server modes (`opencode serve`, `web`, `acp`, `attach`) pass through with a warning log.

## Configuration

The plugin reads no env vars and has no config file. The behavior is encoded directly. To tweak defaults (event-cap, output-tail bytes, etc.), edit `formats.py`.

## Limitations / out of scope

See [DESIGN.md § Future improvements](./DESIGN.md#future-improvements) for the full list. Highlights:

- **No `opencode_pr` tool** — `opencode pr` is interactive. The headless equivalent is "clone the PR and `opencode_run` over the diff", which the model can do with the existing tools.
- **No TUI launcher tool** — pty handoff complicates the structured-return contract. Use `terminal(command="opencode", pty=True)` directly if you need TUI mode.
- **No `auth login` tool** — RCE risk. Auth is image-bake time, not runtime.
- **`opencode_run` blocks for up to one hour** — the handler waits synchronously. If this causes gateway-side issues, a future iteration could switch to a fire-and-poll pattern.

## License

MIT (inherits from the repo root `LICENSE`).
