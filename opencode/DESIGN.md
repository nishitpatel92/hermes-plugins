# opencode plugin — design and roadmap

This document captures the research the plugin was built against, the boundaries of the current implementation, and the changes we deliberately deferred. It's a reference for future contributors and for the author when re-engaging the work after time has passed.

The plugin targets `opencode-ai@1.14.46`. Where opencode evolves, this doc may drift — sections drawn from CLI introspection / source reading are tagged so the author can refresh them against a newer opencode without re-doing the whole audit.

---

## 1. Why this plugin exists

Hermes can already drive opencode through the standard `terminal` + `process` tools, given a correctly shaped invocation. The Hermes sandbox image ships an `oc` wrapper at `/usr/local/bin/oc` (`exec timeout --kill-after=10s --signal=TERM 3600 opencode run "$@"`) that supplies the only invariant the model can't get wrong: a kernel-level 3600s ceiling.

Everything else is workflow advice the model must follow each time:

1. **`background=true`** on the `terminal` call. Without it, Hermes' `TERMINAL_MAX_FOREGROUND_TIMEOUT` (defaulting to 600s) silently clamps the call and the inner `oc` invocation gets killed at the 10-minute mark, surfacing as `exit_code=137` with "Client disconnected" in the inner stream. The terminal tool reports `timeout=600` regardless of what the model asked for.
2. **`timeout=3600`** on the `terminal` call so the surrounding wait outlasts the wrapper.
3. **Paired `process(action="wait", timeout=3600)`** to actually block until completion and read the result.
4. **`--dangerously-skip-permissions`** on the inner `opencode run` so the agent can actually edit/run. Without this flag, **opencode auto-rejects every permission request in non-interactive mode** (`run.ts:336-354`) — every gated tool call silently fails. The model has no way to recover; the run completes "successfully" with nothing accomplished.
5. **Never `opencode run` directly without `oc`** — the wrapper is the only deterministic stop.
6. **Never `opencode pr <N>` headless** — it `gh pr checkout`s and then **launches the interactive TUI**, which never exits in a non-TTY context.
7. **Never `opencode auth login <url>`** — it executes the command declared in `<url>/.well-known/opencode` (`opencode/cli/cmd/providers.ts`). Trusting the URL is RCE.

Documenting all of this in a skill works but is fragile: a single agent that misshapes one call burns up to an hour of wall-clock and produces nothing. Moving the discipline into a plugin gives two layered guarantees:

- **A `pre_tool_call` hook** that blocks the four known-bad shapes regardless of where the call originated.
- **A first-class `opencode_run` tool** that builds the correct shape internally so the model can't mis-construct it.

The kernel `oc` wrapper, the `pre_tool_call` hook, and the structured tool form three independent layers — any single layer is sufficient for the safety property; together they make the failure mode require three independent mistakes.

---

## 2. Research log

The plugin was built against opencode-ai@1.14.46 verified against both `opencode --help` output and the v1.14.46 source tarball from `github.com/sst/opencode`. This section is the distilled reference; for the long-form research transcript, the relevant source files are cited inline.

### 2.1 Subcommands

`opencode --help` registers 25 subcommands (`packages/opencode/src/index.ts:156-179`). Two are hidden (`console`, `generate`).

| Subcommand | Aliases | Notes |
|---|---|---|
| `(default) [project]` | — | Interactive TUI in the named project (or cwd). |
| `run [message..]` | — | Non-interactive one-shot. **The only subcommand this plugin's primary tool wraps.** |
| `attach <url>` | — | TUI bound to a remote `opencode serve`. |
| `serve` | — | Headless HTTP server (OpenAPI surface). |
| `web` | — | `serve` + auto-open of the embedded UI. |
| `acp` | — | Speaks Agent Client Protocol over stdio. |
| `providers` | `auth` | `list`, `login`, `logout`. **`login` is the RCE vector.** |
| `agent` | — | `create`, `list` — manage agent specs. |
| `upgrade [target]` | — | Self-update. |
| `uninstall` | — | Remove the binary + state. |
| `models [provider]` | — | List models from `models.dev` cache. |
| `stats` | — | Token/cost telemetry. |
| `export [sessionID]` | — | Export session as JSON. |
| `import <file>` | — | Import session JSON from path or share URL. |
| `session` | — | `list`, `delete`. |
| `pr <number>` | — | **TUI launcher.** Never headless-safe. |
| `github` | — | `install`, `run` — Actions integration. |
| `mcp` | — | `add`, `list`, `auth`, `logout`, `debug`. |
| `plugin <module>` | `plug` | NPM-install an opencode plugin. |
| `db [query]` | — | sqlite shell or one-shot query. |
| `debug` | — | `config`, `lsp`, `rg`, `file`, `agent`, `skill`, `snapshot`, `paths`, `info`, `wait`. |
| `console` | — | Hidden. Manages opencode.ai Console accounts. |
| `generate` | — | Hidden. Emits OpenAPI spec. |
| `completion` | — | Shell completion. |

