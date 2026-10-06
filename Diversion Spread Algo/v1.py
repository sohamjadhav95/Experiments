import time
import csv
from datetime import datetime
import MetaTrader5 as mt5

def connect_to_mt5(account_id: int, password: str, server: str) -> bool:
    """
    Connects to MetaTrader 5 using the provided credentials.
    
    Args:
        account_id (int): The MetaTrader 5 account number.
        password (str): The password for the account.
        server (str): The server name (e.g., "MetaQuotes-Demo").
        
    Returns:
        bool: True if connection is successful, False otherwise.

        # Example usage (commented out):
        # if connect_to_mt5(12345678, "your_password", "Your-Broker-Server"):
        #     print(mt5.account_info())
        #     mt5.shutdown()
    """
    # Initialize the MT5 terminal
    if not mt5.initialize():
        print(f"initialize() failed, error code = {mt5.last_error()}")
        return False

    # Attempt to log in to the specific account
    authorized = mt5.login(login=account_id, password=password, server=server)
    
    if authorized:
        print(f"Successfully connected to account #{account_id} on {server}")
        return True
    else:
        print(f"Failed to connect to account #{account_id}, error code: {mt5.last_error()}")
        # Shut down connection if login failed but initialize succeeded
        mt5.shutdown()
        return False

def get_current_tick(asset_symbol):
    if not mt5.symbol_select(asset_symbol, True):
        print(f"Failed to select {asset_symbol}")
        return
    tick = mt5.symbol_info_tick(asset_symbol)
    cmp = (tick.ask + tick.bid) / 2
    return cmp

def send_order(symbol, lot_size, order_type, comment):
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot_size,
        "type": order_type,
        "magic": 234567,
        "comment": comment,
        "type_filling": mt5.ORDER_FILLING_FOK,
        "type_time": mt5.ORDER_TIME_GTC,
    }

    order_result = mt5.order_send(request)
    if order_result is None:
        print(f"Order send failed, error code = {mt5.last_error()}")
        return False
    
    return order_result

def close_all_positions():
    """
    Finds and closes all currently open positions on the account.
    
    Returns:
        bool: True if all positions were closed successfully or no positions existed, False otherwise.
    """
    positions = mt5.positions_get()
    if positions is None or len(positions) == 0:
        print("No open positions to close.")
        return True

    all_closed = True
    for pos in positions:
        # Determine the opposite order type to close the trade
        order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        
        # Get the current tick for the specific symbol
        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            print(f"Failed to get tick for {pos.symbol}, cannot close position {pos.ticket}")
            all_closed = False
            continue
            
        # Set execution price based on order type (Bid for Sell, Ask for Buy)
        price = tick.bid if order_type == mt5.ORDER_TYPE_SELL else tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": order_type,
            "position": pos.ticket,  # Link the order to the specific position ticket
            "price": price,
            "deviation": 20,
            "magic": pos.magic,
            "comment": "Close All Positions",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_FOK, # Adjust to IOC if your broker rejects FOK
        }

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            retcode = result.retcode if result else mt5.last_error()
            print(f"Failed to close position {pos.ticket} on {pos.symbol}. Retcode: {retcode}")
            all_closed = False
        else:
            print(f"Position {pos.ticket} closed successfully.")

    return all_closed

def csv_logging(writer, date_time, symbol_1, symbol_2, position, symbol_1_price, symbol_2_price, index_1, index_2, index_spread, flip_occured, flip_loss):

    # f = open("correlation_deployment.csv", "a", newline="")
    # csv_writer = csv.writer(f)
    """
    Writes a row to the CSV using the provided writer object.
    Call this inside your 100ms loop.
    """
    writer.writerow([date_time, symbol_1, symbol_2, position, symbol_1_price, symbol_2_price, index_1, index_2, index_spread, flip_occured, flip_loss])
    return


# Start MT5 Connection -------------------------------------------------------------------------------
connect_to_mt5(463642609, "Soham@987", "Exness-MT5Trial17")

# Create and open csv --------------------------------------------------------------------------------
f = open("correlation_deployment.csv", "a", newline="")
csv_writer = csv.writer(f)

# All Parameters ========================================================================================

# Format: (Year, Month, Day, Hour, Minute, Second)
START_TIME = 2026, 7, 22, 13, 33, 55

SYMBOL_1 = "US30m"
SYMBOL_2 = "USDJPYm"

LOT_SIZE_1 = 0.1
LOT_SIZE_2 = 0.1

DISTANCE_N = 0.02

# Initial Index
INDEX_1 = 1000
INDEX_2 = 1000

# Initial Postions
BUY = mt5.ORDER_TYPE_BUY
SELL = mt5.ORDER_TYPE_SELL

asset_1_position = "Buy"
asset_2_position = "Sell"

Prev_Asset_1_CMP = get_current_tick(SYMBOL_1)
Prev_Asset_2_CMP = get_current_tick(SYMBOL_2)


# MAIN LOOP =============================================================================================


while True:
    