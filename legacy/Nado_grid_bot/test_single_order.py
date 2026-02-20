#!/usr/bin/env python3
"""Безопасный тест: разместить один POST_ONLY ордер и сразу отменить.

ВАЖНО: Использует реальный ключ и размещает реальный ордер на mainnet!
Ордер будет отменён автоматически, но убедитесь, что у вас есть баланс.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "nado-python-sdk"))
sys.path.insert(0, str(Path(__file__).parent))

from bot.config import load_config
from bot.exchange_client import (
    ExchangeClient, NadoApiError, ERR_POST_ONLY_CROSSES, ERR_AMOUNT_TOO_SMALL
)
from bot.logger import setup_logger
import logging

setup_logger(level=logging.INFO)

def main():
    config = load_config()
    ec = ExchangeClient(config)
    
    # Получить текущий mark price
    mark_price = ec.get_mark_price(config.product_id)
    book_info = ec.get_book_info(config.product_id)
    
    print(f"Mark price: {mark_price / 10**18:.2f} USD")
    print(f"Price increment: {book_info.price_increment_x18 / 10**18:.2f} USD")
    print()
    
    # Выбрать безопасную цену: на 2% ниже mark price (далеко от стакана)
    test_price = int(mark_price * 0.98)
    # Округлить вниз к tick
    tick = book_info.price_increment_x18
    test_price = (test_price // tick) * tick
    
    # Фиксированный размер: 0.0015 BTC (минимум для ~100 USDT при цене ~70k)
    test_amount_btc = 0.0015
    test_amount = int(test_amount_btc * 10**18)  # Convert to x18
    
    # Округлить к size_increment
    size_inc = book_info.size_increment
    test_amount = (test_amount // size_inc) * size_inc
    
    # Проверить минимальную стоимость (min_size в quote/USDT)
    order_value = (test_price * test_amount) // 10**18
    min_value_usdt = book_info.min_size / 10**18
    
    if order_value < book_info.min_size:
        print(f"⚠️  Warning: Order value ({order_value / 10**18:.2f} USDT) < min_size ({min_value_usdt:.2f} USDT)")
        print(f"   Using 0.0015 BTC anyway (may fail with error 2003)")
        print()
    
    print(f"Placing test BUY order:")
    print(f"  Price: {test_price / 10**18:.2f} USD")
    print(f"  Amount: {test_amount / 10**18:.8f} BTC")
    print(f"  Total: {order_value / 10**18:.2f} USDT")
    print()
    
    response = input("Continue? (yes/no): ")
    if response.lower() != "yes":
        print("Cancelled.")
        return
    
    try:
        # Разместить POST_ONLY ордер
        digest, resp = ec.place_post_only_order(
            product_id=config.product_id,
            price_x18=test_price,
            amount=test_amount,  # positive = buy
            reduce_only=False,
        )
        
        print(f"✅ Order placed!")
        print(f"   Digest: {digest}")
        
        # Показать полный ответ
        if hasattr(resp, 'dict'):
            resp_dict = resp.dict()
            print(f"   Response status: {resp_dict.get('status', 'unknown')}")
            if 'data' in resp_dict:
                print(f"   Response data: {resp_dict['data']}")
        elif hasattr(resp, 'status'):
            print(f"   Response status: {resp.status}")
        
        # Проверить, что ордер действительно в открытых
        print("\nVerifying order is in open orders...")
        open_orders = ec.get_open_orders(config.product_id)
        found = any(o.digest == digest for o in open_orders)
        if found:
            print(f"✅ Order found in open orders!")
        else:
            print(f"⚠️  Order NOT found in open orders (may have been filled immediately)")
            print(f"   Total open orders: {len(open_orders)}")
        
        print()
        
        # Подождать 2 секунды
        print("Waiting 2 seconds...")
        time.sleep(2)
        
        # Отменить ордер
        print("Cancelling order...")
        cancel_resp = ec.cancel_order(config.product_id, digest)
        print("✅ Cancel request sent!")
        
        # Проверить, что ордер отменен
        print("\nVerifying order is cancelled...")
        time.sleep(1)  # Дать время на обработку отмены
        open_orders_after = ec.get_open_orders(config.product_id)
        still_open = any(o.digest == digest for o in open_orders_after)
        if not still_open:
            print(f"✅ Order confirmed cancelled (not in open orders)")
        else:
            print(f"⚠️  Order still in open orders (cancel may have failed)")
        
    except NadoApiError as e:
        if e.code == ERR_POST_ONLY_CROSSES:
            print(f"⚠️  Order would cross book (code {e.code})")
            print("   This is expected if price is too close to market.")
            print("   Try again with a lower price (e.g., mark_price * 0.95)")
        elif e.code == ERR_AMOUNT_TOO_SMALL:
            print(f"❌ Order amount too small (code {e.code})")
            print(f"   Min size required: {book_info.min_size / 10**18:.2f} USDT")
            print(f"   Your order value: {order_value / 10**18:.2f} USDT")
            print(f"   Try increasing amount or price")
        else:
            print(f"❌ Error: [{e.code}] {e.message}")
            raise
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        raise

if __name__ == "__main__":
    main()