### 2.2 `opencode run` flag surface

Source: `packages/opencode/src/cli/cmd/run.ts:121-230`. The complete set of flags the plugin needs to be aware of:

| Flag | Short | Type | Notes |
|---|---|---|---|
| `[message..]` | — | positional | Concatenated with `" "`; quotes preserved around tokens containing spaces. |
| `--command` | — | string | Run a slash command from `opencode.json`'s `command` map; message becomes `$ARGUMENTS`. Mutually exclusive with `--interactive`. |
| `--continue` | `-c` | bool | Resume most recent root (parent-less) session. |
| `--session` | `-s` | string | Resume specific session id (`ses_…`). |
| `--fork` | — | bool | Fork the resumed session into a sibling. Requires `-c` or `-s`. |
| `--share` | — | bool | Force-share, overriding `share: manual`. |
| `--model` | `-m` | string | `provider/model`. |
| `--agent` | — | string | Agent name. Subagent names fall back to default. |
| `--format` | — | enum | `"default"` or `"json"` (ndjson event stream). |
| `--file` | `-f` | string[] | File or directory attachments. Resolved relative to `--dir`. Missing files exit 1. |
| `--title` | — | string | Session title. Empty string ⇒ derive from first 50 chars. |
| `--attach` | — | string | URL of a remote `opencode serve`. |
| `--password` | `-p` | string | Basic auth for `--attach`. Falls back to `OPENCODE_SERVER_PASSWORD`. |
| `--username` | `-u` | string | Same. Falls back to `OPENCODE_SERVER_USERNAME` or `"opencode"`. |
| `--dir` | — | string | Working directory. Without `--attach`, `chdir`s the process. |
| `--port` | — | number | Port for the in-process server when not attaching. |
| `--variant` | — | string | Reasoning-effort hint passed to provider SDK (`high`, `max`, `minimal`). |
| `--thinking` | — | bool | Default: `true` interactive, `false` non-interactive. |
| `--interactive` | `-i` | bool | TTY split-footer mode. Requires TTY stdout. Mutually exclusive with `--command` and `--format json`. |
| `--dangerously-skip-permissions` | — | bool | Auto-reply `"once"` to permission requests. **Critical** for autonomous build agents. |
| `--demo` | — | bool | Demo slash commands; requires `--interactive`. |

Plus global flags (`--help/-h`, `--version/-v`, `--print-logs`, `--log-level`, `--pure`).

### 2.3 Exit codes

opencode itself emits only **0 (success) or 1 (failure)**. Every error path in `packages/opencode/src/**` goes through `process.exit(1)` or `process.exitCode = 1`. The `finally` block at `src/index.ts:243-247` forces `process.exit()` to kill leaked subprocesses (notably docker-run MCP servers).

Anything else the plugin sees comes from the surrounding wrapper or kernel:

| Code | Source | Plugin's `status` |
|---|---|---|
| `0` | opencode success | `ok` |
| `1` | opencode error | `error` |
| `124` | `oc`'s `timeout` after 3600s SIGTERM | `timeout` |
| `137` | SIGKILL from outside (`oc`'s 10s grace SIGKILL after SIGTERM, Hermes' foreground clamp, sandbox reaper, OOM) | `killed` |
| other | uncategorized | `error` |

The plugin's `formats.decode_exit_code` maps each to a human-readable `exit_meaning` string so the orchestrator gets a hint without re-parsing the code.

