import os
import time
import csv
from datetime import datetime, timezone
from delta_rest_client import DeltaRestClient


# ===================================================================
# CONNECTION
# ===================================================================

def connect_to_delta(api_key: str, api_secret: str, base_url: str) -> DeltaRestClient:
    """Create an authenticated Delta REST client."""
    client = DeltaRestClient(base_url=base_url, api_key=api_key, api_secret=api_secret)
    print(f"Connected to Delta at {base_url}")
    return client


# ===================================================================
# OPTION CHAIN & STRIKE PICKING
# ===================================================================

def get_option_chain(client, underlying: str, expiry_date: str) -> list:
    """
    Fetches option chain (calls + puts) for the given underlying and expiry.
    expiry_date must be in DD-MM-YYYY format.
    """
    return client.option_chain(underlying_asset_symbol=underlying, expiry_date=expiry_date)


def find_strike_by_delta(chain: list, target_abs_delta: float, option_type: str,
                         tie_breaker: str = 'otm', exclude_product_id: int = None) -> dict:
    """
    Pick the strike whose |delta| is closest to target_abs_delta.

    Safety rules built in:
      - skips strikes with missing/None greeks (stale or illiquid quotes)
      - skips exclude_product_id, so we never re-select the strike we just closed
      - tie_breaker resolves equidistant candidates:
            'otm' -> smaller |delta|  (safer: wider breakeven, less gamma)
            'itm' -> larger |delta|   (richer: more premium, more gamma)
    """
    wanted_contract = 'call_options' if option_type == 'call' else 'put_options'
    candidates = []
    for opt in chain:
        if opt.get('contract_type') != wanted_contract:
            continue
        if exclude_product_id is not None and opt.get('product_id') == exclude_product_id:
            continue
        greeks = opt.get('greeks') or {}
        if greeks.get('delta') is None or opt.get('mark_price') is None:
            continue
        opt_abs_delta = abs(float(greeks['delta']))
        distance      = abs(opt_abs_delta - target_abs_delta)
        candidates.append((distance, opt_abs_delta, opt))

    if not candidates:
        raise RuntimeError(f"No usable {option_type} strikes near delta {target_abs_delta}")

    if tie_breaker == 'itm':
        candidates.sort(key=lambda x: (x[0], -x[1]))
    else:
        candidates.sort(key=lambda x: (x[0], x[1]))

    return candidates[0][2]


def fetch_leg_state(client, symbol: str):
    """
    Current delta and mark price for one option symbol.
    Returns None if greeks/mark are missing so the caller can skip the tick.
    """
    t = client.get_ticker(symbol)
    greeks = t.get('greeks') or {}
    if greeks.get('delta') is None or t.get('mark_price') is None:
        return None
    return {
        'delta': float(greeks['delta']),
        'mark_price': float(t['mark_price'])
    }


# ===================================================================
# ORDER EXECUTION (place + verify + slippage protection)
# ===================================================================

ORDER_SEQ        = 0     # makes client_order_id unique even within the same millisecond
TICK_SIZE_CACHE  = {}    # product_id -> tick_size (fetched once per product)


def next_client_order_id() -> str:
    """Unique, monotonic order id. Max 32 chars on Delta."""
    global ORDER_SEQ
    ORDER_SEQ += 1
    return f"dns{int(time.time())}x{ORDER_SEQ:04d}"


def get_tick_size(client, product_id: int) -> float:
    """Tick size for a product, cached. Limit prices must be a multiple of this."""
    if product_id not in TICK_SIZE_CACHE:
        product = client.get_product(product_id)
        TICK_SIZE_CACHE[product_id] = float(product['tick_size'])
    return TICK_SIZE_CACHE[product_id]


def protected_limit_price(mark_price: float, side: str, slippage_pct: float, tick_size: float) -> float:
    """
    Worst price we are willing to accept, anchored to mark:
      buy  (closing a short): pay at most  mark * (1 + slippage_pct)
      sell (opening a short): receive at least mark * (1 - slippage_pct)
    Rounded to the product's tick size.
    """
    raw = mark_price * (1 + slippage_pct) if side == 'buy' else mark_price * (1 - slippage_pct)
    price = round(round(raw / tick_size) * tick_size, 10)
    return max(price, tick_size)


