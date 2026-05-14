# hermes-plugins

A collection of [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugins maintained by [@nishitpatel92](https://github.com/nishitpatel92). Each subdirectory is a self-contained plugin with its own `plugin.yaml`, `__init__.py`, and README.

## Index

| Plugin | Surface | Purpose |
|---|---|---|
| [`host-logs`](./host-logs) | `register_tool` × 2 | Read-only access to homelab host container logs via a hardened docker-socket-proxy. |
| [`opencode`](./opencode) | `register_tool` × 9 + `pre_tool_call` hook | First-class tools for invoking opencode from Hermes (one-shot runs, sessions, stats, diagnostics) with enforcement of the `oc`-wrapper discipline. See [DESIGN.md](./opencode/DESIGN.md) for the full research and roadmap. |

## Installing a plugin

Hermes' `hermes plugins install <user>/<repo>` command treats the whole repo as a single plugin, so for a multi-plugin repo like this one you install each plugin manually:

```bash
git clone https://github.com/nishitpatel92/hermes-plugins.git
cp -r hermes-plugins/<plugin-name> ~/.hermes/plugins/

# enable it
hermes plugins enable <plugin-name>
```

Restart Hermes (or recreate the gateway container) so the plugin's `register()` runs and any new tools/hooks become available:

```bash
hermes plugins list
# <plugin-name> … enabled
```

To uninstall: `rm -rf ~/.hermes/plugins/<plugin-name>` and `hermes plugins disable <plugin-name>`.

## Updating

```bash
cd /path/to/hermes-plugins && git pull
cp -r <plugin-name>/. ~/.hermes/plugins/<plugin-name>/
```

Re-copying is intentional — Hermes loads plugins from `~/.hermes/plugins/`, not from this clone, so changes here don't propagate until you copy them in. If you'd rather skip the copy step, symlink the plugin instead: `ln -s "$(pwd)/<plugin-name>" ~/.hermes/plugins/<plugin-name>`.

## Adding a plugin

Each plugin lives in its own subdirectory at the repo root. The minimum layout is:

```
<plugin-name>/
├── plugin.yaml      # manifest (name, version, description, provides_tools, …)
├── __init__.py      # exports register(ctx)
└── README.md        # what it does, install/config notes
```

See the official [build-a-hermes-plugin guide](https://hermes-agent.nousresearch.com/docs/guides/build-a-hermes-plugin) for the full plugin surface, and any plugin in this repo as a working example. Keep each plugin self-contained — no shared utilities across plugin dirs.

## License

MIT — see [LICENSE](./LICENSE). Individual plugins inherit this unless their own README says otherwise.