### 2.4 JSON event stream (`--format json`)

`packages/opencode/src/cli/cmd/run.ts:582-718`. Every emitted line is ndjson with this shape:

```ts
{ type: <string>, timestamp: <ms>, sessionID: <string>, ...<extra> }
```

The plugin keeps four types and filters the rest (`step_start` and `step_finish` are noise):

| `type` | Extra payload |
|---|---|
| `tool_use` | `{ part: ToolPart }` — completed or errored tool invocations with id, name, state, input/output. |
| `text` | `{ part: TextPart }` — finalized assistant text. |
| `reasoning` | `{ part: ReasoningPart }` — only when `--thinking`. |
| `error` | `{ error: { name, data?: { message? } } }`. |

There is **no explicit `session_end` event**. The process exits with code 0 when it observes `session.status.type === "idle"`. Plugin consumers detect end-of-stream by process exit, not by a sentinel event.

### 2.5 `opencode pr <N>` is not headless

Source: `packages/opencode/src/cli/cmd/pr.ts`. Behavior, in order:

1. `gh pr checkout <N> --branch pr/<N> --force`
2. `gh pr view <N> --json …` to detect cross-repo PRs; adds fork remote if needed.
3. Scans PR body for `https://opncd.ai/s/<id>`; if found, runs `opencode import <url>` and captures the imported session id.
4. **Spawns `opencode` (default subcommand = interactive TUI)** with `-s <imported>` if a session was imported.

It has no flags beyond `<number>`. It never exits cleanly in non-TTY context — the spawned TUI waits for input and the wrapper kills it at 3600s.

Practical consequence: a Hermes-driven PR review needs to clone the PR manually and call `opencode_run` over the diff, not call `opencode pr` and hope.

### 2.6 The `--dangerously-skip-permissions` trap

`run.ts:336-354` hard-codes a permission ruleset for headless mode that denies `question`, `plan_enter`, and `plan_exit`. Then for every `permission.asked` event the run loop fires, the handler at `run.ts:loop` automatically replies:

- `--dangerously-skip-permissions` set ⇒ `reply: "once"` (allow)
- Otherwise ⇒ `reply: "reject"` plus a stderr warning

In other words: an autonomous agent without `--dangerously-skip-permissions` runs to "completion" with every edit, bash invocation, grep, and webfetch silently rejected. The session reports no errors, just no actions. This is why the plugin's default is `dangerously_skip_permissions=true` — anything else turns the build agent into a no-op.

The exception is read-only `plan` mode runs, where permissions are mostly denied by the agent spec anyway, so the flag is a no-op.

### 2.7 `opencode auth login` is RCE

`packages/opencode/src/cli/cmd/providers.ts`. Two forms:

1. **Interactive**: `opencode auth login` (no URL) — picks a provider from the catalogue and runs that provider's login flow. Safe.
2. **`opencode auth login <url>`** — fetches `<url>/.well-known/opencode` for a JSON descriptor of the form `{ auth: { command: string[], env: string } }`, then **runs `command` as a child process and captures stdout as the credential**.

The `<url>` argument is whatever the agent supplied. Trusting it == arbitrary command execution under the gateway user. The plugin blocks the entire form via the `pre_tool_call` hook; the interactive form isn't usable in non-TTY contexts anyway.

### 2.8 Session DB and resume semantics

- DB at `${Global.Path.data}/opencode.db` (default `~/.local/share/opencode/opencode.db`).
- For non-stable installation channels, DB path becomes `opencode-<channel>.db` unless `OPENCODE_DISABLE_CHANNEL_DB` is set.
- The Hermes sandbox redirects `XDG_DATA_HOME=/etc/opencode-data`, so DB ends up at `/etc/opencode-data/opencode/opencode.db`.
- The Hermes sandbox is ephemeral — DB dies with the container. `--continue` and `-s <id>` only resume within the same sandbox. Cross-sandbox resume requires `opencode export` + `opencode import`.
- Session ids are `ses_<12 hex><14 base62>` (26 chars after the prefix). Time-ordered, monotonic per millisecond.

The plugin exposes `opencode_session_export` and `opencode_session_import` so the orchestrator can move state between sandboxes if needed.

### 2.9 `opencode.json` and agent frontmatter

