

import MetaTrader5 as mt5
import time
import os
from datetime import datetime

# ============================================================
# 1. CREDENTIALS (set as environment variables, not hardcoded)
#    Windows (PowerShell):  $env:MT5_PASSWORD="yourpassword"
# ============================================================

ACCOUNT  = 101948656
PASSWORD = "Soham@987*#"
SERVER   = "XMGlobal-MT5 5"

if not PASSWORD:
    print("MT5_PASSWORD env var not set. Set it and re-run.")
    quit()

# ============================================================
# 2. SYMBOL / STRATEGY PARAMETERS
# ============================================================

SYMBOL = "GOLD.i#"   # confirm exact symbol in Market Watch

LOT = 0.01
MAGIC = 444444

STOP_DISTANCE  = 0.2      # breakout distance
SCALE_DISTANCE = 0.20     # ladder spacing
TRAIL_DISTANCE = 0.10     # global trailing SL
TRAIL_STEP     = 0.02     # minimum step to move SL
MAX_POSITIONS  = 2        # total positions incl first

MODIFY_DELAY = 0.15       # tight loop so pending orders track price closely
RECONNECT_DELAY = 5       # seconds to wait before retrying a dropped connection

# ============================================================
# 3. CONNECTION HELPERS
# ============================================================

def connect():
    """(Re)connect to MT5 and select the symbol. Returns True on success."""
    if not mt5.initialize():
        print("MT5 init failed:", mt5.last_error())
        return False

    if not mt5.login(ACCOUNT, PASSWORD, SERVER):
        print("Login failed:", mt5.last_error())
        mt5.shutdown()
        return False

    print("Connected to MT5")

    info = mt5.symbol_info(SYMBOL)
    if info is None:
        print("Symbol not found:", SYMBOL)
        mt5.shutdown()
        return False

    if not info.visible:
        mt5.symbol_select(SYMBOL, True)

    print("Warming up tick stream...")
    for _ in range(20):
        tick = mt5.symbol_info_tick(SYMBOL)
        if tick and tick.ask > 0 and tick.bid > 0:
            print("Tick stream ready")
            break
        time.sleep(0.1)

    return True


def connection_alive():
    """
    Reliable liveness check. symbol_info_tick can stay frozen when the
    market is closed even though the terminal is fine, so check the
    terminal/account state instead of relying on tick movement.
    """
    term = mt5.terminal_info()
    acct = mt5.account_info()
    return term is not None and acct is not None and term.connected

# ============================================================
# 4. TRADE HELPERS (all scoped to this EA's MAGIC number)
# ============================================================

def get_positions():
    positions = mt5.positions_get(symbol=SYMBOL) or []
    return [p for p in positions if p.magic == MAGIC]

def get_orders():
    orders = mt5.orders_get(symbol=SYMBOL) or []
    return [o for o in orders if o.magic == MAGIC]

def check_result(result, context):
    """Log a clear message if an order operation didn't actually succeed."""
    if result is None:
        print(f"{context}: order_send returned None — {mt5.last_error()}")
        return False
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"{context}: failed, retcode={result.retcode} comment={result.comment}")
        return False
    return True

def place_pending(order_type, price):
    result = mt5.order_send({
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": SYMBOL,
        "volume": LOT,
        "type": order_type,
        "price": price,
        "magic": MAGIC,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC
    })
    check_result(result, "place_pending")
    return result

def modify_order(ticket, price):
    result = mt5.order_send({
        "action": mt5.TRADE_ACTION_MODIFY,
        "order": ticket,
        "price": price
    })
    check_result(result, "modify_order")
    return result

def remove_all_orders():
    for o in get_orders():
        result = mt5.order_send({
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": o.ticket
        })
        check_result(result, f"remove_order({o.ticket})")

def close_all_positions():
    """Close every position belonging to this EA (used on shutdown / cleanup)."""
    for p in get_positions():
        tick = mt5.symbol_info_tick(SYMBOL)
        if tick is None:
            continue
        price = tick.bid if p.type == mt5.ORDER_TYPE_BUY else tick.ask
        close_type = mt5.ORDER_TYPE_SELL if p.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        result = mt5.order_send({
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": SYMBOL,
            "volume": p.volume,
            "type": close_type,
            "position": p.ticket,
            "price": price,
            "magic": MAGIC,
            "type_filling": mt5.ORDER_FILLING_IOC
        })
        check_result(result, f"close_position({p.ticket})")

