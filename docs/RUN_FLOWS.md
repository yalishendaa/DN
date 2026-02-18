# RUN_FLOWS

This document shows practical run modes with exact commands.

## Before any flow
Use explicit config path in all commands:
- `./config.yaml`

Files you edit:
- `./config.yaml`
- `./Extended/.env` when selected exchanges include `extended`
- `./Variational/.env` when selected exchanges include `variational`
- `./Nado/.env` when selected exchanges include `nado`

Edit commands:
```bash
cd /root/thevse/DN
nano ./config.yaml
nano ./Extended/.env
nano ./Variational/.env
# only if nado is selected in config:
nano ./Nado/.env
```

Your `./config.yaml` must at minimum define:
- `entry.instrument`
- `entry.primary_exchange`
- `entry.secondary_exchange`
- `entry.direction`
- `entry.size`
- `entry.target_size`
- `instruments[]` mapping fields required by selected exchanges:
  - `extended` -> `extended_market_name`
  - `variational` -> `variational_underlying`
  - `nado` -> `nado_product_id`

Required `.env` files depend on selected exchanges:
- includes `extended` -> `Extended/.env`
- includes `variational` -> `Variational/.env`
- includes `nado` -> `Nado/.env`

---

## 1) Safe check (offline)

Command:
```bash
cd /root/thevse/DN
./scripts/run_enter.sh --safe --config ./config.yaml
```

Required files:
- `./config.yaml`

Expected terminal behavior:
- prints `SAFE mode: offline validation only`
- prints `OK safe check: ...`
- does not initialize adapters
- does not call exchanges
- does not place orders

Logs/results:
- console only
- no trading writes

---

## 2) Dry verification (recommended, if available)

Command:
```bash
cd /root/thevse/DN
./scripts/run_verify.sh --safe --exchange extended --config ./config.yaml
```

Required files:
- `./config.yaml`

Expected terminal behavior:
- prints `running SAFE verify (dry-run)`
- shows verification table with `DRY-RUN` operations
- no adapter initialization/network/order execution in verify dry-run path

Logs/results:
- console summary only
- no real order placement

---

## 3) Enter delta-neutral run (live path)

Command:
```bash
cd /root/thevse/DN
CONFIRM_LIVE_TRADING=1 ./scripts/run_enter.sh --live --config ./config.yaml
```

Required files:
- `./config.yaml`
- required `.env` files for selected exchanges

Expected terminal behavior:
- starts `controller.scripts.enter_delta_neutral`
- initializes selected exchange adapters
- runs entry/hedge flow from config
- may prompt on close path if positions already exist

Logs/results:
- console logs
- trade CSV updates in `logs/trades.csv`

---

## 4) Live safety gate behavior

### A) Missing `--live`
```bash
cd /root/thevse/DN
./scripts/run_enter.sh --config ./config.yaml
```
Behavior:
- blocked immediately
- explains to rerun with `--live` and `CONFIRM_LIVE_TRADING=1`

### B) Missing `CONFIRM_LIVE_TRADING=1`
```bash
cd /root/thevse/DN
./scripts/run_enter.sh --live --config ./config.yaml
```
Behavior:
- blocked immediately
- prints explicit gate message

### C) Live enabled (explicit opt-in)
```bash
cd /root/thevse/DN
CONFIRM_LIVE_TRADING=1 ./scripts/run_enter.sh --live --config ./config.yaml
```
Behavior:
- command proceeds to real run

---

## Strict environment/config check

Command:
```bash
cd /root/thevse/DN
./scripts/doctor.sh --strict --config ./config.yaml
```

Expected behavior:
- detects selected exchanges from config
- validates only required dependencies for those exchanges
- validates required env files/vars for those exchanges
- fails fast with remediation text if something required is missing