def execute_order(client, product_id: int, size: int, side: str,
                  reduce_only: bool = False, limit_price: float = None) -> tuple:
    """
    Place an IOC order, then VERIFY the fill from the exchange response.
    Returns (filled_size, avg_fill_price).

    - if limit_price is given, sends a limit IOC: fills only at limit_price
      or better, cancels the rest. This is the slippage protection — a plain
      market order into a thin option book can fill far from mark.
    - raises if nothing fills, so the caller never assumes a phantom position
    - warns and returns the real size on a partial fill
    - avg_fill_price is the ACTUAL execution price, not mark
    - unique client_order_id makes a network-level retry idempotent
    """
    payload = {
        'product_id': product_id,
        'size': size,
        'side': side,
        'time_in_force': 'ioc',
        'reduce_only': 'true' if reduce_only else 'false',
        'client_order_id': next_client_order_id(),
    }
    if limit_price is not None:
        payload['order_type']  = 'limit_order'
        payload['limit_price'] = str(limit_price)
    else:
        payload['order_type'] = 'market_order'

    resp = client.create_order(payload)

    unfilled = int(float(resp.get('unfilled_size') or 0))
    filled   = size - unfilled
    avg_raw  = resp.get('average_fill_price')
    avg_fill = float(avg_raw) if avg_raw is not None else None

    if filled <= 0:
        raise RuntimeError(
            f"Order NOT filled: product={product_id} side={side} "
            f"limit={limit_price} state={resp.get('state')}"
        )
    if filled < size:
        print(f"  WARNING: partial fill {filled}/{size} on product {product_id}")

    return filled, avg_fill


def get_position_size(client, product_id: int) -> int:
    """Actual position size on the exchange. 0 = flat, negative = short."""
    pos = client.get_position(product_id)
    if not pos:
        return 0
    return int(float(pos.get('size') or 0))


def close_leg_fully(client, leg: dict, slippage_pct: float) -> float:
    """
    Close one tracked leg COMPLETELY, verified against the exchange.
    Pass 1: slippage-protected limit IOC anchored to mark.
    Pass 2 (only if a remainder is left): plain market IOC to guarantee flat.
    Raises if the leg is still not flat after both passes.
    Returns the realized PnL from the close (entry - fill, per contract).
    """
    realized = 0.0

    on_exchange = abs(get_position_size(client, leg['product_id']))
    if on_exchange == 0:
        print(f"  {leg['symbol']} already flat on exchange (reconciled), skipping close.")
        return 0.0

    # Pass 1: protected close
    state = fetch_leg_state(client, leg['symbol'])
    limit = None
    if state is not None:
        tick  = get_tick_size(client, leg['product_id'])
        limit = protected_limit_price(state['mark_price'], 'buy', slippage_pct, tick)
    try:
        filled, fill_price = execute_order(client, leg['product_id'], on_exchange,
                                           'buy', reduce_only=True, limit_price=limit)
        if fill_price is None:
            fill_price = state['mark_price'] if state else leg['entry_price']
        realized += (leg['entry_price'] - fill_price) * filled
        print(f"  closed {leg['symbol']} x{filled} @ {fill_price:.2f}")
    except RuntimeError as e:
        print(f"  protected close got no fill: {e}")

    # Pass 2: if anything is left, take it out at market — being flat matters
    # more than the last few ticks of price here.
    remainder = abs(get_position_size(client, leg['product_id']))
    if remainder > 0:
        print(f"  remainder {remainder} on {leg['symbol']}, closing at market.")
        filled, fill_price = execute_order(client, leg['product_id'], remainder,
                                           'buy', reduce_only=True)
        if fill_price is None:
            fill_price = leg['entry_price']
        realized += (leg['entry_price'] - fill_price) * filled

    # Final verification — never proceed on a half-closed leg
    if abs(get_position_size(client, leg['product_id'])) != 0:
        raise RuntimeError(f"{leg['symbol']} STILL NOT FLAT after two close passes — manual check required")

    return realized


def close_tracked_legs(client, legs: list, slippage_pct: float) -> float:
    """
    Close ONLY the legs this strategy opened. Other positions on the account
    are untouched. Returns total realized PnL from the closes so the caller
    can book it — exit PnL must never be silently discarded.
    """
    total_realized = 0.0
    for leg in legs:
        total_realized += close_leg_fully(client, leg, slippage_pct)
    return total_realized


# ===================================================================
# LEG REPLACEMENT (used by both adjustment and hard-reset paths)
# ===================================================================

