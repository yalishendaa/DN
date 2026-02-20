#!/usr/bin/env python3
"""Диагностика: разместить ордер и проверить его статус через разные API."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "nado-python-sdk"))
sys.path.insert(0, str(Path(__file__).parent))

from bot.config import load_config
from bot.exchange_client import ExchangeClient, NadoApiError
from bot.logger import setup_logger
import logging

setup_logger(level=logging.INFO)

def main():
    config = load_config()
    ec = ExchangeClient(config)
    
    print("=" * 80)
    print("ORDER PLACEMENT DIAGNOSTICS")
    print("=" * 80)
    print(f"Owner: {ec.owner}")
    print(f"Sender (subaccount hex): {ec.sender_hex}")
    print(f"Subaccount name: {config.subaccount_name}")
    print()
    
    # Получить mark price и book info
    mark_price = ec.get_mark_price(config.product_id)
    book_info = ec.get_book_info(config.product_id)
    
    # Цена на 2% ниже mark price
    test_price = int(mark_price * 0.98)
    tick = book_info.price_increment_x18
    test_price = (test_price // tick) * tick
    
    # Размер 0.0015 BTC
    test_amount_btc = 0.0015
    test_amount = int(test_amount_btc * 10**18)
    size_inc = book_info.size_increment
    test_amount = (test_amount // size_inc) * size_inc
    
    print(f"Test order parameters:")
    print(f"  Product ID: {config.product_id} (BTC-PERP)")
    print(f"  Price: {test_price / 10**18:.2f} USD")
    print(f"  Amount: {test_amount / 10**18:.8f} BTC")
    print(f"  Total: {(test_price * test_amount) / 10**36:.2f} USDT")
    print()
    
    response = input("Place order? (yes/no): ")
    if response.lower() != "yes":
        print("Cancelled.")
        return
    
    print("\n" + "=" * 80)
    print("STEP 1: Placing order...")
    print("=" * 80)
    
    try:
        digest, resp = ec.place_post_only_order(
            product_id=config.product_id,
            price_x18=test_price,
            amount=test_amount,
            reduce_only=False,
        )
        
        print(f"✅ Order placement response received!")
        print(f"   Digest: {digest}")
        
        # Детали ответа
        if hasattr(resp, 'dict'):
            resp_dict = resp.dict()
            print(f"   Status: {resp_dict.get('status', 'unknown')}")
            print(f"   Signature: {resp_dict.get('signature', 'N/A')[:20]}...")
            if 'data' in resp_dict and resp_dict['data']:
                print(f"   Data: {resp_dict['data']}")
        elif hasattr(resp, 'status'):
            print(f"   Status: {resp.status}")
        
        print()
        
        # Подождать немного для обработки
        print("Waiting 3 seconds for order to be processed...")
        time.sleep(3)
        
        print("\n" + "=" * 80)
        print("STEP 2: Checking open orders...")
        print("=" * 80)
        
        open_orders = ec.get_open_orders(config.product_id)
        print(f"Total open orders: {len(open_orders)}")
        
        found_in_open = None
        for o in open_orders:
            if o.digest == digest:
                found_in_open = o
                break
        
        if found_in_open:
            print(f"✅ Order FOUND in open orders!")
            print(f"   Price: {int(found_in_open.price_x18) / 10**18:.2f} USD")
            print(f"   Amount: {abs(int(found_in_open.amount)) / 10**18:.8f} BTC")
            print(f"   Remaining: {abs(int(found_in_open.unfilled_amount)) / 10**18:.8f} BTC")
            print(f"   Placed at: {found_in_open.placed_at}")
        else:
            print(f"⚠️  Order NOT found in open orders")
            print(f"   This could mean:")
            print(f"     - Order was immediately filled")
            print(f"     - Order was rejected but error not caught")
            print(f"     - Order is on a different subaccount")
            if open_orders:
                print(f"\n   Other open orders on this subaccount:")
                for o in open_orders[:3]:
                    print(f"     - {o.digest[:20]}... | price={int(o.price_x18)/10**18:.2f} | remaining={abs(int(o.unfilled_amount))/10**18:.8f}")
        
        print("\n" + "=" * 80)
        print("STEP 3: Checking historical orders...")
        print("=" * 80)
        
        try:
            historical = ec.client.market.get_subaccount_historical_orders({
                "subaccount": ec.sender_hex,
                "product_ids": [config.product_id],
                "limit": 10,
            })
            
            orders = historical.orders if hasattr(historical, 'orders') else []
            print(f"Total historical orders (last 10): {len(orders)}")
            
            found_in_history = None
            for o in orders:
                if hasattr(o, 'digest') and o.digest == digest:
                    found_in_history = o
                    break
            
            if found_in_history:
                print(f"✅ Order FOUND in historical orders!")
                if hasattr(found_in_history, 'placed_at'):
                    print(f"   Placed at: {found_in_history.placed_at}")
            else:
                print(f"⚠️  Order NOT found in historical orders (yet)")
                print(f"   Historical indexing may have delay")
                if orders:
                    print(f"\n   Recent orders in history:")
                    for o in orders[:3]:
                        d = o.digest if hasattr(o, 'digest') else 'N/A'
                        print(f"     - {d[:20]}...")
        except Exception as e:
            print(f"⚠️  Error querying history: {e}")
        
        print("\n" + "=" * 80)
        print("STEP 4: Summary")
        print("=" * 80)
        
        if found_in_open:
            print("✅ Order is ACTIVE and should be visible in UI")
            print(f"   Check UI with:")
            print(f"     - Subaccount: {ec.sender_hex}")
            print(f"     - Product: BTC-PERP (ID: 2)")
            print(f"     - Status filter: Active/Open")
        elif found_in_history:
            print("⚠️  Order was placed but is NOT active (filled/cancelled)")
        else:
            print("❌ Order not found anywhere - possible issues:")
            print("   1. Order placement failed silently")
            print("   2. Order is on different subaccount")
            print("   3. API delay in indexing")
            print(f"\n   Try checking UI manually:")
            print(f"     - Subaccount filter: {ec.sender_hex}")
            print(f"     - Product: BTC-PERP")
            print(f"     - Look for order with digest: {digest[:20]}...")
        
        print("\n" + "=" * 80)
        
    except NadoApiError as e:
        print(f"❌ API Error: [{e.code}] {e.message}")
        if e.raw:
            print(f"   Raw response: {e.raw}")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
