"""Адаптер Variational — реализация ExchangeAdapter.

Лёгкий HTTP-клиент к omni.variational.io без зависимости на исходный
бот в `variational/`.
"""

from __future__ import annotations

import logging
import os
import inspect
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from eth_account import Account
from eth_account.messages import encode_defunct

try:
    from curl_cffi import AsyncSession as CurlAsyncSession
except ImportError:  # pragma: no cover - зависит от окружения запуска
    CurlAsyncSession = None  # type: ignore[assignment]

try:
    import aiohttp
except ImportError:  # pragma: no cover - зависит от окружения запуска
    aiohttp = None  # type: ignore[assignment]

from controller.interface import ExchangeAdapter
from controller.models import (
    NormalizedBalance,
    NormalizedOrder,
    NormalizedPosition,
    PlacedOrderResult,
    PositionDirection,
    Side,
)

logger = logging.getLogger("dn.variational")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _find_numeric_field(payload: Any, keys: set[str]) -> float | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys:
                parsed = _to_float(value, default=float("nan"))
                if parsed == parsed:  # not NaN
                    return parsed
            nested = _find_numeric_field(value, keys)
            if nested is not None:
                return nested
    elif isinstance(payload, list):
        for value in payload:
            nested = _find_numeric_field(value, keys)
            if nested is not None:
                return nested
    return None


def _prefix_var(order_id: str) -> str:
    return order_id if order_id.startswith("var:") else f"var:{order_id}"


def _strip_var(order_id: str) -> str:
    return order_id[4:] if order_id.startswith("var:") else order_id


_SENSITIVE_FIELDS = {
    "authorization",
    "cookie",
    "set-cookie",
    "token",
    "access_token",
    "refresh_token",
    "signed_message",
    "signature",
    "private_key",
}
_SENSITIVE_JSON_RE = re.compile(
    r'("(?:authorization|cookie|set-cookie|token|access_token|refresh_token|signed_message|signature|private_key)"\s*:\s*")[^"]*(")',
    flags=re.IGNORECASE,
)
_SENSITIVE_QUERY_RE = re.compile(
    r"((?:authorization|cookie|set-cookie|token|access_token|refresh_token|signed_message|signature|private_key)\s*=\s*)[^&\s]+",
    flags=re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-+/=]+", flags=re.IGNORECASE)


def _sanitize_text(value: str) -> str:
    text = _BEARER_RE.sub(r"\1<redacted>", value)
    text = _SENSITIVE_JSON_RE.sub(r"\1<redacted>\2", text)
    text = _SENSITIVE_QUERY_RE.sub(r"\1<redacted>", text)
    return text


