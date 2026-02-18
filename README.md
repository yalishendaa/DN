# Delta-Neutral Bot (Simple Run)

## 0) Go to repo
```bash
cd /root/thevse/DN
```

## 1) Install once
```bash
./scripts/setup.sh --config ./config.yaml
```

## 2) Fill required env files (depends on exchanges in `config.yaml`)
- `extended` -> `./Extended/.env`
- `nado` -> `./Nado/.env`
- `variational` -> `./Variational/.env`

Minimal config checks:
- `entry.primary_exchange`, `entry.secondary_exchange`
- `entry.instrument`, `entry.direction`, `entry.size`, `entry.target_size`
- `instruments[].nado_product_id` is required if pair includes `nado`

## 3) Preflight check
```bash
./scripts/doctor.sh --strict --config ./config.yaml
```

## 4) Safe check (no network/orders)
```bash
./scripts/run_enter.sh --safe --config ./config.yaml
```

## 5) Real run
```bash
./scripts/run_enter.sh --live --config ./config.yaml
```

Direct command (same thing):
```bash
venv/bin/python -m controller.scripts.enter_delta_neutral --config ./config.yaml --live
```

## If it fails
- `No module named controller`: run from repo root (`/root/thevse/DN`).
- `nado_product_id обязателен`: add `instruments[].nado_product_id` in `config.yaml`.
- Variational 403/challenge: backend access issue (Cloudflare challenge), not local Python setup.