The config schema is `.strict()` — unknown top-level keys fail config load (`packages/opencode/src/config/config.ts:131-308`). The plugin doesn't touch config directly; the sandbox image bakes `/etc/opencode/opencode.json` and bind-mounts `auth.json`.

Agent frontmatter fields (`config/agent.ts:23-52`) include `model`, `variant`, `temperature`, `top_p`, `prompt`, `tools` (deprecated), `disable`, `description`, `mode`, `hidden`, `options`, `color`, `steps`, `permission`. The current six-agent set in `compose_files/hermes/agents/*.md` uses `mode`, `model`, `permission`, and the markdown body for the prompt.

### 2.10 Environment variables that the plugin should be aware of

Most opencode env vars are configuration we control via the sandbox image:

- `OPENCODE_CONFIG=/etc/opencode/opencode.json` (provider config)
- `OPENCODE_CONFIG_DIR=/etc/opencode` (agents dir lookup)
- `XDG_DATA_HOME=/etc/opencode-data` (DB / cache / auth dir)

The few that an agent might want to consider:

- `OPENCODE_PURE` / `--pure` — disable external plugins. Useful for "is the provider broken or is a plugin breaking it?" diagnostic.
- `OPENCODE_DISABLE_AUTOCOMPACT` — for very long runs where compaction would lose context.
- `OPENCODE_AUTO_SHARE` — equivalent to `--share` on every run.

The plugin doesn't read any env vars itself; all behavior is parameterized.

### 2.11 Footguns we encoded

- `/exit` in the TUI is real (`packages/opencode/src/cli/cmd/tui/app.tsx:649-657`), but **disabled when the prompt input contains text**. Pressing `/exit` with a partial prompt triggers autocomplete to select a different command — likely what surfaces as "agent selector dialog" in user reports. Documented in the README and skill: Ctrl+C is the unconditional exit.
- `session list` paginates through `less -R -S` on a TTY. Plugin always passes `--format json -n N` to dodge this.
- `--port` with no value parses as `0` (random port).
- `--continue` resumes only **root** sessions (no `parentID`). Subagent sub-sessions are not enumerable via continue.

---

## 3. Architecture

The plugin runs **in the Hermes gateway Python process**, not in any sandbox. Tool handlers register through `ctx.register_tool` and execute when the LLM invokes the named tool.

```
┌─────────────────────────────────────────────────────────────────────┐
│ Hermes gateway (Python)                                             │
│                                                                     │
│   plugins.opencode                                                  │
│     register(ctx):                                                  │
│       ctx.register_tool("opencode_run", handler=…)   ──┐            │
│       ctx.register_tool("opencode_session_list", …)    │            │
│       … 7 more …                                       │            │
│       ctx.register_hook("pre_tool_call", enforce) ─────┼──→ blocks  │
│                                                        │     bad    │
│   handlers (closure over ctx):                         │     shapes │
│     opencode_run(args):                                ▼            │
│       cmd = "oc --dir … --agent … --model … '<prompt>'"             │
│       start = ctx.dispatch_tool("terminal", {                       │
│         command=cmd, workdir=…, background=True, timeout=3600 })    │
│       result = ctx.dispatch_tool("process", {                       │
│         action="wait", session_id=start.session_id, timeout=3600 }) │
│       return formats.decode(result) + summarize_diff(workdir)       │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Sandbox (dind, hermes-sandbox:latest)                               │
│   /usr/local/bin/oc → timeout 3600 opencode run "$@"                │
└─────────────────────────────────────────────────────────────────────┘
```

The plugin doesn't replicate Hermes' sandbox lifecycle — it dispatches through the same `terminal` and `process` tools the LLM would otherwise call directly. This is what makes the plugin small (~600 LOC) and keeps it on the safe side of Hermes' sandbox invariants.

### Handler return contract

Every handler returns a JSON-serialized string. Errors don't raise — they become `{"status": "error", "error": "..."}`. The `_err_wrap` helper in `tools.py` enforces this for every tool.

### Synchronous wait

`opencode_run` blocks the handler for up to 3600 seconds (whatever `process(action="wait")` takes to return). This matches the wall-clock cost of the existing two-step pattern but consolidates it into one tool call from the model's perspective. If gateway threading limits ever bite us, the fire-and-poll alternative is sketched in § Future improvements.

