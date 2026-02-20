"""Tests for live-trading safety gate helpers."""

from __future__ import annotations

import os
import unittest

from controller.safety import LiveTradingSafetyError, require_live_confirmation


class LiveTradingSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._prev = os.environ.get("CONFIRM_LIVE_TRADING")

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("CONFIRM_LIVE_TRADING", None)
        else:
            os.environ["CONFIRM_LIVE_TRADING"] = self._prev

    def test_dry_run_when_live_flag_is_false(self) -> None:
        os.environ.pop("CONFIRM_LIVE_TRADING", None)
        self.assertFalse(
            require_live_confirmation(live_flag=False, action_name="unit-test"),
        )

    def test_live_raises_without_env_confirmation(self) -> None:
        os.environ.pop("CONFIRM_LIVE_TRADING", None)
        with self.assertRaises(LiveTradingSafetyError):
            require_live_confirmation(live_flag=True, action_name="unit-test")

    def test_live_allows_with_env_confirmation(self) -> None:
        os.environ["CONFIRM_LIVE_TRADING"] = "1"
        self.assertTrue(
            require_live_confirmation(live_flag=True, action_name="unit-test"),
        )


if __name__ == "__main__":
    unittest.main()
