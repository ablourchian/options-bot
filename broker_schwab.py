"""
Schwab broker integration — wraps schwab-py for quotes, options chains,
account info, and order placement.

First-time setup:
    python broker_schwab.py --auth
    (opens browser, log in with Schwab credentials, paste the redirect URL back)

After auth, the token is saved to .schwab_token.json and refreshed automatically.
"""
import os
import json
import asyncio
import argparse
from dotenv import load_dotenv

load_dotenv()

SCHWAB_APP_KEY    = os.getenv("SCHWAB_APP_KEY")
SCHWAB_APP_SECRET = os.getenv("SCHWAB_APP_SECRET")
SCHWAB_ACCOUNT_ID = os.getenv("SCHWAB_ACCOUNT_ID")
TOKEN_PATH        = os.path.join(os.path.dirname(__file__), ".schwab_token.json")
CALLBACK_URL      = "https://127.0.0.1"


def get_client():
    """Return an authenticated schwab client, refreshing token if needed."""
    import schwab
    if not os.path.exists(TOKEN_PATH):
        raise RuntimeError(
            "Not authenticated. Run:  python broker_schwab.py --auth"
        )
    return schwab.auth.client_from_token_file(
        token_path=TOKEN_PATH,
        api_key=SCHWAB_APP_KEY,
        app_secret=SCHWAB_APP_SECRET,
    )


def do_auth():
    """Interactive OAuth flow — opens browser, captures token."""
    import schwab
    print("\n  Opening browser for Schwab login...")
    print("  After you log in and are redirected, copy the FULL URL from your browser")
    print("  and paste it here.\n")
    client = schwab.auth.client_from_manual_flow(
        api_key=SCHWAB_APP_KEY,
        app_secret=SCHWAB_APP_SECRET,
        callback_url=CALLBACK_URL,
        token_path=TOKEN_PATH,
    )
    print(f"\n  Auth successful. Token saved to {TOKEN_PATH}")
    return client


def get_account_info():
    """Return account balances and positions."""
    c = get_client()
    resp = c.get_account(SCHWAB_ACCOUNT_ID, fields=[c.Account.Fields.POSITIONS])
    resp.raise_for_status()
    data = resp.json()
    acct = data.get("securitiesAccount", {})
    bal  = acct.get("currentBalances", {})
    return {
        "buying_power":    bal.get("buyingPower", 0),
        "cash":            bal.get("cashBalance", 0),
        "portfolio_value": bal.get("liquidationValue", 0),
        "positions":       acct.get("positions", []),
    }


def get_option_chain(symbol: str, dte_min: int = 7, dte_max: int = 45,
                     strike_count: int = 10) -> dict:
    """
    Fetch the full option chain for a symbol.
    Returns dict keyed by expiration date → {calls: [...], puts: [...]}.
    """
    import schwab
    from datetime import date, timedelta
    c = get_client()
    today = date.today()

    resp = c.get_option_chain(
        symbol,
        contract_type=c.Options.ContractType.ALL,
        strike_count=strike_count,
        from_date=today + timedelta(days=dte_min),
        to_date=today + timedelta(days=dte_max),
        option_type=c.Options.Type.ALL,
    )
    resp.raise_for_status()
    return resp.json()


def place_option_order(symbol: str, option_symbol: str, quantity: int,
                       instruction: str, order_type: str = "LIMIT",
                       limit_price: float = None, dry_run: bool = True):
    """
    Place a single-leg option order.

    instruction : "BUY_TO_OPEN" | "SELL_TO_OPEN" | "BUY_TO_CLOSE" | "SELL_TO_CLOSE"
    order_type  : "LIMIT" | "MARKET"
    dry_run     : if True, prints the order but does NOT submit it
    """
    import schwab

    order = (
        schwab.orders.options.option_buy_to_open_limit(option_symbol, quantity, limit_price)
        if instruction == "BUY_TO_OPEN" else
        schwab.orders.options.option_sell_to_open_limit(option_symbol, quantity, limit_price)
        if instruction == "SELL_TO_OPEN" else
        schwab.orders.options.option_buy_to_close_limit(option_symbol, quantity, limit_price)
        if instruction == "BUY_TO_CLOSE" else
        schwab.orders.options.option_sell_to_close_limit(option_symbol, quantity, limit_price)
    )

    print(f"\n  Order: {instruction} {quantity}x {option_symbol} @ ${limit_price}")

    if dry_run:
        print("  [DRY RUN] Order NOT submitted. Set dry_run=False to execute.")
        return None

    c = get_client()
    resp = c.place_order(SCHWAB_ACCOUNT_ID, order)
    resp.raise_for_status()
    order_id = resp.headers.get("Location", "").split("/")[-1]
    print(f"  Order submitted. ID: {order_id}")
    return order_id


def get_open_orders():
    """Return all open orders on the account."""
    c = get_client()
    resp = c.get_orders_for_account(
        SCHWAB_ACCOUNT_ID,
        statuses=[c.Order.Status.WORKING, c.Order.Status.QUEUED],
    )
    resp.raise_for_status()
    return resp.json()


def cancel_order(order_id: str):
    c = get_client()
    resp = c.cancel_order(order_id, SCHWAB_ACCOUNT_ID)
    resp.raise_for_status()
    print(f"  Order {order_id} cancelled.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--auth",    action="store_true", help="Run OAuth flow")
    parser.add_argument("--account", action="store_true", help="Print account info")
    parser.add_argument("--chain",   metavar="SYMBOL",    help="Print option chain")
    args = parser.parse_args()

    if args.auth:
        do_auth()
    elif args.account:
        info = get_account_info()
        print(f"\n  Buying power:    ${info['buying_power']:,.2f}")
        print(f"  Cash:            ${info['cash']:,.2f}")
        print(f"  Portfolio value: ${info['portfolio_value']:,.2f}")
        print(f"  Open positions:  {len(info['positions'])}")
    elif args.chain:
        chain = get_option_chain(args.chain)
        calls = chain.get("callExpDateMap", {})
        puts  = chain.get("putExpDateMap", {})
        print(f"\n  {args.chain} — {len(calls)} call expirations, {len(puts)} put expirations")
    else:
        parser.print_help()