### Closure-based ctx capture

Plugin handlers don't receive `ctx` directly (the registered signature is `(args, **kwargs)`). The plugin uses factory functions (`make_opencode_run(ctx)` etc.) in `register()` that close over `ctx` and return the bound handler. This keeps the handler signature compatible with the Hermes contract and gives each handler access to `ctx.dispatch_tool` for inter-tool calls.

---

## 4. Current scope (what ships in v0.1.0)

Nine tools, one hook. All wired in `__init__.py:register()`.

| Tool | Wraps | Synchronous? | Notes |
|---|---|---|---|
| `opencode_run` | `oc …` + `terminal(background=True, timeout=3600)` + `process(action="wait")` | Yes (up to 3600s) | The primary work surface. |
| `opencode_session_list` | `opencode session list --format json -n N` | Yes (~seconds) | Always JSON to skip `less`. |
| `opencode_session_delete` | `opencode session delete <id>` | Yes | |
| `opencode_session_export` | `opencode export [id] [--sanitize]` | Yes (~seconds) | Returns parsed JSON. |
| `opencode_session_import` | `opencode import <source>` | Yes (~seconds) | Accepts file path or share URL. |
| `opencode_stats` | `opencode stats [--days N] [--tools N] [--models N] [--project P]` | Yes | |
| `opencode_models` | `opencode models [provider] [--verbose] [--refresh]` | Yes | |
| `opencode_debug_config` | `opencode debug config` | Yes | Returns resolved JSON. |
| `opencode_version` | `opencode --version` | Yes | |

| Hook | Fires | Action |
|---|---|---|
| `pre_tool_call` | Any `terminal` call | Block bare `opencode run`, `opencode pr`, `opencode auth login`, and `oc` calls missing `background=true` or `timeout>=3600`. Warn-only for unknown opencode invocations. |

### The `opencode_run` parameter surface in full

| Param | Type | Default | Maps to |
|---|---|---|---|
| `prompt` | string | required¹ | positional |
| `workdir` | string | required | `--dir` + terminal `workdir` |
| `agent` | string | (config) | `--agent` |
| `model` | string | (config) | `--model` |
| `variant` | string | — | `--variant` |
| `thinking` | bool | `false` | `--thinking` |
| `title` | string | — | `--title` |
| `files` | string[] | — | `-f` repeated |
| `continue_session` | bool | `false` | `--continue` |
| `session_id` | string | — | `-s` |
| `fork` | bool | `false` | `--fork` (requires continue or id) |
| `share` | bool | `false` | `--share` |
| `slash_command` | string | — | `--command` |
| `format` | `"default"`\|`"json"` | `"default"` | `--format` |
| `parse_events` | bool | `false` | post-processing |
| `attach_url` | string | — | `--attach` |
| `dangerously_skip_permissions` | bool | **`true`** | `--dangerously-skip-permissions` |
| `include_diff_summary` | bool | `true` | post-processing |
| `extra_args` | string[] | `[]` | appended raw |

¹ `prompt` is required unless `slash_command` or `files` is set.

### Defaults that matter

| Default | Why |
|---|---|
| `dangerously_skip_permissions=true` | Otherwise headless runs auto-reject every permission request and silently do nothing. |
| `background=true`, `timeout=3600`, `notify_on_complete=true` | Enforced internally. Always. |
| `include_diff_summary=true` | The orchestrator almost always wants to know what changed. Adds ~100ms of `git status` + `git diff --stat`. |
| `format="default"` | JSON event stream is a context-spend choice. Opt in. |
| `thinking=false` | Matches opencode's own headless default. Reasoning blocks bloat context. |

---

## 5. Deliberate omissions

Things this plugin intentionally does not ship, with rationale. If the rationale changes, these become future-improvements candidates.

### `opencode_pr`

`opencode pr <N>` is a TUI launcher that does `gh pr checkout` and then spawns the interactive opencode (no flags to do anything else). In headless context it never exits. A meaningful "review a PR via opencode" tool would have to replicate the checkout, run `opencode_run` over the diff, and parse findings — that's a different design than a thin wrapper. The current path: orchestrator clones the PR and calls `opencode_run` directly.

