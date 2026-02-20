#!/usr/bin/env python3
"""Быстрая проверка подключения к Nado mainnet."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "nado-python-sdk"))
sys.path.insert(0, str(Path(__file__).parent))

from bot.config import load_config
from bot.exchange_client import ExchangeClient
from bot.logger import setup_logger
import logging

setup_logger(level=logging.INFO)

def main():
    config = load_config()
    print(f"Network: {config.network}")
    print(f"Product: {config.symbol} (ID: {config.product_id})")
    print(f"Subaccount: {config.subaccount_name}")
    print()
    
    ec = ExchangeClient(config)
    print(f"Owner address: {ec.owner}")
    print(f"Sender hex: {ec.sender_hex}")
    print()
    
    # Read-only запросы
    print("Fetching mark price...")
    mark_price = ec.get_mark_price(config.product_id)
    print(f"Mark price (x18): {mark_price}")
    print(f"Mark price (USD): {mark_price / 10**18:.2f}")
    print()
    
    print("Fetching book info...")
    book_info = ec.get_book_info(config.product_id)
    print(f"Price increment (x18): {book_info.price_increment_x18}")
    print(f"Size increment: {book_info.size_increment}")
    print(f"Min size: {book_info.min_size}")
    print()
    
    print("Fetching position...")
    position = ec.get_perp_position_amount(config.product_id)
    print(f"Position amount (x18): {position}")
    print(f"Position (BTC): {position / 10**18:.8f}")
    print()
    
    print("Fetching open orders...")
    orders = ec.get_open_orders(config.product_id)
    print(f"Open orders: {len(orders)}")
    for o in orders[:5]:
        print(f"  - {o.digest[:16]}... | price={int(o.price_x18)/10**18:.2f} | amount={int(o.amount)/10**18:.8f} | remaining={int(o.unfilled_amount)/10**18:.8f}")
    
    print()
    print("✅ Connection test successful!")

if __name__ == "__main__":
    main()