def _sanitize_for_log(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            if str(key).lower() in _SENSITIVE_FIELDS:
                redacted[key] = "<redacted>"
            else:
                redacted[key] = _sanitize_for_log(item)
        return redacted
    if isinstance(value, list):
        return [_sanitize_for_log(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_for_log(item) for item in value)
    if isinstance(value, str):
        return _sanitize_text(value)
    return value


def _safe_str(value: Any) -> str:
    if isinstance(value, BaseException):
        return _sanitize_text(str(value))
    return str(_sanitize_for_log(value))


class VariationalClient:
    """Минимальный API-клиент Variational."""

    BASE_URL = "https://omni.variational.io/api"

    def __init__(self, private_key: str):
        self._account = Account.from_key(private_key)
        self.address = self._account.address
        self._session: Any | None = None

    @property
    def session(self) -> Any:
        if self._session is None:
            raise RuntimeError("VariationalClient is not initialized")
        return self._session

    async def initialize(self) -> None:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/136.0.0.0 Safari/537.36"
            ),
            "Origin": "https://omni.variational.io",
            "Referer": "https://omni.variational.io/",
        }
        if CurlAsyncSession is not None:
            self._session = CurlAsyncSession(
                headers=headers,
                impersonate="chrome",
            )
        elif aiohttp is not None:
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(headers=headers, timeout=timeout)
            logger.warning("curl_cffi not installed; using aiohttp fallback session for Variational")
        else:
            raise RuntimeError(
                "Для VariationalAdapter требуется curl_cffi или aiohttp. "
                "Установите зависимости из requirements.txt."
            )
        try:
            sign_data = await self.get_sign_data()
            msg = encode_defunct(text=sign_data)
            signature = self._account.sign_message(msg).signature.hex().removeprefix("0x")
            await self.auth_login(signature)
        except Exception:
            await self.close()
            raise

    async def _response_status(self, resp: Any) -> int:
        status = getattr(resp, "status_code", None)
        if status is None:
            status = getattr(resp, "status", 0)
        try:
            return int(status)
        except Exception:
            return 0

    async def _response_text(self, resp: Any) -> str:
        text_attr = getattr(resp, "text", "")
        if callable(text_attr):
            text_result = text_attr()
            if inspect.isawaitable(text_result):
                text_result = await text_result
            return str(text_result)
        if isinstance(text_attr, str):
            return text_attr
        return str(text_attr)

    async def _response_json(self, resp: Any) -> Any:
        json_attr = getattr(resp, "json", None)
        if not callable(json_attr):
            raise RuntimeError("JSON parser is not available in response object")
        json_result = json_attr()
        if inspect.isawaitable(json_result):
            json_result = await json_result
        return json_result

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.BASE_URL}{path}"
        resp = await self.session.request(
            method=method.upper(),
            url=url,
            params=params,
            json=payload,
        )
        status = await self._response_status(resp)
        if status >= 400:
            response_text = _sanitize_text(await self._response_text(resp))
            raise RuntimeError(f"{path}: HTTP {status} {response_text}")
        try:
            return await self._response_json(resp)
        except Exception as exc:
            response_text = _sanitize_text(await self._response_text(resp))
            raise RuntimeError(f"{path}: invalid JSON response {response_text}") from exc

    async def get_sign_data(self) -> str:
        resp = await self.session.post(
            url=f"{self.BASE_URL}/auth/generate_signing_data",
            json={"address": self.address},
        )
        sign_data = await self._response_text(resp)
        if not isinstance(sign_data, str) or not sign_data.startswith("omni.variational.io wants you to"):
            raise RuntimeError(f"Failed to get sign data: {_sanitize_text(sign_data)}")
        return sign_data

    async def auth_login(self, signature: str) -> dict[str, Any]:
        data = await self._request_json(
            "POST",
            "/auth/login",
            payload={"address": self.address, "signed_message": signature},
        )
        if not isinstance(data, dict) or data.get("token") is None:
            raise RuntimeError(f"Failed to auth login: {_safe_str(data)}")
        return data

    async def get_portfolio(self) -> dict[str, Any]:
        data = await self._request_json(
            "GET",
            "/portfolio",
            params={"compute_margin": "true"},
        )
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected portfolio payload: {_safe_str(data)}")
        return data

    async def get_balance_details(self) -> dict[str, Any]:
        data = await self._request_json("GET", "/settlement_pools/details")
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected settlement details payload: {_safe_str(data)}")
        return data

    async def get_positions(self) -> list[dict[str, Any]]:
        data = await self._request_json("GET", "/positions")
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected positions payload: {_safe_str(data)}")
        return [item for item in data if isinstance(item, dict)]

    async def get_orders(self) -> list[dict[str, Any]]:
        # API /orders/v2 не принимает status в query (400 Matching variant not found).
        # Все ордера запрашиваем без status; фильтрация по статусу — в get_open_orders.
        params: dict[str, str | int] = {
            "order_by": "created_at",
            "order": "desc",
            "limit": 100,
            "offset": 0,
        }
        data = await self._request_json("GET", "/orders/v2", params=params)
        if not isinstance(data, dict) or not isinstance(data.get("result"), list):
            raise RuntimeError(f"Unexpected orders payload: {_safe_str(data)}")
        return [item for item in data["result"] if isinstance(item, dict)]

    async def get_supported_assets(self) -> dict[str, Any]:
        data = await self._request_json("GET", "/metadata/supported_assets")
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected supported_assets payload: {_safe_str(data)}")
        return data

    async def get_indicative(self, underlying: str, amount: float) -> dict[str, Any]:
        data = await self._request_json(
            "POST",
            "/quotes/indicative",
            payload={
                "instrument": {
                    "underlying": underlying,
                    "instrument_type": "perpetual_future",
                    "settlement_asset": "USDC",
                    "funding_interval_s": 3600,
                },
                "qty": str(amount),
            },
        )
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected indicative payload: {_safe_str(data)}")
        return data

    async def create_limit_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = await self._request_json("POST", "/orders/new/limit", payload=payload)
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected create_order payload: {_safe_str(data)}")
        return data

    async def cancel_order(self, rfq_id: str) -> dict[str, Any]:
        data = await self._request_json(
            "POST",
            "/orders/cancel",
            payload={"rfq_id": rfq_id},
        )
        # API может вернуть JSON null при успешной отмене.
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected cancel_order payload: {_safe_str(data)}")
        return data