def replace_leg(client, old_leg: dict, target_abs_delta: float, option_type: str,
                underlying: str, expiry_date: str, size: int, delay_sec: float,
                tie_breaker: str, min_target_delta: float, slippage_pct: float) -> tuple:
    """
    Close the old leg, wait delay_sec, open a replacement at the target |delta|.
    Returns: (new_leg_dict, realized_pnl_from_closing_old)

    Order safety:
      - the old leg is closed COMPLETELY and verified flat before the new leg
        is opened — a partial close can never stack into double exposure
      - both close and open are slippage-protected limit IOC orders
      - target delta floored at min_target_delta (prevents adjustment storms)
      - old strike excluded from the new search (never close-and-reopen same strike)
      - if the open's response is lost (timeout), the exchange is queried before
        concluding anything: if the position exists, it is adopted; only a truly
        missing fill is retried, and a final failure raises loudly
    """
    # --- Step 1: close old leg fully (verified flat, or this raises) ---
    realized = close_leg_fully(client, old_leg, slippage_pct)

    # --- Step 2: let chain prices and Greeks refresh after the close ---
    time.sleep(delay_sec)

    # --- Step 3: pick the replacement strike ---
    target  = max(target_abs_delta, min_target_delta)
    chain   = get_option_chain(client, underlying, expiry_date)
    new_opt = find_strike_by_delta(chain, target, option_type,
                                   tie_breaker=tie_breaker,
                                   exclude_product_id=old_leg['product_id'])
    new_pid  = new_opt['product_id']
    new_mark = float(new_opt['mark_price'])
    tick     = get_tick_size(client, new_pid)
    floor    = protected_limit_price(new_mark, 'sell', slippage_pct, tick)

    # --- Step 4: open the new leg; recover state if the response is lost ---
    entry  = None
    filled = 0
    try:
        filled, fill_price = execute_order(client, new_pid, size, 'sell', limit_price=floor)
        entry = fill_price if fill_price is not None else new_mark
    except Exception as e:
        print(f"  open attempt failed/lost: {e}")
        time.sleep(2)
        on_exchange = abs(get_position_size(client, new_pid))
        if on_exchange > 0:
            # The order actually executed but we lost the response — adopt it.
            filled = on_exchange
            entry  = new_mark
            print(f"  recovered: position x{filled} exists on exchange, adopting at mark {entry:.2f}")
        else:
            # One retry at market — staying two-legged matters more than the spread.
            print(f"  retrying open at market...")
            filled, fill_price = execute_order(client, new_pid, size, 'sell')
            entry = fill_price if fill_price is not None else new_mark

    print(f"  opened {new_opt['symbol']} x{filled} @ {entry:.2f} (delta {new_opt['greeks']['delta']})")

    new_leg = {
        'symbol':      new_opt['symbol'],
        'product_id':  new_pid,
        'entry_price': entry,
        'size':        filled,
        'side':        'sell'
    }
    return new_leg, realized


# ===================================================================
# CSV LOGGING
# ===================================================================

CSV_HEADER = ["timestamp", "event", "call", "put", "call_delta", "put_delta", "net_delta",
              "call_mark", "put_mark", "realized", "unrealized", "total_pnl", "target", "adj_count"]


def csv_log(writer, ts, event, call_sym, put_sym, call_delta, put_delta, net_delta,
            call_mark, put_mark, realized_pnl, unrealized_pnl, total_pnl, target, adj_count):
    writer.writerow([
        ts, event, call_sym, put_sym,
        round(call_delta, 4), round(put_delta, 4), round(net_delta, 4),
        round(call_mark, 4), round(put_mark, 4),
        round(realized_pnl, 4), round(unrealized_pnl, 4), round(total_pnl, 4),
        round(target, 4), adj_count
    ])


# ===================================================================
# PARAMETERS  (everything tunable lives here)
# ===================================================================

# --- API ---
API_KEY    = "i2PwNlNX80N5kL0LhbjhQ72W5dpgOy"
API_SECRET = "uQyGlzmpmICPfeoRmW7086566CsQOxrqKLtawYrTUmNqN9RlDWu8cPcKmZNV"
BASE_URL   = "https://cdn-ind.testnet.deltaex.org"   # testnet; live = https://api.india.delta.exchange

# --- Underlying & expiry ---
UNDERLYING  = "BTC"
EXPIRY_DATE = "10-06-2026"    # DD-MM-YYYY (Delta option chain format)

