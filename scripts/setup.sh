#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR_DEFAULT="${REPO_ROOT}/venv"
VENV_DIR="${VENV_DIR:-${VENV_DIR_DEFAULT}}"
CONFIG_PATH="config.yaml"

usage() {
  cat <<USAGE
Usage: $0 [--config <path>] [--venv <path>]

Options:
  --config <path>   Config file for selected exchanges detection (default: config.yaml)
  --venv <path>     Venv directory (default: ${VENV_DIR_DEFAULT})
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --config)
      CONFIG_PATH="${2:-}"
      shift 2
      ;;
    --venv)
      VENV_DIR="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[setup] FAIL: unknown argument: $1"
      usage
      exit 1
      ;;
  esac
done

if [ -z "${CONFIG_PATH}" ]; then
  echo "[setup] FAIL: --config requires a value"
  exit 1
fi
if [ -z "${VENV_DIR}" ]; then
  echo "[setup] FAIL: --venv requires a value"
  exit 1
fi

# Resolve config path relative to repo root when needed.
if [ "${CONFIG_PATH#/}" = "${CONFIG_PATH}" ]; then
  CONFIG_ABS="${REPO_ROOT}/${CONFIG_PATH}"
else
  CONFIG_ABS="${CONFIG_PATH}"
fi

if [ ! -f "${CONFIG_ABS}" ]; then
  echo "[setup] FAIL: config not found: ${CONFIG_ABS}"
  echo "[setup] remediation: pass --config <path-to-config.yaml>"
  exit 1
fi

echo "[setup] repo: ${REPO_ROOT}"
echo "[setup] config: ${CONFIG_ABS}"
echo "[setup] venv: ${VENV_DIR}"

cd "${REPO_ROOT}"

if [ ! -d "${VENV_DIR}" ]; then
  echo "[setup] creating venv at ${VENV_DIR}"
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
else
  echo "[setup] venv already exists: ${VENV_DIR}"
fi

VENV_PY="${VENV_DIR}/bin/python"

if [ ! -x "${VENV_PY}" ]; then
  echo "[setup] FAIL: python not found in venv: ${VENV_PY}"
  exit 1
fi

echo "[setup] installing build tooling (pip/setuptools/wheel/poetry-core)"
MISSING_BUILD_TOOLS="$("${VENV_PY}" - <<'PY'
import importlib.util

mods = {
    "pip": "pip",
    "setuptools": "setuptools",
    "wheel": "wheel",
    "poetry-core": "poetry.core",
}
missing = []
for name, mod in mods.items():
    try:
        if importlib.util.find_spec(mod) is None:
            missing.append(name)
    except Exception:
        missing.append(name)
print(",".join(missing))
PY
)"

if [ -n "${MISSING_BUILD_TOOLS}" ]; then
  echo "[setup] missing build tools: ${MISSING_BUILD_TOOLS}"
  if ! "${VENV_PY}" -m pip install --upgrade pip setuptools wheel poetry-core; then
    echo "[setup] note: could not install build tools right now (likely offline); will continue"
  fi
else
  echo "[setup] build tools already available in venv"
fi

echo "[setup] installing base dependencies from requirements.txt"
"${VENV_PY}" -m pip install -r requirements.txt

readarray -t SETUP_META < <("${VENV_PY}" - <<PY
from __future__ import annotations
import yaml
from pathlib import Path

config_path = Path(r"${CONFIG_ABS}")
raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
if not isinstance(raw, dict):
    raise SystemExit("config root must be a mapping")

entry = raw.get("entry", {})
if not isinstance(entry, dict):
    entry = {}

primary = str(entry.get("primary_exchange", "extended")).strip().lower() or "extended"
default_secondary = {
    "extended": "variational",
    "nado": "extended",
    "variational": "extended",
}
secondary_raw = entry.get("secondary_exchange")
if secondary_raw is None or (isinstance(secondary_raw, str) and not secondary_raw.strip()):
    secondary = default_secondary.get(primary, "extended")
else:
    secondary = str(secondary_raw).strip().lower()

selected = sorted({primary, secondary})
print(f"PRIMARY={primary}")
print(f"SECONDARY={secondary}")
print(f"SELECTED={','.join(selected)}")
PY
)