class VariationalAdapter(ExchangeAdapter):
    """Адаптер Variational для контроллера."""

    def __init__(
        self,
        env_file: str | None = None,
        instrument_map: dict[str, str] | None = None,
    ):
        self._env_file = env_file or "Variational/.env"
        self._instrument_map = instrument_map or {}
        self._client: VariationalClient | None = None
        self._min_qty_cache: dict[str, float] = {}

    @property
    def name(self) -> str:
        return "variational"

    @property
    def client(self) -> VariationalClient:
        if self._client is None:
            raise RuntimeError("Адаптер не инициализирован. Вызовите initialize() первым.")
        return self._client

    def _resolve_env_path(self) -> Path:
        env_path = Path(self._env_file)
        if env_path.is_absolute():
            return env_path
        dn_root = Path(__file__).resolve().parent.parent
        return dn_root / env_path

    def _to_underlying(self, instrument: str) -> str:
        underlying = self._instrument_map.get(instrument)
        if not underlying:
            raise ValueError(
                f"Инструмент '{instrument}' не найден в instrument_map. "
                f"Доступные: {list(self._instrument_map.keys())}"
            )
        return underlying

    async def _get_min_qty(self, underlying: str) -> float:
        cached = self._min_qty_cache.get(underlying)
        if cached is not None:
            return cached
        min_qty = 0.001
        try:
            assets = await self.client.get_supported_assets()
            asset_info_raw = assets.get(underlying)
            asset_info = asset_info_raw[0] if isinstance(asset_info_raw, list) and asset_info_raw else {}
            spot_price = _to_float(asset_info.get("price"), 0.0) if isinstance(asset_info, dict) else 0.0
            quote_amount = max(0.001, (10.0 / spot_price) if spot_price > 0 else 0.01)
            indicative = await self.client.get_indicative(underlying, quote_amount)
            qty_limits = indicative.get("qty_limits", {})
            if isinstance(qty_limits, dict):
                bid = qty_limits.get("bid", {})
                ask = qty_limits.get("ask", {})
                if isinstance(bid, dict):
                    min_qty = max(min_qty, _to_float(bid.get("min_qty"), 0.0))
                if isinstance(ask, dict):
                    min_qty = max(min_qty, _to_float(ask.get("min_qty"), 0.0))
        except Exception as exc:
            logger.warning("Failed to get min_qty for %s: %s", underlying, _safe_str(exc))
        self._min_qty_cache[underlying] = max(min_qty, 0.0)
        return self._min_qty_cache[underlying]

    async def initialize(self) -> None:
        env_path = self._resolve_env_path()
        if not env_path.exists():
            raise FileNotFoundError(f"Файл окружения Variational не найден: {env_path}")

        load_dotenv(env_path)
        private_key = os.environ.get("VARIATIONAL_PRIVATE_KEY", "").strip()
        if not private_key:
            raise ValueError("VARIATIONAL_PRIVATE_KEY не задан в окружении/файле .env")

        try:
            client = VariationalClient(private_key=private_key)
        except Exception as exc:
            raise ValueError("VARIATIONAL_PRIVATE_KEY имеет некорректный формат") from exc
        await client.initialize()
        self._client = client
        logger.info("Variational adapter initialized (env=%s)", env_path)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
            logger.info("Variational adapter closed")

    async def get_balance(self) -> NormalizedBalance:
        portfolio = await self.client.get_portfolio()
        equity = _find_numeric_field(
            portfolio,
            {"balance", "equity", "total_equity", "account_equity"},
        )
        available = _find_numeric_field(
            portfolio,
            {
                "available",
                "available_balance",
                "available_for_trade",
                "free_collateral",
                "health",
                "withdrawable_balance",
            },
        )
        if available is None:
            details = await self.client.get_balance_details()
            available = _find_numeric_field(
                details,
                {
                    "available",
                    "available_balance",
                    "available_for_trade",
                    "free_collateral",
                    "health",
                    "withdrawable_balance",
                },
            )

        eq = equity if equity is not None else 0.0
        av = available if available is not None else eq
        return NormalizedBalance(equity=eq, available=av, currency="USD")

    async def get_position(self, instrument: str) -> NormalizedPosition:
        underlying = self._to_underlying(instrument)
        positions = await self.client.get_positions()
        position = next(
            (
                pos
                for pos in positions
                if str(
                    pos.get("position_info", {})
                    .get("instrument", {})
                    .get("underlying", "")
                ).upper()
                == underlying.upper()
            ),
            None,
        )
        if not position:
            return NormalizedPosition(
                instrument=instrument,
                size=0.0,
                direction=PositionDirection.FLAT,
            )

        info = position.get("position_info", {})
        qty = _to_float(info.get("qty"), 0.0)
        direction = PositionDirection.FLAT
        if qty > 0:
            direction = PositionDirection.LONG
        elif qty < 0:
            direction = PositionDirection.SHORT

        mark = _to_float(info.get("mark_price"), 0.0)
        if mark <= 0:
            mark = await self.get_reference_price(instrument)

        return NormalizedPosition(
            instrument=instrument,
            size=qty,
            direction=direction,
            entry_price=_to_float(info.get("avg_entry_price"), 0.0),
            mark_price=mark,
            unrealised_pnl=_to_float(
                info.get("unrealized_pnl", info.get("unrealised_pnl", info.get("pnl", 0.0))),
                0.0,
            ),
        )

    async def get_open_orders(self, instrument: str) -> list[NormalizedOrder]:
        underlying = self._to_underlying(instrument)
        orders = await self.client.get_orders()
        open_statuses = {"pending", "open", "opened", "working", "new", "partially_filled"}

        result: list[NormalizedOrder] = []
        for order in orders:
            status = str(order.get("status", "")).lower()
            if status and status not in open_statuses:
                continue

            order_underlying = str(order.get("instrument", {}).get("underlying", ""))
            if order_underlying.upper() != underlying.upper():
                continue

            order_id = str(order.get("rfq_id") or order.get("id") or "")
            if not order_id:
                continue

            side_raw = str(order.get("side", "buy")).lower()
            side = Side.BUY if side_raw == "buy" else Side.SELL
            amount = _to_float(order.get("qty", order.get("amount")), 0.0)
            filled = _to_float(order.get("filled_qty", order.get("filled", 0.0)), 0.0)
            price = _to_float(order.get("limit_price", order.get("price", 0.0)), 0.0)
            result.append(
                NormalizedOrder(
                    id=_prefix_var(order_id),
                    instrument=instrument,
                    side=side,
                    price=price,
                    amount=max(0.0, amount),
                    filled=max(0.0, filled),
                    post_only=bool(order.get("post_only", False)),
                    reduce_only=bool(order.get("is_reduce_only", order.get("reduce_only", False))),
                )
            )
        return result

    async def get_reference_price(self, instrument: str) -> float:
        bid, ask = await self.get_best_bid_ask(instrument)
        if bid > 0 and ask > 0:
            return (bid + ask) / 2
        return bid or ask

    async def get_best_bid_ask(self, instrument: str) -> tuple[float, float]:
        underlying = self._to_underlying(instrument)
        qty = max(await self._get_min_qty(underlying), 0.001)
        indicative = await self.client.get_indicative(underlying, qty)
        bid = _to_float(indicative.get("bid"), 0.0)
        ask = _to_float(indicative.get("ask"), 0.0)
        return bid, ask

    async def place_limit_order(
        self,
        instrument: str,
        side: Side,
        price: float,
        amount: float,
        post_only: bool = True,
        reduce_only: bool = False,
        external_id: str | None = None,
    ) -> PlacedOrderResult:
        try:
            if price <= 0:
                return PlacedOrderResult(id="", success=False, error="price должен быть > 0")
            if amount <= 0:
                return PlacedOrderResult(id="", success=False, error="amount должен быть > 0")

            underlying = self._to_underlying(instrument)
            min_qty = await self._get_min_qty(underlying)
            if amount + 1e-12 < min_qty:
                return PlacedOrderResult(
                    id="",
                    success=False,
                    error=f"amount={amount} меньше минимального размера {min_qty}",
                )

            payload = {
                "order_type": "limit",
                "limit_price": str(price),
                "side": side.value,
                "instrument": {
                    "underlying": underlying,
                    "instrument_type": "perpetual_future",
                    "settlement_asset": "USDC",
                    "funding_interval_s": 3600,
                },
                "qty": str(amount),
                "is_auto_resize": False,
                "use_mark_price": False,
                "is_reduce_only": reduce_only,
            }
            # На стороне API post_only может быть не обязательным, но сохраняем параметр
            # для прозрачности входных данных в логах.
            if external_id:
                payload["client_order_id"] = external_id
            if not post_only:
                payload["post_only"] = False

            response = await self.client.create_limit_order(payload)
            order_id = str(response.get("rfq_id") or "")
            if not order_id:
                return PlacedOrderResult(
                    id="",
                    success=False,
                    error=f"unexpected order response: {_safe_str(response)}",
                )
            logger.info(
                "Order placed on Variational: %s %s %s @ %s, id=%s",
                side.value,
                amount,
                instrument,
                price,
                order_id,
            )
            return PlacedOrderResult(id=_prefix_var(order_id), success=True)
        except Exception as exc:
            logger.error("Failed to place order on Variational: %s", _safe_str(exc))
            return PlacedOrderResult(id="", success=False, error=_safe_str(exc))

    async def cancel_order(self, instrument: str, order_id: str) -> bool:
        try:
            _ = instrument  # interface compatibility
            raw_id = _strip_var(order_id)
            response = await self.client.cancel_order(raw_id)
            if response.get("status") in {"success", "cancelled"}:
                logger.info("Order cancelled on Variational: %s", order_id)
                return True
            # API может вернуть пустой объект при 200.
            if response == {}:
                logger.info("Order cancelled on Variational: %s", order_id)
                return True
            logger.warning(
                "Unexpected cancel response on Variational for %s: %s",
                order_id,
                _safe_str(response),
            )
            return True
        except Exception as exc:
            logger.error("Failed to cancel order %s on Variational: %s", order_id, _safe_str(exc))
            return False

    async def cancel_all_orders(self, instrument: str) -> int:
        try:
            orders = await self.get_open_orders(instrument)
            cancelled = 0
            for order in orders:
                if await self.cancel_order(instrument, order.id):
                    cancelled += 1
            logger.info("Cancelled %d orders on Variational for %s", cancelled, instrument)
            return cancelled
        except Exception as exc:
            logger.error("Failed to cancel all orders on Variational: %s", _safe_str(exc))
            return 0
