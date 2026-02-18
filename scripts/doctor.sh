#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR_DEFAULT="${REPO_ROOT}/venv"
VENV_DIR="${VENV_DIR:-${VENV_DIR_DEFAULT}}"
VENV_PY="${VENV_DIR}/bin/python"
CONFIG_PATH="config.yaml"
STRICT=0

usage() {
  cat <<USAGE
Usage: $0 [--strict] [--config <path>] [--venv <path>]

Options:
  --strict          fail on missing required deps/env/config checks
  --config <path>   config path (default: config.yaml)
  --venv <path>     venv path (default: ${VENV_DIR_DEFAULT})
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --strict)
      STRICT=1
      shift
      ;;
    --config)
      CONFIG_PATH="${2:-}"
      shift 2
      ;;
    --venv)
      VENV_DIR="${2:-}"
      VENV_PY="${VENV_DIR}/bin/python"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[doctor] FAIL: unknown argument: $1"
      usage
      exit 1
      ;;
  esac
done

cd "${REPO_ROOT}"

echo "[doctor] repo: ${REPO_ROOT}"

if [ ! -x "${VENV_PY}" ]; then
  echo "[doctor] FAIL: venv python not found at ${VENV_PY}"
  echo "[doctor] remediation: run ./scripts/setup.sh"
  exit 1
fi

echo "[doctor] python: $(${VENV_PY} --version)"

if [ "${CONFIG_PATH#/}" = "${CONFIG_PATH}" ]; then
  CONFIG_ABS="${REPO_ROOT}/${CONFIG_PATH}"
else
  CONFIG_ABS="${CONFIG_PATH}"
fi

if [ ! -f "${CONFIG_ABS}" ]; then
  echo "[doctor] FAIL: config not found: ${CONFIG_ABS}"
  exit 1
fi

echo "[doctor] config: ${CONFIG_ABS}"
if [ "${STRICT}" -eq 1 ]; then
  echo "[doctor] mode: STRICT"
else
  echo "[doctor] mode: non-strict"
fi

STRICT_ENV="${STRICT}" REPO_ROOT_ENV="${REPO_ROOT}" CONFIG_PATH_ENV="${CONFIG_ABS}" \
"${VENV_PY}" - <<'PY'
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import yaml
from dotenv import dotenv_values

from controller.config import load_config

strict = os.environ.get("STRICT_ENV", "0") == "1"
repo_root = Path(os.environ["REPO_ROOT_ENV"]).resolve()
config_path = Path(os.environ["CONFIG_PATH_ENV"]).resolve()


def fail(msg: str, remediation: str) -> None:
    print(f"[doctor] FAIL: {msg}")
    print(f"[doctor] remediation: {remediation}")
    raise SystemExit(1)


def warn(msg: str) -> None:
    print(f"[doctor] WARN: {msg}")


def info(msg: str) -> None:
    print(f"[doctor] {msg}")


raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
if not isinstance(raw, dict):
    fail("config root must be mapping", f"fix YAML structure in {config_path}")

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
allowed = {"extended", "nado", "variational"}
for ex in selected:
    if ex not in allowed:
        fail(
            f"unsupported exchange in config: {ex}",
            "set entry.primary_exchange/entry.secondary_exchange to extended|nado|variational",
        )

info(f"selected exchanges: {', '.join(selected)}")

# Required imports only for selected exchanges.
required_imports: dict[str, list[str]] = {
    "base": ["controller", "yaml", "dotenv"],
    "extended": ["x10"],
    "nado": ["nado_protocol"],
    "variational": ["eth_account"],
}

for mod in required_imports["base"]:
    try:
        importlib.import_module(mod)
        info(f"OK import: {mod}")
    except Exception as exc:
        fail(f"missing base dependency '{mod}' ({exc})", "run ./scripts/setup.sh")

if "extended" in selected:
    for mod in required_imports["extended"]:
        try:
            importlib.import_module(mod)
            info(f"OK import: {mod}")
        except Exception as exc:
            fail(
                f"missing Extended dependency '{mod}' ({exc})",
                f"run ./scripts/setup.sh --config {config_path}; ensure Extended/python_sdk is present",
            )

if "nado" in selected:
    for mod in required_imports["nado"]:
        try:
            importlib.import_module(mod)
            info(f"OK import: {mod}")
        except Exception as exc:
            fail(
                f"missing Nado dependency '{mod}' ({exc})",
                f"run ./scripts/setup.sh --config {config_path}; ensure Nado/nado-python-sdk is present",
            )

if "variational" in selected:
    try:
        importlib.import_module("curl_cffi")
        info("OK import: curl_cffi")
    except Exception:
        try:
            importlib.import_module("aiohttp")
            info("OK import: aiohttp (curl_cffi fallback)")
        except Exception as exc:
            fail(
                f"missing Variational HTTP dependency (curl_cffi/aiohttp) ({exc})",
                "run ./scripts/setup.sh",
            )

    try:
        importlib.import_module("eth_account")
        info("OK import: eth_account")
    except Exception as exc:
        fail(f"missing Variational dependency 'eth_account' ({exc})", "run ./scripts/setup.sh")


def resolve_env_path(section: str, default_rel: str) -> Path:
    section_raw = raw.get(section, {})
    if not isinstance(section_raw, dict):
        section_raw = {}
    env_val = section_raw.get("env_file", default_rel)
    env_path = Path(str(env_val))
    if not env_path.is_absolute():
        env_path = repo_root / env_path
    return env_path.resolve()


env_files = {
    "extended": resolve_env_path("extended", "Extended/.env"),
    "nado": resolve_env_path("nado", "Nado/.env"),
    "variational": resolve_env_path("variational", "Variational/.env"),
}

required_env_vars = {
    "extended": ["X10_API_KEY", "X10_PUBLIC_KEY", "X10_PRIVATE_KEY", "X10_VAULT_ID"],
    "nado": ["NADO_PRIVATE_KEY"],
    "variational": ["VARIATIONAL_PRIVATE_KEY"],
}

placeholder_tokens = (
    "your_",
    "<",
    "replace",
    "changeme",
    "example",
)

for ex in selected:
    env_path = env_files[ex]
    if not env_path.exists():
        msg = f"required env file missing for {ex}: {env_path}"
        if strict:
            fail(msg, f"create {env_path} (copy from .env.example and fill secrets)")
        warn(msg)
        continue

    info(f"OK env file: {env_path}")
    values = dotenv_values(env_path)

    for key in required_env_vars[ex]:
        val = values.get(key)
        if val is None or not str(val).strip():
            msg = f"missing required env var {key} in {env_path}"
            if strict:
                fail(msg, f"set {key} in {env_path}")
            warn(msg)
            continue

        lowered = str(val).strip().lower()
        if any(tok in lowered for tok in placeholder_tokens):
            msg = f"placeholder value for {key} in {env_path}"
            if strict:
                fail(msg, f"replace placeholder for {key} in {env_path}")
            warn(msg)

try:
    cfg = load_config(str(config_path))
    info(
        f"OK config load: mode={cfg.mode}, instruments={len(cfg.instruments)}, "
        f"pair={cfg.entry_primary_exchange}+{cfg.entry_secondary_exchange}"
    )
except Exception as exc:
    fail(f"controller.config.load_config failed: {exc}", f"fix config/env and rerun: {config_path}")

info("done")
PY
