# host-logs hermes plugin

Read-only access to homelab host container logs from a Hermes agent — without giving the agent (or its sandboxes) any write capability on the host docker daemon.

Adds two tools the agent can call:

- **`host_logs(container, tail=200, stdout=true, stderr=true, since=0)`** — fetch recent stdout/stderr from a container running on the homelab host (`worklane-api`, `caddy`, `n8n`, `hermes` itself, …). Read-only. Cannot start/stop/exec the container.
- **`host_containers(all=false)`** — list containers on the host so the agent can discover names to pass to `host_logs`.

## Why

Hermes spawns its agent commands inside sandbox containers (via `terminal.backend: docker` in dind). Sandboxes can't see host containers — different daemon. To debug host-side services from an agent session, the agent needs *some* path to the host daemon. This plugin takes the narrowest viable path:

1. A separate [`tecnativa/docker-socket-proxy`](https://github.com/Tecnativa/docker-socket-proxy) container bind-mounts `/var/run/docker.sock` on the host and exposes it as HTTP, with **only read endpoints enabled** (`CONTAINERS=1`, `LOGS=1`, `INFO=1`, …; `POST=0`). Write/exec/destroy verbs return 403.
2. The plugin runs inside the hermes process and queries the proxy. The agent's *sandbox* never gets the socket.

Even if the hermes process is fully compromised, the attacker's host-daemon access is limited to list/inspect/log — no `docker run`, no `exec`, no `kill`.

## Setup

### 1. Run the docker-socket-proxy

Add a `socket-proxy` service on the same docker network as hermes. Example compose:

```yaml
networks:
  main-network:
    name: main-network
    external: true

services:
  socket-proxy:
    image: tecnativa/docker-socket-proxy:latest
    container_name: socket-proxy
    restart: unless-stopped
    environment:
      CONTAINERS: 1
      LOGS: 1
      INFO: 1
      VERSION: 1
      EVENTS: 1
      PING: 1
      POST: 0
      DELETE: 0
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    networks:
      main-network: {}
    deploy:
      resources:
        limits:
          cpus: '0.25'
          memory: 64M
```

The plugin defaults to `http://socket-proxy:2375`. Override with `HERMES_HOST_LOGS_PROXY=http://other:2375` in hermes' env if you run the proxy elsewhere.

### 2. Install the plugin in hermes

```sh
hermes plugins install nishitpatel92/hermes-plugins
```

The plugin clones into `~/.hermes/plugins/host-logs/`. Restart hermes (or recreate the container) so the plugin's `register()` runs and the tools become available.

```sh
hermes plugins list
# host-logs … enabled
```

### 3. Try it from a chat / gateway session

```
> What does worklane-api look like in the logs right now?

The agent calls host_logs(container="worklane-api", tail=100) and reports back.
```

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `HERMES_HOST_LOGS_PROXY` | `http://socket-proxy:2375` | Base URL of the docker-socket-proxy. |

## Behavior notes

- **Output cap**: `host_logs` truncates returned text to ~100 KB and notes the truncation. Override the limit by editing `MAX_RESULT_BYTES` in `__init__.py` if you really need more in one call.
- **Multiplexed log streams**: docker logs API uses a multiplexed framing format for non-TTY containers. The plugin demuxes correctly and merges stdout+stderr in chronological order.
- **`since`**: integer seconds ago. The proxy itself accepts a Unix timestamp; the plugin computes that from the relative value at request time.
- **Stopped containers**: `host_containers(all=true)` includes them; `host_logs` works on them too (logs from before they stopped).

## Security

- All host-daemon access goes through the proxy. The plugin does not bind-mount `/var/run/docker.sock` into hermes itself.
- The proxy's `:ro` mount of the host socket only restricts file-level perms, not API-level — the read-only enforcement is provided by the proxy's own ENV-driven endpoint allowlist (`POST=0`).
- The plugin runs in hermes' Python process, not in agent sandboxes. Sandbox compromise has zero added blast radius from this plugin.

## License

MIT.
