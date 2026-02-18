# WHAT_YOU_WILL_SEE

Real examples from this repository (commands were executed locally).

## 1) `doctor --strict` success

Command:
```bash
cd /root/thevse/DN
./scripts/doctor.sh --strict --config ./config.yaml
```

Output:
```text
[doctor] repo: /root/thevse/DN
[doctor] python: Python 3.12.3
[doctor] config: /root/thevse/DN/./config.yaml
[doctor] mode: STRICT
[doctor] selected exchanges: extended, variational
[doctor] OK import: controller
[doctor] OK import: yaml
[doctor] OK import: dotenv
[doctor] OK import: x10
[doctor] OK import: curl_cffi
[doctor] OK import: eth_account
[doctor] OK env file: /root/thevse/DN/Extended/.env
[doctor] OK env file: /root/thevse/DN/Variational/.env
[doctor] OK config load: mode=monitor, instruments=1, pair=extended+variational
[doctor] done
```

## 2) `doctor --strict` failure example (Nado dependency missing)

Commands:
```bash
cat > /tmp/dn_nado_check.yaml <<'EOF_CFG'
mode: monitor
entry:
  instrument: BTC-PERP
  primary_exchange: nado
  secondary_exchange: extended
instruments:
  - symbol: BTC-PERP
    nado_product_id: 2
    extended_market_name: BTC-USD
extended:
  env_file: Extended/.env
  network: mainnet
nado:
  env_file: Nado/.env
  network: mainnet
  subaccount_name: default
EOF_CFG

cd /root/thevse/DN
./scripts/doctor.sh --strict --config /tmp/dn_nado_check.yaml
```

Output:
```text
[doctor] repo: /root/thevse/DN
[doctor] python: Python 3.12.3
[doctor] config: /tmp/dn_nado_check.yaml
[doctor] mode: STRICT
[doctor] selected exchanges: extended, nado
[doctor] OK import: controller
[doctor] OK import: yaml
[doctor] OK import: dotenv
[doctor] OK import: x10
[doctor] FAIL: missing Nado dependency 'nado_protocol' (No module named 'nado_protocol')
[doctor] remediation: run ./scripts/setup.sh --config /tmp/dn_nado_check.yaml; ensure Nado/nado-python-sdk is present
```

How to fix:
- install Nado SDK path/deps via setup:
```bash
cd /root/thevse/DN
./scripts/setup.sh --config /tmp/dn_nado_check.yaml
```

## 3) `run_enter --safe` success

Command:
```bash
cd /root/thevse/DN
./scripts/run_enter.sh --safe --config ./config.yaml
```

Output:
```text
[run] SAFE mode: offline validation only
[run] OK safe check: mode=monitor, instruments=1, pair=extended+variational
```

## 4) Live gate refusal without `CONFIRM_LIVE_TRADING=1`

Command:
```bash
cd /root/thevse/DN
./scripts/run_enter.sh --live --config ./config.yaml
```

Output:
```text
[run] FAIL: live run is blocked; set CONFIRM_LIVE_TRADING=1
[run] remediation: CONFIRM_LIVE_TRADING=1 ./scripts/run_enter.sh --live ./config.yaml
```

Command that enables live:
```bash
cd /root/thevse/DN
CONFIRM_LIVE_TRADING=1 ./scripts/run_enter.sh --live --config ./config.yaml
```
