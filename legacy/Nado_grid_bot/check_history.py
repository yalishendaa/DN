#!/usr/bin/env python3
"""Проверка истории ордеров через API."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "nado-python-sdk"))
sys.path.insert(0, str(Path(__file__).parent))

from bot.config import load_config
from bot.exchange_client import ExchangeClient
from bot.logger import setup_logger
import logging
from datetime import datetime

setup_logger(level=logging.WARNING)  # Suppress INFO logs

def main():
    config = load_config()
    ec = ExchangeClient(config)
    
    print(f"Owner: {ec.owner}")
    print(f"Sender (subaccount): {ec.sender_hex}")
    print(f"Subaccount name: {config.subaccount_name}")
    print()
    
    # Получить исторические ордера
    print("Fetching historical orders for BTC-PERP (product_id=2)...")
    
    try:
        historical = ec.client.market.get_subaccount_historical_orders({
            "subaccount": ec.sender_hex,
            "product_ids": [config.product_id],
            "limit": 20,  # Последние 20 ордеров
        })
        
        orders = historical.orders if hasattr(historical, 'orders') else []
        
        print(f"\nTotal historical orders: {len(orders)}")
        print()
        
        if len(orders) == 0:
            print("⚠️  No historical orders found!")
            print("\nThis could mean:")
            print("  1. No orders were ever placed from this subaccount")
            print("  2. Orders are on a different subaccount")
            print("  3. API query failed")
            return
        
        print("Recent orders (last 20):")
        print("=" * 120)
        
        for i, o in enumerate(orders, 1):
            # Парсим данные ордера
            side = "BUY" if int(o.amount) > 0 else "SELL"
            price_usd = int(o.price_x18) / 10**18 if hasattr(o, 'price_x18') else 0
            amount_btc = abs(int(o.amount)) / 10**18 if hasattr(o, 'amount') else 0
            
            # Статус
            status = "UNKNOWN"
            if hasattr(o, 'status'):
                status = o.status
            elif hasattr(o, 'unfilled_amount'):
                remaining = abs(int(o.unfilled_amount)) / 10**18 if hasattr(o, 'unfilled_amount') else 0
                if remaining == 0:
                    status = "FILLED"
                else:
                    status = "PARTIAL"
            
            # Время
            timestamp = ""
            if hasattr(o, 'placed_at'):
                try:
                    ts = int(o.placed_at)
                    timestamp = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                except:
                    timestamp = str(o.placed_at)
            
            print(f"\nOrder #{i}:")
            print(f"  Digest: {o.digest if hasattr(o, 'digest') else 'N/A'}")
            print(f"  Side: {side}")
            print(f"  Price: ${price_usd:,.2f} USD" if price_usd > 0 else "  Price: N/A")
            print(f"  Amount: {amount_btc:.8f} BTC" if amount_btc > 0 else "  Amount: N/A")
            print(f"  Status: {status}")
            print(f"  Time: {timestamp}")
            
            # Дополнительные поля если есть
            if hasattr(o, 'unfilled_amount'):
                remaining = abs(int(o.unfilled_amount)) / 10**18
                filled = amount_btc - remaining
                print(f"  Filled: {filled:.8f} BTC")
                print(f"  Remaining: {remaining:.8f} BTC")
        
        print("\n" + "=" * 120)
        print(f"\n✅ Found {len(orders)} historical order(s)")
        
        # Проверим самый последний
        if orders:
            latest = orders[0]
            print(f"\nLatest order digest: {latest.digest if hasattr(latest, 'digest') else 'N/A'}")
            print(f"Latest order time: {timestamp if timestamp else 'N/A'}")
        
    except Exception as e:
        print(f"❌ Error fetching historical orders: {e}")
        import traceback
        traceback.print_exc()
        
        # Попробуем альтернативный способ
        print("\nTrying alternative query...")
        try:
            # Через indexer
            historical = ec.client.market.get_subaccount_historical_orders({
                "subaccounts": [ec.sender_hex],
                "product_ids": [config.product_id],
                "limit": 10,
            })
            print(f"Alternative query returned: {type(historical)}")
            if hasattr(historical, 'orders'):
                print(f"Orders count: {len(historical.orders)}")
        except Exception as e2:
            print(f"Alternative query also failed: {e2}")

if __name__ == "__main__":
    main()
