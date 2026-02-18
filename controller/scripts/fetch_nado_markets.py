#!/usr/bin/env python3
"""Получить список ID рынков Nado и сохранить в markets.json.

Использует read-only API (get_all_product_symbols), приватный ключ не нужен.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_DN_ROOT = _SCRIPT_DIR.parent.parent
if str(_DN_ROOT) not in sys.path:
    sys.path.insert(0, str(_DN_ROOT))
_NADO_SDK_ROOT = _DN_ROOT / "Nado" / "nado-python-sdk"
if _NADO_SDK_ROOT.exists() and str(_NADO_SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(_NADO_SDK_ROOT))

from nado_protocol.client import NadoClientMode, create_nado_client


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Nado market IDs to markets.json")
    parser.add_argument(
        "--config",
        "-c",
        default="config.yaml",
        help="Path to config.yaml (for nado network)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="markets.json",
        help="Output file path (default: markets.json)",
    )
    parser.add_argument(
        "--network",
        choices=["mainnet", "testnet", "devnet"],
        default=None,
        help="Override network from config",
    )
    args = parser.parse_args()

    # Определяем сеть
    network = args.network
    if network is None:
        config_path = _DN_ROOT / args.config
        if config_path.exists():
            import yaml

            with open(config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            nado_cfg = cfg.get("nado", {})
            network = nado_cfg.get("network", "mainnet")
        else:
            network = "mainnet"

    mode_map = {
        "mainnet": NadoClientMode.MAINNET,
        "testnet": NadoClientMode.TESTNET,
        "devnet": NadoClientMode.DEVNET,
    }
    mode = mode_map.get(network, NadoClientMode.MAINNET)

    print(f"Connecting to Nado {network}...")
    client = create_nado_client(mode, signer=None)

    print("Fetching product symbols...")
    symbols = client.market.get_all_product_symbols()

    # ProductSymbolsData = list[ProductSymbol], ProductSymbol: product_id, symbol
    markets = [
        {"product_id": p.product_id, "symbol": p.symbol}
        for p in symbols
    ]
    markets.sort(key=lambda m: (m["product_id"], m["symbol"]))

    out_path = _DN_ROOT / args.output
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(markets, f, indent=2, ensure_ascii=False)

    print(f"Saved {len(markets)} markets to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
