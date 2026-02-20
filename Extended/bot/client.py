"""Инициализация торгового клиента для Extended Exchange."""

from x10.perpetual.accounts import StarkPerpetualAccount
from x10.perpetual.configuration import EndpointConfig
from x10.perpetual.trading_client import PerpetualTradingClient

from bot.config import ExtendedBotConfig


def create_trading_client(config: ExtendedBotConfig) -> PerpetualTradingClient:
    """
    Создать торговый клиент для Extended Exchange.

    Args:
        config: Конфигурация бота с API ключами и настройками

    Returns:
        PerpetualTradingClient: Инициализированный торговый клиент
    """
    stark_account = StarkPerpetualAccount(
        vault=config.vault_id,
        private_key=config.private_key,
        public_key=config.public_key,
        api_key=config.api_key,
    )

    trading_client = PerpetualTradingClient(
        endpoint_config=config.endpoint_config,
        stark_account=stark_account,
    )

    return trading_client
