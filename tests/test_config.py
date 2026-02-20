"""Тесты загрузки и валидации controller/config.py."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml
from controller.config import ConfigValidationError, load_config


def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


class ControllerConfigTests(unittest.TestCase):
    def _build_base_files(self, tmp: Path) -> tuple[Path, Path]:
        ext_env = tmp / "extended.env"
        nado_env = tmp / "nado.env"
        ext_env.write_text("X10_API_KEY=demo\n", encoding="utf-8")
        nado_env.write_text("NADO_PRIVATE_KEY=0xdemo\n", encoding="utf-8")
        return ext_env, nado_env

    def _base_config(self, ext_env: Path, nado_env: Path) -> dict:
        return {
            "entry": {
                "primary_exchange": "extended",
                "secondary_exchange": "nado",
            },
            "mode": "monitor",
            "cycle_interval_sec": 5,
            "max_retries": 2,
            "backoff_base_sec": 1,
            "extended": {"env_file": str(ext_env), "network": "mainnet"},
            "nado": {
                "env_file": str(nado_env),
                "network": "mainnet",
                "subaccount_name": "default",
            },
            "instruments": [
                {
                    "symbol": "ETH-PERP",
                    "extended_market_name": "ETH-USD",
                    "nado_product_id": 4,
                }
            ],
            "risk": {
                "max_delta_base": 0.01,
                "max_delta_usd": 1000,
                "max_order_size_base": 0.1,
                "max_position_base": 1.0,
                "min_balance_usd": 50,
            },
            "order_post_only": True,
            "price_offset_pct": 0.1,
        }

    def test_load_config_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            ext_env, nado_env = self._build_base_files(tmp)
            config_file = tmp / "config.yaml"
            _write_yaml(config_file, self._base_config(ext_env, nado_env))

            cfg = load_config(str(config_file))

            self.assertEqual(cfg.mode, "monitor")
            self.assertEqual(len(cfg.instruments), 1)
            self.assertEqual(cfg.instruments[0].symbol, "ETH-PERP")
            self.assertEqual(cfg.extended_env_file, str(ext_env))
            self.assertEqual(cfg.nado_env_file, str(nado_env))
            self.assertEqual(cfg.entry_primary_exchange, "extended")
            self.assertEqual(cfg.entry_secondary_exchange, "nado")

    def test_invalid_mode_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            ext_env, nado_env = self._build_base_files(tmp)
            payload = self._base_config(ext_env, nado_env)
            payload["mode"] = "invalid_mode"

            config_file = tmp / "config.yaml"
            _write_yaml(config_file, payload)

            with self.assertRaises(ConfigValidationError):
                load_config(str(config_file))

    def test_empty_instruments_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            ext_env, nado_env = self._build_base_files(tmp)
            payload = self._base_config(ext_env, nado_env)
            payload["instruments"] = []

            config_file = tmp / "config.yaml"
            _write_yaml(config_file, payload)

            with self.assertRaises(ConfigValidationError):
                load_config(str(config_file))

    def test_missing_env_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            ext_env = tmp / "missing-extended.env"
            nado_env = tmp / "missing-nado.env"
            payload = self._base_config(ext_env, nado_env)

            config_file = tmp / "config.yaml"
            _write_yaml(config_file, payload)

            with self.assertRaises(FileNotFoundError):
                load_config(str(config_file))

    def test_variational_pair_allows_missing_nado_env_and_product_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            ext_env, nado_env = self._build_base_files(tmp)
            payload = self._base_config(ext_env, nado_env)
            payload["entry"] = {
                "primary_exchange": "variational",
                "secondary_exchange": "extended",
            }
            payload["nado"]["env_file"] = str(tmp / "missing-nado.env")
            payload["instruments"] = [
                {
                    "symbol": "ETH-PERP",
                    "extended_market_name": "ETH-USD",
                    "variational_underlying": "ETH",
                }
            ]

            config_file = tmp / "config.yaml"
            _write_yaml(config_file, payload)

            cfg = load_config(str(config_file))
            self.assertEqual(cfg.entry_primary_exchange, "variational")
            self.assertEqual(cfg.entry_secondary_exchange, "extended")
            self.assertEqual(cfg.instruments[0].variational_underlying, "ETH")

    def test_variational_pair_requires_underlying(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            ext_env, nado_env = self._build_base_files(tmp)
            payload = self._base_config(ext_env, nado_env)
            payload["entry"] = {
                "primary_exchange": "variational",
                "secondary_exchange": "extended",
            }
            payload["instruments"] = [
                {
                    "symbol": "ETH-PERP",
                    "extended_market_name": "ETH-USD",
                }
            ]

            config_file = tmp / "config.yaml"
            _write_yaml(config_file, payload)

            with self.assertRaises(ConfigValidationError):
                load_config(str(config_file))


if __name__ == "__main__":
    unittest.main()
