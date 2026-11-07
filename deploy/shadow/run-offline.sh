#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)"
: "${ASTRA_SHADOW_DATA:?Set ASTRA_SHADOW_DATA to a dedicated external directory}"

MODE="replay"
if [[ "${1:-}" == "--verify" ]]; then
  MODE="verify"
  shift
fi

DATA_DIR="$(realpath -m -- "${ASTRA_SHADOW_DATA}")"
case "${DATA_DIR}" in
  "${REPO_ROOT}"|"${REPO_ROOT}/"*)
    echo "Refusing shadow data directory inside the repository: ${DATA_DIR}" >&2
    exit 2
    ;;
esac

mkdir -p -- "${DATA_DIR}"
DATA_DIR="$(realpath -- "${DATA_DIR}")"
case "${DATA_DIR}" in
  "${REPO_ROOT}"|"${REPO_ROOT}/"*)
    echo "Refusing resolved shadow data directory inside the repository: ${DATA_DIR}" >&2
    exit 2
    ;;
esac

if [[ "${MODE}" == "replay" && ! -f "${DATA_DIR}/events.jsonl" ]]; then
  echo "Missing external event tape: ${DATA_DIR}/events.jsonl" >&2
  exit 2
fi

for target in events.jsonl replay-state.json replay-results.jsonl; do
  path="${DATA_DIR}/${target}"
  if [[ -L "${path}" ]]; then
    echo "Refusing symlinked shadow data file: ${path}" >&2
    exit 2
  fi
  if [[ -e "${path}" ]]; then
    links="$(stat -c '%h' -- "${path}")"
    if [[ "${links}" != "1" ]]; then
      echo "Refusing hard-linked shadow data file (${links} links): ${path}" >&2
      exit 2
    fi
  fi
done

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker CLI is required for the offline sandbox." >&2
  exit 2
fi

export ASTRA_SHADOW_DATA="${DATA_DIR}"
COMPOSE=(docker compose --file "${REPO_ROOT}/deploy/shadow/compose.offline.yml")
if [[ "${MODE}" == "verify" ]]; then
  exec "${COMPOSE[@]}" run --rm --pull never --entrypoint python shadow-replay \
    /workspace/scripts/verify_shadow_runtime.py "$@"
fi

exec "${COMPOSE[@]}" run --rm --pull never shadow-replay "$@"
