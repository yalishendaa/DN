"""Конфигурация для торгового бота Extended Exchange."""

import os
from dataclasses import dataclass
from typing import Literal

from dotenv import load_dotenv

from x10.perpetual.configuration import EndpointConfig, MAINNET_CONFIG, TESTNET_CONFIG


@dataclass
class ExtendedBotConfig:
    """Конфигурация для торгового бота."""

    api_key: str
    public_key: str
    private_key: str
    vault_id: int
    environment: Literal["testnet", "mainnet"] = "testnet"
    builder_id: int | None = None

    @property
    def endpoint_config(self) -> EndpointConfig:
        """Получить конфигурацию эндпоинта в зависимости от окружения."""
        if self.environment == "mainnet":
            return MAINNET_CONFIG
        return TESTNET_CONFIG

    @classmethod
    def from_env(cls, env_file: str | None = None) -> "ExtendedBotConfig":
        """
        Загрузить конфигурацию из переменных окружения.

        Args:
            env_file: Путь к файлу .env (опционально)

        Returns:
            ExtendedBotConfig: Загруженная конфигурация

        Raises:
            ValueError: Если обязательные переменные окружения не установлены
        """
        if env_file:
            load_dotenv(env_file)
        else:
            load_dotenv()

        api_key = os.getenv("X10_API_KEY")
        public_key = os.getenv("X10_PUBLIC_KEY")
        private_key = os.getenv("X10_PRIVATE_KEY")
        vault_id = os.getenv("X10_VAULT_ID")
        builder_id = os.getenv("X10_BUILDER_ID")
        environment = os.getenv("X10_ENVIRONMENT", "testnet").lower()

        if not api_key:
            raise ValueError("X10_API_KEY is not set in environment variables")
        if not public_key:
            raise ValueError("X10_PUBLIC_KEY is not set in environment variables")
        if not private_key:
            raise ValueError("X10_PRIVATE_KEY is not set in environment variables")
        if not vault_id:
            raise ValueError("X10_VAULT_ID is not set in environment variables")

        if not public_key.startswith("0x"):
            raise ValueError("X10_PUBLIC_KEY must be a hex string starting with 0x")
        if not private_key.startswith("0x"):
            raise ValueError("X10_PRIVATE_KEY must be a hex string starting with 0x")

        if environment not in ("testnet", "mainnet"):
            raise ValueError(f"X10_ENVIRONMENT must be 'testnet' or 'mainnet', got '{environment}'")

        return cls(
            api_key=api_key,
            public_key=public_key,
            private_key=private_key,
            vault_id=int(vault_id),
            environment=environment,  # type: ignore
            builder_id=int(builder_id) if builder_id else None,
        )
