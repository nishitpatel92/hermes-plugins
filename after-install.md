# host-logs — installed!

This plugin adds two tools — `host_logs` and `host_containers` — that read **homelab host** container logs via a docker-socket-proxy.

## Before the agent can use it

1. Make sure a docker-socket-proxy is running on the same network as hermes. See the README for an example compose.
2. Restart hermes so the plugin's `register()` runs:
   ```sh
   docker compose -f compose_files/hermes/compose.yml restart
   # or: hermes gateway run
   ```
3. Verify the tools loaded:
   ```sh
   hermes tools --summary | grep host_
   ```
4. Override the proxy URL via `HERMES_HOST_LOGS_PROXY` in hermes' env if your proxy isn't at `http://socket-proxy:2375`.

## Try it

In a chat session:

> *"Show me the last 50 lines of worklane-api's logs."*

The agent should call `host_containers` (to confirm the name) then `host_logs(container="worklane-api", tail=50)` and surface the result.
