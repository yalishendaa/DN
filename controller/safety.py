"""Safety helpers for gating live-trading actions."""

from __future__ import annotations

import os


class LiveTradingSafetyError(RuntimeError):
    """Raised when a live-trading action is requested without explicit confirmation."""


def require_live_confirmation(*, live_flag: bool, action_name: str) -> bool:
    """Return True only when live mode is explicitly confirmed.

    Live actions are permitted only when:
    1) CLI flag `--live` is passed (live_flag=True)
    2) env var `CONFIRM_LIVE_TRADING=1` is present
    """
    if not live_flag:
        return False

    if os.environ.get("CONFIRM_LIVE_TRADING", "").strip() == "1":
        return True

    raise LiveTradingSafetyError(
        "LIVE TRADING BLOCKED for "
        f"`{action_name}`: set CONFIRM_LIVE_TRADING=1 and rerun with --live"
    )
