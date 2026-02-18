# DN Configuration Reference

This file documents the `config.yaml` and `config.advanced.yaml` fields used by the delta-neutral runtime.

## 1) `config.yaml` (runtime config)

### Top-level fields

| Field | Type | Default | Used by | Notes |
|---|---|---|---|---|
| `mode` | `monitor` \| `auto` | `monitor` | `python -m controller` | `auto` can place orders; safety gate still required (`--live` + `CONFIRM_LIVE_TRADING=1`). |
| `cycle_interval_sec` | float > 0 | `10.0` | `controller` loop | Delay between cycles. |
| `max_retries` | int >= 0 | `3` | config model | Stored in config; no universal retry loop in controller core. |
| `backoff_base_sec` | float >= 0 | `1.0` | config model | Stored in config. |
| `log_level` | `DEBUG`/`INFO`/`WARNING`/`ERROR` | `INFO` | `controller` | Logging level. |
| `log_file` | string/null | `null` | `controller` | Optional log file path. |
| `order_post_only` | bool | `true` | `controller` auto actions | Maker-only behavior for auto actions. |
| `price_offset_pct` | float >= 0 | `0.01` | `delta_engine` | Price offset when generating rebalance orders. |

### Entry pair selection

| Field | Type | Default | Used by |
|---|---|---|---|
| `entry.primary_exchange` | `extended` \| `nado` \| `variational` | `extended` | `controller`, `enter_delta_neutral` |
| `entry.secondary_exchange` | `extended` \| `nado` \| `variational` | derived by primary (`extended->variational`, `nado->extended`, `variational->extended`) | `controller`, `enter_delta_neutral` |

### Instruments

`instruments` is required and must be non-empty.

Each item:

| Field | Required when | Used by |
|---|---|---|
| `symbol` | always | all runtimes |
| `extended_market_name` | `extended` in active pair | `ExtendedAdapter` |
| `nado_product_id` | `nado` in active pair | `NadoAdapter` |
| `variational_underlying` | `variational` in active pair | `VariationalAdapter` |

### Exchange sections

| Section | Field | Default | Used by |
|---|---|---|---|
| `extended` | `env_file` | `Extended/.env` | `ExtendedAdapter` |
| `extended` | `network` | `mainnet` | config + endpoint selection |
| `nado` | `env_file` | `Nado/.env` | `NadoAdapter` (optional integration) |
| `nado` | `network` | `mainnet` | `NadoAdapter` |
| `nado` | `subaccount_name` | `default` | `NadoAdapter` |
| `variational` | `env_file` | `Variational/.env` | `VariationalAdapter` |

### Risk section

| Field | Type | Default |
|---|---|---|
| `risk.max_delta_base` | float >= 0 | `0.01` |
| `risk.max_delta_usd` | float >= 0 | `1000.0` |
| `risk.max_order_size_base` | float > 0 | `0.05` |
| `risk.max_position_base` | float > 0 | `1.0` |
| `risk.min_balance_usd` | float >= 0 | `100.0` |

## 2) `entry` fields used by `enter_delta_neutral`

These live in `config.yaml` under `entry`.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `instrument` | string | first `instruments[0].symbol` | Trading symbol for the run. |
| `primary_exchange` | exchange name | `entry_primary_exchange` | First leg exchange. |
| `secondary_exchange` | exchange name | derived by primary | Hedge leg exchange. |
| `direction` | `long`/`short` | `long` | Direction on primary leg for open flow. |
| `size` | float | none | Per-order clip size. |
| `target_size` | float/null | null | Target cumulative size in open flow. |
| `post_only` | bool | `true` | Post-only for primary leg orders. |
| `slippage_pct` | float | `0.05` | Hedge price aggressiveness base. |
| `secondary_slippage_pct` | float | `slippage_pct` | Secondary-leg specific slippage. |
| `poll_interval` | float | `1.5` | Poll cadence while waiting for fills. |
| `reprice_interval_sec` | float | `30.0` | Reprice cadence for resting primary order. |
| `log_mode` | `full`/`compact` | `full` | Console verbosity mode. |

Additional optional knobs are also read in `enter_delta_neutral`, including:
- `offset_pct`, `offset_retry_pct`
- `close_offset_pct`, `close_offset_retry_pct`
- `ioc_min_cross_pct`
- `hedge_margin_buffer`, `close_min_notional`
- `post_only_fallback_factor`, `post_only_fallback_retries`, `post_only_fallback_max_pct`
- `hedge_confirm_timeout_sec`, `hedge_confirm_poll_sec`
- `hedge_retry_count`, `hedge_retry_slippage_mult`, `hedge_retry_max_slippage_pct`

## 3) `config.advanced.yaml`

`enter_delta_neutral` loads `config.advanced.yaml` (if present) and merges:
1. `advanced.entry` defaults
2. overridden by `config.yaml -> entry`

Use this for stable tuning defaults while keeping per-run values in `config.yaml`.

## 4) Minimal examples

### Minimal safe `config.yaml` (dry-run friendly)

```yaml
mode: monitor
entry:
  instrument: BTC-PERP
  primary_exchange: extended
  secondary_exchange: variational
  direction: long
  size: 0.001
  target_size: 0.001

instruments:
  - symbol: BTC-PERP
    extended_market_name: BTC-USD
    variational_underlying: BTC

extended:
  env_file: Extended/.env
  network: mainnet

variational:
  env_file: Variational/.env
```

### Optional advanced tuning (`config.advanced.yaml`)

```yaml
entry:
  post_only: true
  poll_interval: 0.5
  reprice_interval_sec: 15
  slippage_pct: 0.05
```
