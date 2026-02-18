#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR_DEFAULT="${REPO_ROOT}/venv"
VENV_DIR="${VENV_DIR:-${VENV_DIR_DEFAULT}}"
VENV_PY="${VENV_DIR}/bin/python"

usage() {
  cat <<USAGE
Usage: $0 [--safe] [--live] [--config <path>] [config-path]

Options:
  --safe            offline config validation only (no adapters, no network, no orders)
  --live            allow real run (still requires CONFIRM_LIVE_TRADING=1)
  --config <path>   explicit config path (alternative to positional arg)
USAGE
}

SAFE_MODE=0
LIVE_MODE=0
CONFIG_PATH=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --safe)
      SAFE_MODE=1
      shift
      ;;
    --live)
      LIVE_MODE=1
      shift
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
      if [ -z "${CONFIG_PATH}" ]; then
        CONFIG_PATH="$1"
        shift
      else
        echo "[run] FAIL: unknown argument: $1"
        usage
        exit 1
      fi
      ;;
  esac
done

if [ -z "${CONFIG_PATH}" ]; then
  CONFIG_PATH="config.yaml"
fi

if [ ! -x "${VENV_PY}" ]; then
  echo "[run] FAIL: venv python not found at ${VENV_PY}"
  echo "[run] remediation: run ./scripts/setup.sh"
  exit 1
fi

cd "${REPO_ROOT}"

if [ ! -f "${CONFIG_PATH}" ]; then
  echo "[run] FAIL: config not found: ${CONFIG_PATH}"
  usage
  exit 1
fi

if [ "${SAFE_MODE}" -eq 1 ] && [ "${LIVE_MODE}" -eq 1 ]; then
  echo "[run] FAIL: --safe and --live cannot be used together"
  exit 1
fi

if [ "${SAFE_MODE}" -eq 1 ]; then
  echo "[run] SAFE mode: offline validation only"
  "${VENV_PY}" - <<PY
from controller.config import load_config
cfg = load_config("${CONFIG_PATH}")
print(
    f"[run] OK safe check: mode={cfg.mode}, instruments={len(cfg.instruments)}, "
    f"pair={cfg.entry_primary_exchange}+{cfg.entry_secondary_exchange}"
)
PY
  exit 0
fi

if [ "${LIVE_MODE}" -ne 1 ]; then
  echo "[run] FAIL: live run is blocked without --live"
  echo "[run] remediation: CONFIRM_LIVE_TRADING=1 ./scripts/run_enter.sh --live ${CONFIG_PATH}"
  exit 2
fi

if [ "${CONFIRM_LIVE_TRADING:-}" != "1" ]; then
  echo "[run] FAIL: live run is blocked; set CONFIRM_LIVE_TRADING=1"
  echo "[run] remediation: CONFIRM_LIVE_TRADING=1 ./scripts/run_enter.sh --live ${CONFIG_PATH}"
  exit 2
fi

echo "[run] starting enter_delta_neutral (LIVE) with config: ${CONFIG_PATH}"
PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" \
  "${VENV_PY}" -m controller.scripts.enter_delta_neutral --config "${CONFIG_PATH}" --live