def apply_global_sl(sl):
    for p in get_positions():
        result = mt5.order_send({
            "action": mt5.TRADE_ACTION_SLTP,
            "position": p.ticket,
            "sl": sl
        })
        check_result(result, f"apply_sl({p.ticket})")

# ============================================================
# 5. ONE TRADE CYCLE
#    Runs entry -> ladder -> trailing SL -> SL hit, then returns.
#    No artificial timeout: the cycle only ends when this EA's
#    positions are flat again, or the connection drops.
# ============================================================

def run_cycle():
    direction = None
    global_sl = None

    while True:

        if not connection_alive():
            return "conn_lost"

        try:
            tick = mt5.symbol_info_tick(SYMBOL)
            if tick is None:
                time.sleep(MODIFY_DELAY)
                continue
            ask, bid = tick.ask, tick.bid
            positions = get_positions()
            orders = get_orders()
        except Exception as e:
            print("Data fetch error:", e)
            return "conn_lost"

        print(f"[{datetime.now().time()}] Price={ask} Dir={direction} Pos={len(positions)} Ord={len(orders)}")

        # A. ENTRY ENGINE
        if not positions and direction is None:
            buy_price  = ask + STOP_DISTANCE
            sell_price = bid - STOP_DISTANCE

            if not orders:
                place_pending(mt5.ORDER_TYPE_BUY_STOP, buy_price)
                place_pending(mt5.ORDER_TYPE_SELL_STOP, sell_price)
            else:
                for o in orders:
                    if o.type == mt5.ORDER_TYPE_BUY_STOP:
                        modify_order(o.ticket, buy_price)
                    elif o.type == mt5.ORDER_TYPE_SELL_STOP:
                        modify_order(o.ticket, sell_price)

        # B. FIRST FILL -> FIX DIRECTION & PLACE LADDER
        elif positions and direction is None:
            remove_all_orders()

            first_pos = positions[0]
            direction = "BUY" if first_pos.type == 0 else "SELL"

            if direction == "BUY":
                global_sl = bid - TRAIL_DISTANCE
                base_price = first_pos.price_open
            else:
                global_sl = ask + TRAIL_DISTANCE
                base_price = first_pos.price_open

            apply_global_sl(global_sl)

            for i in range(1, MAX_POSITIONS):
                if direction == "BUY":
                    price = base_price + i * SCALE_DISTANCE
                    place_pending(mt5.ORDER_TYPE_BUY_STOP, price)
                else:
                    price = base_price - i * SCALE_DISTANCE
                    place_pending(mt5.ORDER_TYPE_SELL_STOP, price)

            print("Scale-in ladder placed")

        # C. RUNNING TRADE -> GLOBAL TRAILING SL ONLY
        elif positions and direction:
            if direction == "BUY":
                new_sl = bid - TRAIL_DISTANCE
                if new_sl >= global_sl + TRAIL_STEP:
                    global_sl = new_sl
                    apply_global_sl(global_sl)
            else:
                new_sl = ask + TRAIL_DISTANCE
                if new_sl <= global_sl - TRAIL_STEP:
                    global_sl = new_sl
                    apply_global_sl(global_sl)

        # D. SL HIT -> CYCLE COMPLETE (reuse the positions list from
        #    this iteration instead of calling get_positions() again)
        if direction and not positions:
            print("Global SL hit — cycle completed")
            remove_all_orders()
            return "done"

        time.sleep(MODIFY_DELAY)

# ============================================================
# 6. OUTER LOOP — RUNS FOREVER UNTIL Ctrl+C
# ============================================================

def main():
    if not connect():
        print("Initial connection failed. Exiting.")
        return

    cycle_count = 0

    try:
        while True:
            cycle_count += 1
            print(f"\n=== Starting cycle #{cycle_count} ===")

            result = run_cycle()
            print(f"Cycle #{cycle_count} ended with status: {result}")

            if result == "conn_lost":
                print("Connection lost. Attempting to reconnect...")
                mt5.shutdown()
                while not connect():
                    print(f"Retrying in {RECONNECT_DELAY}s...")
                    time.sleep(RECONNECT_DELAY)

            time.sleep(2)  # brief pause between cycles

    except KeyboardInterrupt:
        print("\nStopped by user (Ctrl+C).")
    finally:
        remove_all_orders()
        print("Shutting down MT5 connection.")
        mt5.shutdown()

if __name__ == "__main__":
    main()