### `opencode_tui_start`

The TUI requires `pty=true` on the terminal call and a different interaction model (`process(action="submit")` to send prompts, `process(action="poll")` to inspect state). Wrapping that into a "structured" tool buys little — the orchestrator already has the raw `terminal(command="opencode", pty=True)` path documented in the skill and isn't blocked from using it.

### `opencode_auth_*`

The `login` form is RCE; `list` and `logout` are diagnostic with effectively no agent use case. The `pre_tool_call` hook allows `auth list` and `auth logout` through if the orchestrator does call them via raw `terminal`. No structured tool.

### `opencode_serve` / `web` / `acp` / `attach`

These start long-lived servers. They don't fit the one-shot tool shape, and Hermes-bound opencode currently runs entirely inside the per-task sandbox. If we ever move to a long-lived opencode server outside the sandbox (`opencode serve` on the main network, agents `--attach` to it), `opencode_attach_*` tools become useful — see § Future improvements.

### Plugin-side cost telemetry

`opencode_stats` exposes the data; nothing in the plugin aggregates or stores it across runs. Cost discipline is the orchestrator's job, and the `build.md` agent spec was recently re-framed to express that as a context-economy concern, not a cost-discipline one. If pattern-of-use shows the orchestrator needs a "spend so far this session" signal, a `post_tool_call` aggregator hook is a natural addition.

---

## 6. Pre-call hook decisions

The `pre_tool_call` hook fires for every `terminal` call. The patterns it matches are deliberately narrow — each one targets a specific failure mode with a documented root cause:

| Pattern | Failure it prevents | Reference |
|---|---|---|
| `^opencode run\b` | 600s foreground clamp → SIGKILL of opencode, surfacing as `exit_code=137` | Hermes' `TERMINAL_MAX_FOREGROUND_TIMEOUT` |
| `^opencode pr\b` | TUI launcher never exits headless → wrapper kill at 3600s | `opencode/cli/cmd/pr.ts` |
| `^opencode auth login\b` | Arbitrary command execution from `<url>/.well-known/opencode` | `opencode/cli/cmd/providers.ts` |
| `^oc\b` with `background != true` | Same 600s clamp the wrapper exists to defeat | Hermes terminal config |
| `^oc\b` with `timeout < 3600` | `process(action="wait")` returns early, missing the actual completion | Hermes terminal config |

Anything matching `^opencode (session|stats|export|import|models|debug|--version|...)` passes through without enforcement — those are fast and benign. Anything else starting with `opencode` (e.g. `opencode serve`) gets a warning log and passes through.

The hook is **block-only**, not rewrite. Rewriting (e.g. silently adding `--dangerously-skip-permissions`) was rejected because it hides the model's mistake. Blocking with a useful error message teaches the model the correct shape on the next attempt; rewriting trains the model that the wrong shape works.

---

## 7. Future improvements

Ordered roughly by expected value, with rough effort and dependency notes.

### 7.1 Aggregate cost telemetry via `post_tool_call`

After every `opencode_run`, capture token/cost (currently in opencode's sqlite). Surface a "spend so far this session" or "spend this turn" signal to the orchestrator. Replaces the role the deleted "cost discipline" rule used to play, but driven by data instead of by tokens-have-a-price-tag intuition. Cost: ~80 LOC + one extra `opencode stats` call per run (~200ms).

### 7.2 Fire-and-poll variant of `opencode_run`

Currently `opencode_run` blocks the handler synchronously for up to 3600s. If gateway threading limits or async-context issues surface, split into `opencode_run_start(...) → session_id` and `opencode_run_collect(session_id) → result`. The model loses the single-tool-call ergonomics but the gateway gets back its event loop. Cost: ~150 LOC + a small skill update.

### 7.3 PR review without `opencode pr`

A `opencode_review_pr(pr_number, workdir, ...)` tool that does the `gh pr checkout` itself, builds a "review this diff" prompt with diff contents attached as `-f`, runs through `opencode_run` with the `reviewer` agent, and surfaces structured findings. Replaces the dropped `opencode_pr` tool with one that actually works headless. Cost: ~120 LOC.

### 7.4 Remote `opencode serve` mode

If the Hermes stack ever moves opencode out of per-task sandboxes into a long-lived server on `main-network`, the plugin grows `opencode_serve_*` lifecycle tools and `opencode_run` learns to prefer `--attach <serve-url>` over per-sandbox spawn. Session DB then survives across tasks, cross-sandbox `--continue` becomes possible, and the cold-start cost of each opencode invocation drops. Cost: significant — touches the docker compose stack as much as the plugin. Out of scope until there's a concrete pain point this solves.

### 7.5 Streaming progress events

For `format="json"` runs, parse and surface events incrementally rather than only after process exit. Requires either a) the model calling `opencode_run_progress(session_id)` periodically (couples to fire-and-poll above), or b) a server-sent-events style channel back through the gateway — which Hermes doesn't currently expose. Defer until #7.2 lands.

