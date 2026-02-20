"""Тесты критической логики DeltaEngine."""

from __future__ import annotations

import unittest

from controller.config import ControllerConfig, InstrumentConfig, RiskLimits
from controller.delta_engine import DeltaEngine
from controller.models import (
    DeltaSnapshot,
    ExchangeState,
    NormalizedBalance,
    NormalizedPosition,
    PositionDirection,
    Side,
)


def _state(
    exchange: str,
    instrument: str,
    pos_size: float,
    ref_price: float,
    available: float = 1_000.0,
) -> ExchangeState:
    direction = PositionDirection.FLAT
    if pos_size > 0:
        direction = PositionDirection.LONG
    if pos_size < 0:
        direction = PositionDirection.SHORT

    return ExchangeState(
        exchange=exchange,
        instrument=instrument,
        balance=NormalizedBalance(equity=available, available=available),
        position=NormalizedPosition(
            instrument=instrument,
            size=pos_size,
            direction=direction,
            mark_price=ref_price,
        ),
        reference_price=ref_price,
    )


class DeltaEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.instrument = "ETH-PERP"
        self.base_cfg = ControllerConfig(
            mode="auto",
            instruments=[
                InstrumentConfig(
                    symbol=self.instrument,
                    extended_market_name="ETH-USD",
                    nado_product_id=4,
                )
            ],
            risk=RiskLimits(
                max_delta_base=0.01,
                max_delta_usd=10.0,
                max_order_size_base=0.2,
                max_position_base=1.0,
                min_balance_usd=0.0,
            ),
            price_offset_pct=0.5,
        )

    def test_within_tolerance_has_no_actions(self) -> None:
        engine = DeltaEngine(self.base_cfg)
        snapshot = DeltaSnapshot(
            instrument=self.instrument,
            extended_state=_state("extended", self.instrument, 0.005, 2000.0),
            nado_state=_state("nado", self.instrument, -0.001, 2000.0),
        )

        decision = engine.analyze(snapshot)

        self.assertTrue(decision.within_tolerance)
        self.assertEqual(decision.actions, [])

    def test_positive_delta_places_sell_on_extended(self) -> None:
        engine = DeltaEngine(self.base_cfg)
        snapshot = DeltaSnapshot(
            instrument=self.instrument,
            extended_state=_state("extended", self.instrument, 0.8, 2000.0),
            nado_state=_state("nado", self.instrument, 0.1, 2000.0),
        )

        decision = engine.analyze(snapshot)

        self.assertFalse(decision.within_tolerance)
        self.assertEqual(len(decision.actions), 1)
        action = decision.actions[0]
        self.assertEqual(action.exchange, "extended")
        self.assertEqual(action.side, Side.SELL)
        self.assertAlmostEqual(action.amount, 0.2, places=8)
        self.assertAlmostEqual(action.price, 1990.0, places=8)

    def test_action_is_filtered_by_max_position_limit(self) -> None:
        engine = DeltaEngine(self.base_cfg)
        snapshot = DeltaSnapshot(
            instrument=self.instrument,
            extended_state=_state("extended", self.instrument, 0.99, 2000.0),
            nado_state=_state("nado", self.instrument, 0.01, 2000.0),
        )

        decision = engine.analyze(snapshot)

        self.assertFalse(decision.within_tolerance)
        self.assertEqual(decision.actions, [])

    def test_collects_safety_warnings(self) -> None:
        cfg = ControllerConfig(
            mode="monitor",
            instruments=self.base_cfg.instruments,
            risk=RiskLimits(
                max_delta_base=0.01,
                max_delta_usd=10.0,
                max_order_size_base=0.2,
                max_position_base=1.0,
                min_balance_usd=100.0,
            ),
            price_offset_pct=0.5,
        )
        engine = DeltaEngine(cfg)
        snapshot = DeltaSnapshot(
            instrument=self.instrument,
            extended_state=_state("extended", self.instrument, 0.0, 0.0, available=50.0),
            nado_state=_state("nado", self.instrument, 0.0, 2000.0, available=40.0),
        )

        decision = engine.analyze(snapshot)

        warnings_blob = " | ".join(decision.warnings)
        self.assertIn("Extended: ref price = 0", warnings_blob)
        self.assertIn("Extended: баланс", warnings_blob)
        self.assertIn("Nado: баланс", warnings_blob)


if __name__ == "__main__":
    unittest.main()
