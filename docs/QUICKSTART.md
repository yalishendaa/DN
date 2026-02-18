# QUICKSTART (5 minutes)

## 1) Setup
```bash
cd /root/thevse/DN
./scripts/setup.sh --config ./config.yaml
```

## 2) Edit `config.yaml` (minimum fields)
```bash
cd /root/thevse/DN
nano ./config.yaml
```

Required in `entry`:
- `instrument`
- `primary_exchange`
- `secondary_exchange`
- `direction`
- `size`
- `target_size`

Required in `instruments[]` for selected exchanges:
- `extended` -> `extended_market_name`
- `variational` -> `variational_underlying`
- `nado` -> `nado_product_id`

## 3) Fill only required `.env` files
If config uses `extended + variational`:
```bash
cd /root/thevse/DN
nano ./Extended/.env
nano ./Variational/.env
```

If config includes `nado`, also fill:
```bash
cd /root/thevse/DN
nano ./Nado/.env
```

## 4) Strict preflight
```bash
cd /root/thevse/DN
./scripts/doctor.sh --strict --config ./config.yaml
```

## 5) Offline safe run (no network, no exchange init, no orders)
```bash
cd /root/thevse/DN
./scripts/run_enter.sh --safe --config ./config.yaml
```

## 6) Real run (live gate)
Without gate env var, run is blocked.
```bash
cd /root/thevse/DN
./scripts/run_enter.sh --live --config ./config.yaml
```

Live-enabled command:
```bash
cd /root/thevse/DN
CONFIRM_LIVE_TRADING=1 ./scripts/run_enter.sh --live --config ./config.yaml
```
