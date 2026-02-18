# Delta-Neutral Bot — Packaging + Onboarding

This repo is intended to be run through `scripts/` only.

## Quickstart (single source of truth)

From repo root:

```bash
cd /root/thevse/DN
./scripts/setup.sh --config config.yaml
```

Fill only env files required by exchanges from your `config.yaml`:
- if pair includes `extended`: fill `Extended/.env`
- if pair includes `variational`: fill `Variational/.env`
- if pair includes `nado`: fill `Nado/.env`

Then run checks and launch:

```bash
./scripts/doctor.sh --strict --config config.yaml
./scripts/run_enter.sh --safe --config config.yaml
CONFIRM_LIVE_TRADING=1 ./scripts/run_enter.sh --live --config config.yaml
```

## Scripts

### `./scripts/setup.sh`
Purpose:
- create venv
- install build tooling (`pip`, `setuptools`, `wheel`, `poetry-core`)
- install `requirements.txt`
- install local SDKs in editable mode if present:
  - `Extended/python_sdk`
  - `Nado/nado-python-sdk`
- copy `.env.example` -> `.env` (only if missing)

Usage:
```bash
./scripts/setup.sh [--config <path>] [--venv <path>]
```

### `./scripts/doctor.sh`
Purpose:
- detect selected exchanges from config (`entry.primary_exchange` + `entry.secondary_exchange`)
- validate only required imports/deps for selected exchanges
- validate required env files and required env vars for selected exchanges
- run `controller.config.load_config`

Usage:
```bash
./scripts/doctor.sh [--strict] [--config <path>] [--venv <path>]
```

Strict mode:
- fail-fast on missing required deps/env/config checks
- no warnings for non-selected exchanges
- example: for `extended+variational`, doctor does not require `nado_protocol`

### `./scripts/run_enter.sh`
Purpose:
- `--safe`: offline config validation only (no adapters, no network, no orders)
- `--live`: real run of `controller.scripts.enter_delta_neutral`

Usage:
```bash
./scripts/run_enter.sh [--safe] [--live] [--config <path>] [config-path]
```

Safety gate:
- live run is blocked unless **both**:
  - `--live`
  - `CONFIRM_LIVE_TRADING=1`

### `./scripts/run_verify.sh` (recommended)
Purpose:
- convenient wrapper for `controller.scripts.verify_order_placement`
- safe dry check by default

Usage:
```bash
./scripts/run_verify.sh --safe --exchange extended --config config.yaml
# live verify (gated)
CONFIRM_LIVE_TRADING=1 ./scripts/run_verify.sh --live --exchange extended --config config.yaml
```

## Troubleshooting

### Missing `poetry-core`
`setup.sh` installs it automatically. If installation failed:
```bash
./scripts/setup.sh --config config.yaml
```

### Editable SDK install failed
- if SDK is required by selected exchanges, `setup.sh` fails with remediation message
- fix by restoring local SDK directory and rerunning setup:
  - `Extended/python_sdk`
  - `Nado/nado-python-sdk`

### Wrong Python version
Use Python 3.12:
```bash
python3 --version
```

### Missing required env vars
Run strict doctor to get exact missing keys:
```bash
./scripts/doctor.sh --strict --config config.yaml
```

### `No module named 'controller'`
Use script wrappers from repo root instead of direct `python -m ...`.

## Self-verification commands (latest)

Commands run:
```bash
rm -rf /tmp/dn_clean_venv && cp -a /root/thevse/DN/venv /tmp/dn_clean_venv
./scripts/setup.sh --config config.yaml --venv /tmp/dn_clean_venv
./scripts/doctor.sh --strict --config config.yaml --venv /tmp/dn_clean_venv
VENV_DIR=/tmp/dn_clean_venv ./scripts/run_enter.sh --safe --config config.yaml
```

Results:
- `setup.sh`: PASS
- `doctor.sh --strict`: PASS
- `run_enter.sh --safe`: PASS