# --- Position sizing ---
LOT_SIZE = 1                  # contracts per leg (integer)

# --- Entry deltas (absolute values, balanced legs) ---
CALL_ENTRY_DELTA = 0.20
PUT_ENTRY_DELTA  = 0.20       # kept separate so either side can be re-biased if needed

# --- Rebalance triggers ---
NET_DELTA_THRESHOLD = 0.15    # |net position delta per lot| beyond this  =>  normal rebalance
HARD_RESET_DELTA    = 0.45    # larger leg's |delta| must exceed this for a hard reset...
LEG_DIFF_THRESHOLD  = 0.10    # ...AND legs must be at least this asymmetric (both required)

# --- Strike selection ---
STRIKE_TIE_BREAKER = 'otm'    # 'otm' (safer) or 'itm' (richer) when strikes are equidistant
MIN_TARGET_DELTA   = 0.10     # floor for replacement-leg delta; prevents adjustment storms

# --- Execution protection ---
MAX_SLIPPAGE_PCT = 0.05       # worst acceptable fill vs mark (5% of premium); orders are
                              # limit IOC anchored to mark instead of naked market orders

# --- Exit rules ---
PROFIT_TARGET_PCT = 0.80      # close when total PnL >= 40% of entry premium
MAX_LOSS_PCT      = 1.00      # hard stop: close all when total PnL <= -100% of entry premium
TIME_STOP = datetime(2026, 6, 10, 11, 50, tzinfo=timezone.utc)   # Delta BTC options settle 12:00 UTC (17:30 IST)

# --- Operational ---
POLL_INTERVAL_SEC            = 5      # recompute every 5s; sufficient precision for these bands
DELAY_BETWEEN_CLOSE_OPEN_SEC = 1.5    # wait between closing a leg and opening its replacement
LOG_FILE                     = "strangle_log.csv"


# ===================================================================
# SETUP
# ===================================================================

# Sanity check: the time stop must land before the option's settlement,
# otherwise the bot keeps polling a dead chain on expiry day and never exits.
_expiry_settlement = datetime.strptime(EXPIRY_DATE, "%d-%m-%Y").replace(
    hour=12, minute=0, tzinfo=timezone.utc)
if TIME_STOP >= _expiry_settlement:
    raise SystemExit(f"CONFIG ERROR: TIME_STOP {TIME_STOP} is not before expiry settlement {_expiry_settlement}")

client = connect_to_delta(API_KEY, API_SECRET, BASE_URL)

write_header = (not os.path.exists(LOG_FILE)) or os.path.getsize(LOG_FILE) == 0
f = open(LOG_FILE, "a", newline="")
log_writer = csv.writer(f)
if write_header:
    log_writer.writerow(CSV_HEADER)
    f.flush()

# Pick the initial two strikes
chain     = get_option_chain(client, UNDERLYING, EXPIRY_DATE)
init_call = find_strike_by_delta(chain, CALL_ENTRY_DELTA, 'call', STRIKE_TIE_BREAKER)
init_put  = find_strike_by_delta(chain, PUT_ENTRY_DELTA,  'put',  STRIKE_TIE_BREAKER)

print(f"Initial CALL: {init_call['symbol']}  delta={init_call['greeks']['delta']}  mark={init_call['mark_price']}")
print(f"Initial PUT : {init_put['symbol']}   delta={init_put['greeks']['delta']}   mark={init_put['mark_price']}")

# Open both legs with slippage protection. If the second entry fails, the
# first is closed immediately — the script must never crash out holding a
# naked single leg with nothing managing it.
entered_legs = []
try:
    for opt in (init_call, init_put):
        mark  = float(opt['mark_price'])
        tick  = get_tick_size(client, opt['product_id'])
        floor = protected_limit_price(mark, 'sell', MAX_SLIPPAGE_PCT, tick)
        filled, fill_price = execute_order(client, opt['product_id'], LOT_SIZE, 'sell',
                                           limit_price=floor)
        entered_legs.append({
            'symbol':      opt['symbol'],
            'product_id':  opt['product_id'],
            'entry_price': fill_price if fill_price is not None else mark,
            'size':        filled,
            'side':        'sell'
        })
except Exception as e:
    print(f"ENTRY FAILED: {e}")
    if entered_legs:
        print("Unwinding partially entered position...")
        close_tracked_legs(client, entered_legs, MAX_SLIPPAGE_PCT)
    f.close()
    raise SystemExit("Entry aborted — account left flat.")

