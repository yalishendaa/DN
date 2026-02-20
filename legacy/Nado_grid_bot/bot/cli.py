"""CLI interface for Nado Grid Bot.

Usage:
    python -m bot.cli start       # Full start: init, reconcile, place grid, listen
    python -m bot.cli stop        # Graceful shutdown (sends SIGTERM to running bot)
    python -m bot.cli pause       # Pause order placement
    python -m bot.cli resume      # Resume order placement
    python -m bot.cli status      # Print current state
    python -m bot.cli dry-run     # Simulate without placing real orders
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from pathlib import Path

# Ensure project root is on PYTHONPATH
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)


def cmd_start(args: argparse.Namespace) -> None:
    from bot.config import load_config
    from bot.main import GridBot

    config = load_config(
        config_path=args.config,
        env_path=args.env,
    )
    bot = GridBot(config, dry_run=False)
    bot.start()


def cmd_dry_run(args: argparse.Namespace) -> None:
    from bot.config import load_config
    from bot.main import GridBot

    config = load_config(
        config_path=args.config,
        env_path=args.env,
    )
    bot = GridBot(config, dry_run=True)
    bot.start()


def cmd_stop(args: argparse.Namespace) -> None:
    """Send SIGTERM to running bot process via PID file."""
    pid_file = PROJECT_ROOT / "data" / "bot.pid"
    if not pid_file.exists():
        print("No PID file found. Is the bot running?")
        sys.exit(1)
    pid = int(pid_file.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to PID {pid}")
    except ProcessLookupError:
        print(f"Process {pid} not found. Removing stale PID file.")
        pid_file.unlink(missing_ok=True)


def cmd_pause(args: argparse.Namespace) -> None:
    from bot.config import load_config
    from bot.state_store import StateStore, BOT_STATE_PAUSED

    config = load_config(
        config_path=args.config,
        env_path=args.env,
    )
    db_path = PROJECT_ROOT / config.state_path
    store = StateStore(str(db_path))
    store.set_bot_state(BOT_STATE_PAUSED)
    store.close()
    print("Bot state set to PAUSED")


def cmd_resume(args: argparse.Namespace) -> None:
    from bot.config import load_config
    from bot.state_store import StateStore, BOT_STATE_RUNNING

    config = load_config(
        config_path=args.config,
        env_path=args.env,
    )
    db_path = PROJECT_ROOT / config.state_path
    store = StateStore(str(db_path))
    store.set_bot_state(BOT_STATE_RUNNING)
    store.close()
    print("Bot state set to RUNNING")


def cmd_status(args: argparse.Namespace) -> None:
    from bot.config import load_config
    from bot.main import GridBot
    from bot.logger import setup_logger
    import logging

    # Suppress verbose logs for status command
    setup_logger(level=logging.WARNING)

    config = load_config(
        config_path=args.config,
        env_path=args.env,
    )
    bot = GridBot(config, dry_run=True)
    info = bot.status()
    print(json.dumps(info, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="nado-grid",
        description="Nado BTC-PERP Long Grid Bot",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to YAML config (default: config.yaml)",
    )
    parser.add_argument(
        "--env",
        default=".env",
        help="Path to .env file (default: .env)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("start", help="Start the grid bot")
    sub.add_parser("stop", help="Stop a running bot (via PID)")
    sub.add_parser("pause", help="Pause order placement")
    sub.add_parser("resume", help="Resume order placement")
    sub.add_parser("status", help="Show current bot status")
    sub.add_parser("dry-run", help="Simulate grid without placing orders")

    args = parser.parse_args()

    dispatch = {
        "start": cmd_start,
        "stop": cmd_stop,
        "pause": cmd_pause,
        "resume": cmd_resume,
        "status": cmd_status,
        "dry-run": cmd_dry_run,
    }

    dispatch[args.command](args)


if __name__ == "__main__":
    main()
