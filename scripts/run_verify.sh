#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR_DEFAULT="${REPO_ROOT}/venv"
VENV_DIR="${VENV_DIR:-${VENV_DIR_DEFAULT}}"
VENV_PY="${VENV_DIR}/bin/python"

usage() {
  cat <<USAGE
Usage: $0 [--safe] [--live] [--exchange <name>] [--config <path>]

Defaults:
  --safe (offline dry-run path)
  --exchange extended
  --config config.yaml
USAGE
}

MODE="safe"
EXCHANGE="extended"
CONFIG_PATH="config.yaml"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --safe)
      MODE="safe"
      shift
      ;;
    --live)
      MODE="live"
      shift
      ;;
    --exchange)
      EXCHANGE="${2:-}"
      shift 2
      ;;
    --config)
      CONFIG_PATH="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[verify] FAIL: unknown argument: $1"
      usage
      exit 1
      ;;
  esac
done

if [ ! -x "${VENV_PY}" ]; then
  echo "[verify] FAIL: venv python not found at ${VENV_PY}"
  echo "[verify] remediation: run ./scripts/setup.sh"
  exit 1
fi

cd "${REPO_ROOT}"

if [ ! -f "${CONFIG_PATH}" ]; then
  echo "[verify] FAIL: config not found: ${CONFIG_PATH}"
  exit 1
fi

if [ "${MODE}" = "live" ]; then
  if [ "${CONFIRM_LIVE_TRADING:-}" != "1" ]; then
    echo "[verify] FAIL: live verify blocked; set CONFIRM_LIVE_TRADING=1"
    exit 2
  fi
  echo "[verify] running LIVE verify"
  PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" \
    "${VENV_PY}" -m controller.scripts.verify_order_placement \
    --live --exchange "${EXCHANGE}" --config "${CONFIG_PATH}"
  exit 0
fi

echo "[verify] running SAFE verify (dry-run)"
PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" \
  "${VENV_PY}" -m controller.scripts.verify_order_placement \
  --dry-run --exchange "${EXCHANGE}" --config "${CONFIG_PATH}"