PRIMARY=""
SECONDARY=""
SELECTED=""
for line in "${SETUP_META[@]}"; do
  case "$line" in
    PRIMARY=*) PRIMARY="${line#PRIMARY=}" ;;
    SECONDARY=*) SECONDARY="${line#SECONDARY=}" ;;
    SELECTED=*) SELECTED="${line#SELECTED=}" ;;
  esac
done

if [ -z "${SELECTED}" ]; then
  echo "[setup] FAIL: could not determine selected exchanges from config"
  exit 1
fi

echo "[setup] selected exchanges: ${SELECTED}"

exchange_selected() {
  local name="$1"
  case ",${SELECTED}," in
    *",${name},"*) return 0 ;;
    *) return 1 ;;
  esac
}

install_sdk() {
  local exchange="$1"
  local label="$2"
  local sdk_dir="$3"
  local fallback_pkg="$4"
  local import_name="$5"

  local required=0
  if exchange_selected "${exchange}"; then
    required=1
  fi

  if "${VENV_PY}" - <<PY >/dev/null 2>&1
import importlib
importlib.import_module("${import_name}")
PY
  then
    echo "[setup] SDK already importable (${label} -> ${import_name}), skipping install"
    return 0
  fi

  if [ -d "${sdk_dir}" ] && { [ -f "${sdk_dir}/pyproject.toml" ] || [ -f "${sdk_dir}/setup.py" ]; }; then
    echo "[setup] installing local SDK (${label}) editable: ${sdk_dir}"
    if "${VENV_PY}" -m pip install -e "${sdk_dir}" --no-deps; then
      echo "[setup] SDK installed (${label})"
      return 0
    fi

    if [ "${required}" -eq 1 ]; then
      echo "[setup] FAIL: editable install failed for required SDK (${label})"
      echo "[setup] remediation: ensure network access and rerun ${VENV_PY} -m pip install -e ${sdk_dir} --no-deps"
      exit 1
    fi

    echo "[setup] note: optional SDK (${label}) failed to install; continuing"
    return 0
  fi

  echo "[setup] local SDK directory not found for ${label}: ${sdk_dir}"
  echo "[setup] trying pip package fallback for ${label}: ${fallback_pkg}"
  if "${VENV_PY}" -m pip install "${fallback_pkg}"; then
    echo "[setup] SDK fallback installed (${label})"
    return 0
  fi

  if [ "${required}" -eq 1 ]; then
    echo "[setup] FAIL: required SDK (${label}) is missing and fallback install failed"
    echo "[setup] remediation: restore local SDK directory (${sdk_dir}) or provide network access for pip package ${fallback_pkg}"
    exit 1
  fi

  echo "[setup] note: optional SDK (${label}) unavailable; continuing"
}

install_sdk "extended" "Extended" "${REPO_ROOT}/Extended/python_sdk" "x10-python-trading-starknet" "x10"
install_sdk "nado" "Nado" "${REPO_ROOT}/Nado/nado-python-sdk" "nado-protocol" "nado_protocol"

copy_env_template() {
  local src="$1"
  local dst="$2"
  if [ -f "${src}" ] && [ ! -f "${dst}" ]; then
    echo "[setup] creating ${dst} from template"
    cp "${src}" "${dst}"
  elif [ -f "${dst}" ]; then
    echo "[setup] keeping existing ${dst}"
  else
    echo "[setup] note: template missing: ${src}"
  fi
}

copy_env_template "${REPO_ROOT}/Extended/.env.example" "${REPO_ROOT}/Extended/.env"
copy_env_template "${REPO_ROOT}/Variational/.env.example" "${REPO_ROOT}/Variational/.env"
copy_env_template "${REPO_ROOT}/Nado/.env.example" "${REPO_ROOT}/Nado/.env"

echo "[setup] done"
echo "[setup] next: ./scripts/doctor.sh --strict --config ${CONFIG_PATH}"