### 7.6 Per-agent permission policies

The plugin currently exposes one knob (`dangerously_skip_permissions`). opencode's permission system is much richer — per-tool allow/deny/ask, glob patterns, last-match-wins. A higher-level `permission_profile` enum (`autonomous`, `read_only`, `interactive_ask`) that maps to richer permission configs could replace the binary knob. Cost: ~60 LOC + one decision per profile.

### 7.7 MCP server visibility

`opencode_mcp_list(workdir)` and `opencode_mcp_debug(workdir, server)` to surface what MCP servers are configured + their connection state. Useful when opencode tools depend on a misconfigured MCP server and the failure mode is "tool reported no results" rather than "server unreachable". Cost: ~50 LOC.

### 7.8 Skill auto-rewrite

Once the plugin is the primary surface, the bundled opencode skill at `/opt/data/skills/.../opencode/SKILL.md` should collapse to a brief pointer ("call `opencode_run`; the raw `oc` pattern is the fallback"). A future plugin version could ship the rewritten skill as a sibling file and have `register()` write it to the right place at load time, or expose a `hermes skills install opencode` companion command. Cost: low; design question is whether plugins should mutate skills at all.

### 7.9 Event-stream filtering policy

The current default drops `step_start`/`step_finish` and keeps `tool_use`/`text`/`reasoning`/`error`. Other useful filterings (e.g. "only show tool errors", "only show files touched") could be exposed via a `event_filter` enum or a `filter` callable. Cost: ~30 LOC.

### 7.10 Plugin-managed sandboxes

Currently the plugin relies entirely on Hermes' sandbox machinery. If Hermes' sandbox lifecycle ever proves incompatible with long-lived opencode work (e.g. the reaper kills sandboxes between an `opencode_run_start` and a `opencode_run_collect`), the plugin would need to manage its own dedicated long-lived sandbox(es). Cost: high. Out of scope unless forced by a concrete failure.

---

## 8. References

Code (this plugin):
- `__init__.py` — `register(ctx)` wires tools and the hook
- `schemas.py` — JSON schemas the LLM sees
- `tools.py` — handler implementations and the `pre_tool_call` matcher
- `formats.py` — exit-code decode, diff summary, event-stream parser

External (opencode source tree, v1.14.46):
- `packages/opencode/src/index.ts` — subcommand registration, global flags
- `packages/opencode/src/cli/cmd/run.ts` — `run` flags, permission auto-rejection, event-stream emission
- `packages/opencode/src/cli/cmd/pr.ts` — why `pr` is TUI-only
- `packages/opencode/src/cli/cmd/providers.ts` — the `auth login` RCE surface
- `packages/opencode/src/config/config.ts` — top-level config schema
- `packages/opencode/src/config/agent.ts` — agent frontmatter schema
- `packages/opencode/src/storage/db.ts` — session DB path resolution

External (Hermes / this stack):
- `compose_files/hermes/oc` — the kernel-timeout wrapper inside the sandbox
- `compose_files/hermes/CLAUDE.md` — invariants for the Hermes + dind setup
- `compose_files/hermes/agents/*.md` — the six-agent opencode team configured inside the sandbox
- `compose_files/hermes/opencode-skill.md` — the model-facing skill the plugin's structured tools replace

External (Hermes plugin system docs):
- https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins
- https://hermes-agent.nousresearch.com/docs/guides/build-a-hermes-plugin
- https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks
