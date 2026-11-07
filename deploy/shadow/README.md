# Offline shadow replay sandbox

This profile is **offline only**. It cannot reach BingX, Telegram, GitHub, the host network or a local exchange service.

## Runtime boundary

- Container network: `network_mode: none` (loopback only; no external interface/default route).
- Repository: mounted at `/workspace:ro`.
- Writable bind mount: only the caller-supplied external `ASTRA_SHADOW_DATA` directory at `/data:rw`.
- Root filesystem: read-only; `/tmp` is a small tmpfs.
- Process: numeric unprivileged UID/GID 65532; all Linux capabilities dropped; `no-new-privileges`; PID and memory limits.
- No Docker socket, credentials, environment secrets, host PID, host network or privileged mode are mounted/enabled.
- The replay program itself imports no HTTP, WebSocket, broker or private-client package.

Prepare the external directory on the host and grant access only to the sandbox UID:

```sh
mkdir -p /absolute/path/to/astra-shadow-data
chown 65532:65532 /absolute/path/to/astra-shadow-data
chmod 0700 /absolute/path/to/astra-shadow-data
export ASTRA_SHADOW_DATA=/absolute/path/to/astra-shadow-data
docker compose -f deploy/shadow/compose.offline.yml run --rm shadow-replay --help
```

The actual command expects a pre-existing `events.jsonl` file in that external directory. It reads that file, writes a restart checkpoint and a result JSONL only under `/data`. The public capture program is not invoked by this profile. This PR has not generated any event tape.

## Runtime verification

From a synthetic event tape, start the service with the above Compose profile and run:

```sh
docker compose -f deploy/shadow/compose.offline.yml run --rm --entrypoint python shadow-replay /workspace/scripts/verify_shadow_runtime.py
```

The verifier checks effective UID, zero effective capabilities, `NoNewPrivs`, read-only root and repository mounts, writable data mount, tmpfs /tmp, and absence of non-loopback interfaces/default route. It also attempts a temporary write to the repository (must fail) and to `/data` (must succeed and is removed).

These checks attest to the **observed container process** under the supplied Docker daemon/configuration. They do not prove the Docker host/kernel is uncompromised, that an administrator cannot override the profile, or that a future capture network is safely restricted. Runtime verification must be executed on the actual host before experiment approval. A network-enabled capture must use a separately approved egress proxy/firewall allowlist and a shared BingX request-budget coordinator; it is intentionally not defined here.

## Evidence status

The Compose profile is prepared in this PR. The runtime verifier is code-reviewed but **has not been executed in an actual container during PR development**. Therefore current evidence is configuration-level, not a runtime attestation. This profile is incapable of performing public market-data collection while `network_mode: none` is enforced.
