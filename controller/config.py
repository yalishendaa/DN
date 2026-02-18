"""Конфигурация Delta-Neutral контроллера.

Загрузка из YAML + .env файлов.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import yaml

# ---------------------------------------------------------------------------
# Exchange names
# ---------------------------------------------------------------------------


ExchangeName = Literal["extended", "nado", "variational"]
_SUPPORTED_EXCHANGES: set[str] = {"extended", "nado", "variational"}
_DEFAULT_SECONDARY: dict[ExchangeName, ExchangeName] = {
    "extended": "variational",
    "nado": "extended",
    "variational": "extended",
}

# ---------------------------------------------------------------------------
# Instrument mapping
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InstrumentConfig:
    """Маппинг одного логического инструмента на биржи."""

    symbol: str  # Логический символ (напр. BTC-PERP)
    extended_market_name: str | None = None  # market_name на Extended (напр. BTC-USD)
    nado_product_id: int | None = None  # product_id на Nado (напр. 2)
    variational_underlying: str | None = None  # underlying на Variational (напр. BTC)


# ---------------------------------------------------------------------------
# Risk limits
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RiskLimits:
    """Лимиты риска для дельта-нейтральной стратегии."""

    max_delta_base: float = 0.01  # Макс. дисбаланс в базовом активе
    max_delta_usd: float = 1000.0  # Макс. дисбаланс в USD
    max_order_size_base: float = 0.05  # Макс. размер одного ордера
    max_position_base: float = 1.0  # Макс. позиция на одну биржу
    min_balance_usd: float = 100.0  # Мин. баланс на бирже для торговли


# ---------------------------------------------------------------------------
# Main config
# ---------------------------------------------------------------------------


@dataclass
class ControllerConfig:
    """Конфигурация Delta-Neutral контроллера."""

    # Режим
    mode: Literal["monitor", "auto"] = "monitor"

    # Инструменты
    instruments: list[InstrumentConfig] = field(default_factory=list)

    # Риски
    risk: RiskLimits = field(default_factory=RiskLimits)

    # Цикл
    cycle_interval_sec: float = 10.0  # Интервал между циклами
    max_retries: int = 3  # Макс. попыток на один API-вызов
    backoff_base_sec: float = 1.0  # Базовая задержка ретрая

    # Extended
    extended_env_file: str = ""
    extended_network: str = "mainnet"

    # Nado
    nado_env_file: str = ""
    nado_network: str = "mainnet"
    nado_subaccount_name: str = "default"

    # Variational
    variational_env_file: str = ""

    # Entry pair
    entry_primary_exchange: ExchangeName = "extended"
    entry_secondary_exchange: ExchangeName = "variational"

    # Логирование
    log_level: str = "INFO"
    log_file: str | None = None

    # Ордера (для auto-режима)
    order_post_only: bool = True
    price_offset_pct: float = 0.01  # Сдвиг цены от ref (% от ref price)


class ConfigValidationError(ValueError):
    """Ошибка валидации конфигурации контроллера."""


def _as_non_empty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigValidationError(f"Поле '{field}' должно быть непустой строкой")
    return value.strip()


def _as_optional_non_empty_string(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _as_non_empty_string(value, field)


def _as_float(value: Any, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        raise ConfigValidationError(f"Поле '{field}' должно быть числом") from e


def _as_int(value: Any, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as e:
        raise ConfigValidationError(f"Поле '{field}' должно быть целым числом") from e


def _as_exchange_name(value: Any, field: str) -> ExchangeName:
    name = _as_non_empty_string(value, field).lower()
    if name not in _SUPPORTED_EXCHANGES:
        allowed = ", ".join(sorted(_SUPPORTED_EXCHANGES))
        raise ConfigValidationError(f"Поле '{field}' должно быть одним из: {allowed}")
    return cast(ExchangeName, name)


def _resolve_path(path_value: str, dn_root: Path) -> Path:
    path = Path(path_value)
    if not path.is_absolute():
        path = dn_root / path
    return path


def _validate_instrument_mapping(
    inst: InstrumentConfig,
    idx: int,
    primary: ExchangeName,
    secondary: ExchangeName,
) -> None:
    required = {primary, secondary}
    if "extended" in required and not inst.extended_market_name:
        raise ConfigValidationError(
            f"instruments[{idx}].extended_market_name обязателен для пары {primary}+{secondary}"
        )
    if "nado" in required and inst.nado_product_id is None:
        raise ConfigValidationError(
            f"instruments[{idx}].nado_product_id обязателен для пары {primary}+{secondary}"
        )
    if "variational" in required and not inst.variational_underlying:
        raise ConfigValidationError(
            f"instruments[{idx}].variational_underlying обязателен для пары "
            f"{primary}+{secondary}"
        )


def _require_non_negative(value: float, field: str) -> None:
    if value < 0:
        raise ConfigValidationError(f"Поле '{field}' должно быть >= 0")


def _require_positive(value: float, field: str) -> None:
    if value <= 0:
        raise ConfigValidationError(f"Поле '{field}' должно быть > 0")


def load_config(config_path: str = "config.yaml") -> ControllerConfig:
    """Загрузить конфигурацию из YAML файла.

    Args:
        config_path: Путь к YAML конфигу (абсолютный или относительно DN/).

    Returns:
        ControllerConfig: Готовая конфигурация.
    """
    path = Path(config_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / config_path

    if not path.exists():
        raise FileNotFoundError(f"Конфиг не найден: {path}")

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ConfigValidationError("Корневой YAML-объект должен быть mapping (dict)")

    entry_raw = raw.get("entry", {})
    if entry_raw and not isinstance(entry_raw, dict):
        raise ConfigValidationError("Секция 'entry' должна быть mapping (dict)")
    entry = entry_raw if isinstance(entry_raw, dict) else {}

    primary_exchange = _as_exchange_name(
        entry.get("primary_exchange", "extended"),
        "entry.primary_exchange",
    )
    secondary_raw = entry.get("secondary_exchange")
    if secondary_raw is None or (isinstance(secondary_raw, str) and not secondary_raw.strip()):
        secondary_exchange = _DEFAULT_SECONDARY[primary_exchange]
    else:
        secondary_exchange = _as_exchange_name(secondary_raw, "entry.secondary_exchange")
    if primary_exchange == secondary_exchange:
        raise ConfigValidationError("entry.primary_exchange и entry.secondary_exchange должны отличаться")

    extended_raw = raw.get("extended", {})
    if not isinstance(extended_raw, dict):
        raise ConfigValidationError("Секция 'extended' должна быть mapping (dict)")

    nado_raw = raw.get("nado", {})
    if not isinstance(nado_raw, dict):
        raise ConfigValidationError("Секция 'nado' должна быть mapping (dict)")

    variational_raw = raw.get("variational", {})
    if not isinstance(variational_raw, dict):
        variational_raw = {}

    # Загружаем .env файлы
    dn_root = Path(__file__).resolve().parent.parent
    ext_env = _as_non_empty_string(extended_raw.get("env_file", "Extended/.env"), "extended.env_file")
    nado_env = _as_non_empty_string(nado_raw.get("env_file", "Nado/.env"), "nado.env_file")
    variational_env_raw = variational_raw.get("env_file", "Variational/.env")
    variational_env = (
        _as_non_empty_string(variational_env_raw, "variational.env_file")
        if variational_env_raw is not None
        else ""
    )

    ext_env_path = _resolve_path(ext_env, dn_root)
    nado_env_path = _resolve_path(nado_env, dn_root)
    variational_env_path = _resolve_path(variational_env, dn_root) if variational_env else None

    active_exchanges = {primary_exchange, secondary_exchange}
    if "extended" in active_exchanges and not ext_env_path.exists():
        raise FileNotFoundError(f"Файл окружения Extended не найден: {ext_env_path}")
    if "nado" in active_exchanges and not nado_env_path.exists():
        raise FileNotFoundError(f"Файл окружения Nado не найден: {nado_env_path}")

    # Инструменты
    raw_instruments = raw.get("instruments", [])
    if not isinstance(raw_instruments, list) or not raw_instruments:
        raise ConfigValidationError("В конфиге должен быть непустой список 'instruments'")

    instruments: list[InstrumentConfig] = []
    seen_symbols: set[str] = set()
    for idx, item in enumerate(raw_instruments):
        if not isinstance(item, dict):
            raise ConfigValidationError(f"instruments[{idx}] должен быть mapping (dict)")
        symbol = _as_non_empty_string(item.get("symbol"), f"instruments[{idx}].symbol")
        market_name = (
            _as_optional_non_empty_string(
                item.get("extended_market_name"),
                f"instruments[{idx}].extended_market_name",
            )
            if "extended_market_name" in item
            else None
        )
        product_id = (
            _as_int(item.get("nado_product_id"), f"instruments[{idx}].nado_product_id")
            if "nado_product_id" in item and item.get("nado_product_id") is not None
            else None
        )
        if product_id is not None and product_id <= 0:
            raise ConfigValidationError(f"instruments[{idx}].nado_product_id должен быть > 0")
        variational_underlying = (
            _as_optional_non_empty_string(
                item.get("variational_underlying"),
                f"instruments[{idx}].variational_underlying",
            )
            if "variational_underlying" in item
            else None
        )
        if symbol in seen_symbols:
            raise ConfigValidationError(f"Дублирующийся инструмент symbol='{symbol}'")
        seen_symbols.add(symbol)

        inst = InstrumentConfig(
            symbol=symbol,
            extended_market_name=market_name,
            nado_product_id=product_id,
            variational_underlying=variational_underlying,
        )
        _validate_instrument_mapping(inst, idx, primary_exchange, secondary_exchange)
        instruments.append(inst)

    # Риски
    risk_raw = raw.get("risk", {})
    if not isinstance(risk_raw, dict):
        raise ConfigValidationError("Секция 'risk' должна быть mapping (dict)")

    max_delta_base = _as_float(risk_raw.get("max_delta_base", 0.01), "risk.max_delta_base")
    max_delta_usd = _as_float(risk_raw.get("max_delta_usd", 1000.0), "risk.max_delta_usd")
    max_order_size_base = _as_float(
        risk_raw.get("max_order_size_base", 0.05),
        "risk.max_order_size_base",
    )
    max_position_base = _as_float(
        risk_raw.get("max_position_base", 1.0),
        "risk.max_position_base",
    )
    min_balance_usd = _as_float(risk_raw.get("min_balance_usd", 100.0), "risk.min_balance_usd")

    _require_non_negative(max_delta_base, "risk.max_delta_base")
    _require_non_negative(max_delta_usd, "risk.max_delta_usd")
    _require_positive(max_order_size_base, "risk.max_order_size_base")
    _require_positive(max_position_base, "risk.max_position_base")
    _require_non_negative(min_balance_usd, "risk.min_balance_usd")

    risk = RiskLimits(
        max_delta_base=max_delta_base,
        max_delta_usd=max_delta_usd,
        max_order_size_base=max_order_size_base,
        max_position_base=max_position_base,
        min_balance_usd=min_balance_usd,
    )

    mode = str(raw.get("mode", "monitor")).lower()
    if mode not in ("monitor", "auto"):
        raise ConfigValidationError("mode должен быть 'monitor' или 'auto'")

    cycle_interval_sec = _as_float(raw.get("cycle_interval_sec", 10.0), "cycle_interval_sec")
    max_retries = _as_int(raw.get("max_retries", 3), "max_retries")
    backoff_base_sec = _as_float(raw.get("backoff_base_sec", 1.0), "backoff_base_sec")
    _require_positive(cycle_interval_sec, "cycle_interval_sec")
    _require_non_negative(float(max_retries), "max_retries")
    _require_non_negative(backoff_base_sec, "backoff_base_sec")

    ext_network = str(extended_raw.get("network", "mainnet")).lower()
    if ext_network not in ("mainnet", "testnet"):
        raise ConfigValidationError("extended.network должен быть 'mainnet' или 'testnet'")

    nado_network = str(nado_raw.get("network", "mainnet")).lower()
    if nado_network not in ("mainnet", "testnet", "devnet"):
        raise ConfigValidationError("nado.network должен быть 'mainnet', 'testnet' или 'devnet'")

    nado_subaccount_name = _as_non_empty_string(
        nado_raw.get("subaccount_name", "default"),
        "nado.subaccount_name",
    )

    log_level = str(raw.get("log_level", "INFO")).upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ConfigValidationError("log_level должен быть одним из: DEBUG, INFO, WARNING, ERROR")

    order_post_only = raw.get("order_post_only", True)
    if not isinstance(order_post_only, bool):
        raise ConfigValidationError("order_post_only должен быть bool")
    price_offset_pct = _as_float(raw.get("price_offset_pct", 0.01), "price_offset_pct")
    _require_non_negative(price_offset_pct, "price_offset_pct")

    log_file = raw.get("log_file")
    if log_file is not None and not isinstance(log_file, str):
        raise ConfigValidationError("log_file должен быть строкой или null")

    cfg = ControllerConfig(
        mode=mode,  # type: ignore[arg-type]
        instruments=instruments,
        risk=risk,
        cycle_interval_sec=cycle_interval_sec,
        max_retries=max_retries,
        backoff_base_sec=backoff_base_sec,
        extended_env_file=str(ext_env_path),
        extended_network=ext_network,
        nado_env_file=str(nado_env_path),
        nado_network=nado_network,
        nado_subaccount_name=nado_subaccount_name,
        variational_env_file=str(variational_env_path) if variational_env_path else "",
        entry_primary_exchange=primary_exchange,
        entry_secondary_exchange=secondary_exchange,
        log_level=log_level,
        log_file=log_file,
        order_post_only=order_post_only,
        price_offset_pct=price_offset_pct,
    )

    return cfg