call_leg, put_leg = entered_legs[0], entered_legs[1]

# Initial premium drives the profit target and stop-loss. Stays fixed for the run.
INITIAL_PREMIUM   = (call_leg['entry_price'] * call_leg['size']
                     + put_leg['entry_price'] * put_leg['size'])
PROFIT_TARGET_ABS = PROFIT_TARGET_PCT * INITIAL_PREMIUM
MAX_LOSS_ABS      = MAX_LOSS_PCT * INITIAL_PREMIUM

realized_pnl     = 0.0   # accumulates closed-leg PnL across adjustments AND final exit
adjustment_count = 0

print(f"Initial premium collected: {INITIAL_PREMIUM:.2f}")
print(f"Profit target (absolute) : {PROFIT_TARGET_ABS:.2f}")
print(f"Max loss     (absolute)  : {MAX_LOSS_ABS:.2f}")


# ===================================================================
# MAIN LOOP
# ===================================================================

while True:
    try:
        now     = datetime.now(timezone.utc)
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")

        # --- TIME STOP ---
        if now >= TIME_STOP:
            print("TIME STOP reached — closing tracked legs.")
            realized_pnl += close_tracked_legs(client, [call_leg, put_leg], MAX_SLIPPAGE_PCT)
            csv_log(log_writer, now_str, "TIME_STOP",
                    call_leg['symbol'], put_leg['symbol'],
                    0, 0, 0, 0, 0,
                    realized_pnl, 0, realized_pnl, PROFIT_TARGET_ABS, adjustment_count)
            f.flush()
            break

        # --- CURRENT STATE (skip tick if Greeks are stale/missing) ---
        c = fetch_leg_state(client, call_leg['symbol'])
        p = fetch_leg_state(client, put_leg['symbol'])
        if c is None or p is None:
            print(f"{now_str}  stale/missing greeks — skipping tick")
            time.sleep(POLL_INTERVAL_SEC)
            continue

        call_delta, call_mark = c['delta'], c['mark_price']
        put_delta,  put_mark  = p['delta'], p['mark_price']

        # Signed delta of our SHORT strangle, weighted by actual leg sizes
        # (sizes can differ after a partial fill), normalized per lot so the
        # thresholds keep the same meaning regardless of LOT_SIZE:
        #   neutral:  call=+0.20, put=-0.20  ->  net = 0
        #   BTC up  -> net negative (position is net SHORT)
        #   BTC down-> net positive (position is net LONG)
        position_delta = -(call_delta * call_leg['size'] + put_delta * put_leg['size'])
        net_delta      = position_delta / LOT_SIZE

        # P&L: short PnL = entry - current_mark (mark used for *unrealized* only;
        # realized PnL is always booked from actual fill prices)
        unreal_call = (call_leg['entry_price'] - call_mark) * call_leg['size']
        unreal_put  = (put_leg['entry_price']  - put_mark)  * put_leg['size']
        unrealized  = unreal_call + unreal_put
        total_pnl   = realized_pnl + unrealized

        event = "tick"

        # --- PROFIT TARGET ---
        if total_pnl >= PROFIT_TARGET_ABS:
            print(f"PROFIT TARGET hit. total_pnl={total_pnl:.2f} target={PROFIT_TARGET_ABS:.2f}")
            realized_pnl += close_tracked_legs(client, [call_leg, put_leg], MAX_SLIPPAGE_PCT)
            csv_log(log_writer, now_str, "PROFIT_TARGET",
                    call_leg['symbol'], put_leg['symbol'],
                    call_delta, put_delta, net_delta, call_mark, put_mark,
                    realized_pnl, 0, realized_pnl, PROFIT_TARGET_ABS, adjustment_count)
            f.flush()
            break

        # --- MAX LOSS STOP ---
        if total_pnl <= -MAX_LOSS_ABS:
            print(f"MAX LOSS hit. total_pnl={total_pnl:.2f} limit={-MAX_LOSS_ABS:.2f} — closing all.")
            realized_pnl += close_tracked_legs(client, [call_leg, put_leg], MAX_SLIPPAGE_PCT)
            csv_log(log_writer, now_str, "MAX_LOSS_STOP",
                    call_leg['symbol'], put_leg['symbol'],
                    call_delta, put_delta, net_delta, call_mark, put_mark,
                    realized_pnl, 0, realized_pnl, PROFIT_TARGET_ABS, adjustment_count)
            f.flush()
            break

        # --- HARD RESET: one leg far more extended than the other AND deep ITM ---
        # Both conditions must hold:
        #   (a) leg asymmetry  | |callD| - |putD| |  >  LEG_DIFF_THRESHOLD
        #   (b) the bigger leg's |delta|            >  HARD_RESET_DELTA
        # If both legs drift toward ATM together (IV spike), the strangle is
        # still balanced and we don't reset.
        leg_diff   = abs(call_delta) - abs(put_delta)   # signed: positive = call is bigger
        bigger_leg = max(abs(call_delta), abs(put_delta))

        if abs(leg_diff) > LEG_DIFF_THRESHOLD and bigger_leg > HARD_RESET_DELTA:
            if leg_diff > 0:
                event = f"HARD_RESET_CALL (call={call_delta:.3f}, put={put_delta:.3f}, diff={leg_diff:.3f})"
                print(event)
                # Call is the runaway leg: close it, open new call at put's |delta|
                new_leg, realized_inc = replace_leg(
                    client, call_leg, abs(put_delta), 'call',
                    UNDERLYING, EXPIRY_DATE, LOT_SIZE, DELAY_BETWEEN_CLOSE_OPEN_SEC,
                    STRIKE_TIE_BREAKER, MIN_TARGET_DELTA, MAX_SLIPPAGE_PCT
                )
                call_leg = new_leg
            else:
                event = f"HARD_RESET_PUT (call={call_delta:.3f}, put={put_delta:.3f}, diff={leg_diff:.3f})"
                print(event)
                # Put is the runaway leg: close it, open new put at call's |delta|
                new_leg, realized_inc = replace_leg(
                    client, put_leg, abs(call_delta), 'put',
                    UNDERLYING, EXPIRY_DATE, LOT_SIZE, DELAY_BETWEEN_CLOSE_OPEN_SEC,
                    STRIKE_TIE_BREAKER, MIN_TARGET_DELTA, MAX_SLIPPAGE_PCT
                )
                put_leg = new_leg
            realized_pnl += realized_inc
            adjustment_count += 1

        # --- REGULAR REBALANCE: net delta out of band, neither leg ITM yet ---
        elif abs(net_delta) > NET_DELTA_THRESHOLD:
            if net_delta < 0:
                # BTC moved up: put is the winner (further OTM, premium dropped).
                # Book the put, re-sell at call's current |delta| to restore symmetry.
                event = f"REBAL_PUT (net_delta={net_delta:.3f})"
                print(event)
                new_leg, realized_inc = replace_leg(
                    client, put_leg, abs(call_delta), 'put',
                    UNDERLYING, EXPIRY_DATE, LOT_SIZE, DELAY_BETWEEN_CLOSE_OPEN_SEC,
                    STRIKE_TIE_BREAKER, MIN_TARGET_DELTA, MAX_SLIPPAGE_PCT
                )
                put_leg = new_leg
            else:
                # BTC moved down: call is the winner. Book it, re-sell at put's |delta|.
                event = f"REBAL_CALL (net_delta={net_delta:.3f})"
                print(event)
                new_leg, realized_inc = replace_leg(
                    client, call_leg, abs(put_delta), 'call',
                    UNDERLYING, EXPIRY_DATE, LOT_SIZE, DELAY_BETWEEN_CLOSE_OPEN_SEC,
                    STRIKE_TIE_BREAKER, MIN_TARGET_DELTA, MAX_SLIPPAGE_PCT
                )
                call_leg = new_leg
            realized_pnl += realized_inc
            adjustment_count += 1

        # --- LOG every tick ---
        csv_log(log_writer, now_str, event,
                call_leg['symbol'], put_leg['symbol'],
                call_delta, put_delta, net_delta, call_mark, put_mark,
                realized_pnl, unrealized, total_pnl, PROFIT_TARGET_ABS, adjustment_count)
        f.flush()

    except Exception as e:
        print(f"Loop error: {e}")

    # Pause between iterations — keeps the loop from hammering the API.
    # 5-second precision is sufficient for these delta bands.
    time.sleep(POLL_INTERVAL_SEC)

f.close()
print(f"Strategy run complete. Final realized PnL: {realized_pnl:.2f}")