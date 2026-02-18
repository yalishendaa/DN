"""Точка входа: python -m controller [--config path/to/config.yaml]"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys

from controller.config import load_config
from controller.controller import DeltaNeutralController
from controller.logger import setup_logging
from controller.safety import LiveTradingSafetyError, require_live_confirmation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delta-Neutral Controller — двухногая работа между выбранными биржами",
    )
    parser.add_argument(
        "--config",
        "-c",
        default="config.yaml",
        help="Путь к конфигу YAML (по умолчанию config.yaml в корне DN/)",
    )
    parser.add_argument(
        "--mode",
        "-m",
        choices=["monitor", "auto"],
        default=None,
        help="Переопределить режим из конфига",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Переопределить уровень логирования",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Разрешить живую торговлю (только вместе с CONFIRM_LIVE_TRADING=1). "
            "Без флага контроллер работает в безопасном режиме."
        ),
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    # Загрузка конфига
    try:
        config = load_config(args.config)
    except Exception as e:
        print(f"Ошибка загрузки конфига: {e}", file=sys.stderr)
        sys.exit(1)

    # Переопределение из CLI
    if args.mode:
        config.mode = args.mode
    if args.log_level:
        config.log_level = args.log_level

    if config.mode == "auto":
        try:
            require_live_confirmation(
                live_flag=args.live,
                action_name="controller auto mode",
            )
        except LiveTradingSafetyError as e:
            print(f"!!! {e}", file=sys.stderr)
            sys.exit(2)

    # Настройка логирования
    setup_logging(level=config.log_level, log_file=config.log_file)

    # Создание и запуск контроллера
    controller = DeltaNeutralController(config)

    # Обработка сигналов остановки
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, controller.stop)

    try:
        await controller.initialize()
        await controller.run()
    except KeyboardInterrupt:
        controller.stop()
    except Exception as e:
        print(f"Критическая ошибка: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        await controller.close()


if __name__ == "__main__":
    asyncio.run(main())
