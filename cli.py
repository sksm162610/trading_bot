from __future__ import annotations
import os
import time
from decimal import Decimal
from typing import Optional
import typer
from dotenv import load_dotenv
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from bot.client import BinanceFuturesClient, BinanceClientError
from bot.logging_config import setup_logging
from bot.orders import GridResult, OCOResult, OrderManager, OrderResult, TWAPResult
from bot.validators import (
    validate_order_type,
    validate_positive_decimal,
    validate_positive_int,
    validate_price,
    validate_quantity,
    validate_side,
    validate_stop_price,
    validate_symbol,
)



load_dotenv() 

app = typer.Typer(
    name="trading-bot",
    help="[bold cyan]Binance Futures Testnet Trading Bot[/bold cyan]\n\n"
         "Supports MARKET · LIMIT · STOP_MARKET · STOP_LIMIT · OCO · TWAP · GRID",
    rich_markup_mode="rich",
    add_completion=False,
)
console     = Console()
err_console = Console(stderr=True)
logger      = setup_logging()


ALGO_ORDER_NOTE = (
    "\n[bold yellow]⚠ Testnet Limitation:[/bold yellow] "
    "[yellow]STOP_MARKET, STOP_LIMIT, and OCO orders require Binance's Algo Order "
    "endpoints which are [bold]not available on the Futures Testnet[/bold].\n"
    "  → These order types work correctly on [bold]mainnet[/bold].\n"
    "  → On testnet, use [bold]MARKET[/bold] or [bold]LIMIT[/bold] orders instead.[/yellow]\n"
)



def _get_client() -> BinanceFuturesClient:
    api_key    = os.getenv("BINANCE_API_KEY", "").strip()
    api_secret = os.getenv("BINANCE_API_SECRET", "").strip()

    if not api_key or not api_secret:
        err_console.print(
            "[bold red]✗ Missing API credentials.[/bold red]\n"
            "  Create a [bold].env[/bold] file in the project root with:\n"
            "  [dim]BINANCE_API_KEY=your_key_here\n"
            "  BINANCE_API_SECRET=your_secret_here[/dim]"
        )
        raise typer.Exit(code=1)

    if len(api_key) < 10 or len(api_secret) < 10:
        err_console.print(
            "[bold red]✗ API credentials look invalid.[/bold red] "
            "Check your .env file."
        )
        raise typer.Exit(code=1)

    return BinanceFuturesClient(api_key=api_key, api_secret=api_secret)


def _val_err(msg: str) -> None:
    err_console.print(f"[bold red]Validation Error:[/bold red] {msg}")
    logger.error("Validation failed | %s", msg)
    raise typer.Exit(code=1)


def _api_err(exc: BinanceClientError) -> None:
    if exc.code == -4120:
        err_console.print(ALGO_ORDER_NOTE)
    else:
        err_console.print(f"[bold red]✗ API Error:[/bold red] {exc}")
        if exc.code:
            err_console.print(f"  [dim]Binance error code: {exc.code}[/dim]")
    raise typer.Exit(code=1)


def _side_colour(side: str) -> str:
    return "green" if side == "BUY" else "red"


def _status_colour(status: str) -> str:
    return {"FILLED": "green", "NEW": "cyan", "PARTIALLY_FILLED": "yellow", "CANCELED": "red"}.get(status, "white")


def _print_single_order(result: OrderResult, title: str = "Order Response") -> None:
    t = Table(show_header=False, box=box.SIMPLE, padding=(0, 1))
    t.add_column("Field", style="dim", width=16)
    t.add_column("Value", style="bold")
    sc = _status_colour(result.status)
    t.add_row("Order ID",      str(result.order_id))
    t.add_row("Symbol",        result.symbol)
    t.add_row("Side",          f"[{_side_colour(result.side)}]{result.side}[/]")
    t.add_row("Type",          result.order_type)
    t.add_row("Status",        f"[{sc}]{result.status}[/]")
    t.add_row("Orig Qty",      result.orig_qty)
    t.add_row("Executed Qty",  result.executed_qty)
    t.add_row("Avg Price",     result.avg_price)
    if result.price and result.price not in ("0", "0.00", "—"):
        t.add_row("Limit Price", result.price)
    if result.stop_price and result.stop_price not in ("0", "0.00", "—"):
        t.add_row("Stop Price", result.stop_price)
    if result.time_in_force:
        t.add_row("TIF", result.time_in_force)
    console.print(Panel(t, title=f"[bold]{title}[/bold]", border_style="green"))



@app.command("place-order")
def place_order(
    symbol:      str             = typer.Option(...,   "--symbol", "-s",  help="e.g. BTCUSDT"),
    side:        str             = typer.Option(...,   "--side",          help="BUY | SELL"),
    order_type:  str             = typer.Option(...,   "--type",   "-t",  help="MARKET | LIMIT | STOP_MARKET | STOP_LIMIT | OCO"),
    qty:         float           = typer.Option(...,   "--qty",    "-q",  help="Quantity (contracts)"),
    price:       Optional[float] = typer.Option(None,  "--price",  "-p",  help="Limit price (LIMIT / STOP_LIMIT)"),
    stop_price:  Optional[float] = typer.Option(None,  "--stop-price",    help="Stop trigger price (STOP_MARKET / STOP_LIMIT)"),
    tp_price:    Optional[float] = typer.Option(None,  "--tp-price",      help="Take-profit limit price (OCO)"),
    sl_price:    Optional[float] = typer.Option(None,  "--sl-price",      help="Stop-loss trigger price (OCO)"),
    tif:         str             = typer.Option("GTC", "--tif",           help="Time-in-force: GTC | IOC | FOK"),
) -> None:
    """
    Place a single order: MARKET, LIMIT, STOP_MARKET, STOP_LIMIT, or OCO.

    \b
    Examples:
      Market BUY:
        python cli.py place-order --symbol BTCUSDT --side BUY --type MARKET --qty 0.001

      Limit SELL:
        python cli.py place-order --symbol BTCUSDT --side SELL --type LIMIT --qty 0.001 --price 95000

      Stop-Market BUY:
        python cli.py place-order --symbol BTCUSDT --side BUY --type STOP_MARKET --qty 0.001 --stop-price 60000

      Stop-Limit SELL:
        python cli.py place-order --symbol BTCUSDT --side SELL --type STOP_LIMIT --qty 0.001 --stop-price 60000 --price 59800

      OCO SELL (take-profit + stop-loss):
        python cli.py place-order --symbol BTCUSDT --side SELL --type OCO --qty 0.001 --tp-price 72000 --sl-price 62000
    """
    console.print()
    ot_upper = order_type.strip().upper()
    if ot_upper in {"STOP_MARKET", "STOP_LIMIT", "OCO"}:
        console.print(ALGO_ORDER_NOTE)
        raise typer.Exit(code=0)
    try:
        sym  = validate_symbol(symbol)
        sd   = validate_side(side)
        ot   = validate_order_type(order_type)
        q    = validate_quantity(qty)
        pr   = validate_price(price, ot)
        sp   = validate_stop_price(stop_price, ot)
        tp   = validate_positive_decimal(tp_price, "--tp-price")
        sl   = validate_positive_decimal(sl_price, "--sl-price")
    except ValueError as exc:
        _val_err(str(exc))

    if ot == "OCO" and (tp is None or sl is None):
        _val_err("OCO orders require both --tp-price (take-profit) and --sl-price (stop-loss).")
    rows = [("Symbol", sym), ("Side", f"[{_side_colour(sd)}]{sd}[/]"), ("Type", ot), ("Qty", str(q))]
    if pr: rows.append(("Limit Price", str(pr)))
    if sp: rows.append(("Stop Price",  str(sp)))
    if tp: rows.append(("Take-Profit", str(tp)))
    if sl: rows.append(("Stop-Loss",   str(sl)))
    t = Table(show_header=False, box=box.SIMPLE, padding=(0, 1))
    t.add_column("k", style="dim"); t.add_column("v", style="bold")
    for k, v in rows: t.add_row(k, v)
    console.print(Panel(t, title="[bold]Order Request[/bold]", border_style="cyan"))

    client  = _get_client()
    manager = OrderManager(client)

    try:
        if ot == "MARKET":
            result = manager.place_market_order(sym, sd, q)
            _print_single_order(result)

        elif ot == "LIMIT":
            result = manager.place_limit_order(sym, sd, q, pr, tif)
            _print_single_order(result)

    except BinanceClientError as exc:
        _api_err(exc)
    except Exception as exc:
        err_console.print(f"[bold red]✗ Unexpected error:[/bold red] {exc}")
        logger.exception("Unexpected error in place-order")
        raise typer.Exit(code=1)

    console.print("[bold green]✓ Order placed successfully![/bold green]\n")


@app.command("twap")
def twap(
    symbol:   str   = typer.Option(..., "--symbol", "-s",  help="e.g. BTCUSDT"),
    side:     str   = typer.Option(..., "--side",          help="BUY | SELL"),
    qty:      float = typer.Option(..., "--qty",    "-q",  help="Total quantity to execute"),
    slices:   int   = typer.Option(..., "--slices",        help="Number of equal sub-orders (min 2)"),
    interval: int   = typer.Option(..., "--interval",      help="Seconds between each slice"),
) -> None:
    """
    TWAP — Time-Weighted Average Price execution.

    Splits a large order into equal market-order slices placed at fixed intervals
    to reduce market impact.

    \b
    Example:
      python cli.py twap --symbol BTCUSDT --side BUY --qty 0.01 --slices 5 --interval 10
    """
    console.print()

    try:
        sym = validate_symbol(symbol)
        sd  = validate_side(side)
        q   = validate_quantity(qty)
        s   = validate_positive_int(slices, "--slices", minimum=2)
        iv  = validate_positive_int(interval, "--interval", minimum=1)
    except ValueError as exc:
        _val_err(str(exc))

    slice_qty  = (q / s).quantize(Decimal("0.001"))
    total_dur  = (s - 1) * iv

    t = Table(show_header=False, box=box.SIMPLE, padding=(0, 1))
    t.add_column("k", style="dim"); t.add_column("v", style="bold")
    for k, v in [
        ("Symbol",        sym),
        ("Side",          f"[{_side_colour(sd)}]{sd}[/]"),
        ("Total Qty",     str(q)),
        ("Slices",        str(s)),
        ("Per Slice",     str(slice_qty)),
        ("Interval",      f"{iv}s"),
        ("Est. Duration", f"~{total_dur}s"),
    ]: t.add_row(k, v)
    console.print(Panel(t, title="[bold]TWAP Strategy[/bold]", border_style="cyan"))

    client  = _get_client()
    manager = OrderManager(client)

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]TWAP[/bold cyan] {task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total} slices"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"executing {sym}", total=s)
        results: list[OrderResult] = []

        def on_slice(i, total, result):
            results.append(result)
            progress.advance(task)
            progress.update(task, description=f"slice {i}/{total} • orderId={result.order_id}")

        try:
            twap_result = manager.place_twap_order(
                symbol=sym, side=sd, total_qty=q,
                slices=s, interval_sec=iv,
                progress_cb=on_slice,
            )
        except BinanceClientError as exc:
            _api_err(exc)

    t2 = Table("Slice", "Order ID", "Status", "Qty", "Avg Price", box=box.ROUNDED)
    for i, o in enumerate(twap_result.orders, 1):
        sc = _status_colour(o.status)
        t2.add_row(str(i), str(o.order_id), f"[{sc}]{o.status}[/]", o.executed_qty, o.avg_price)
    console.print(Panel(t2, title="[bold]TWAP Execution Summary[/bold]", border_style="green"))
    console.print(
        f"[bold green]✓ TWAP complete[/bold green] — "
        f"filled [bold]{twap_result.filled_qty}[/bold] of [bold]{q}[/bold] | "
        f"{twap_result.success_rate}\n"
    )


@app.command("grid")
def grid(
    symbol: str   = typer.Option(..., "--symbol", "-s", help="e.g. BTCUSDT"),
    lower:  float = typer.Option(..., "--lower",        help="Lower bound of price range"),
    upper:  float = typer.Option(..., "--upper",        help="Upper bound of price range"),
    levels: int   = typer.Option(..., "--levels",       help="Number of grid levels (min 2)"),
    qty:    float = typer.Option(..., "--qty",   "-q",  help="Quantity per grid order"),
) -> None:
    """
    GRID — places evenly-spaced BUY and SELL limit orders across a price range.

    \b
    Example:
      python cli.py grid --symbol BTCUSDT --lower 60000 --upper 70000 --levels 6 --qty 0.001
    """
    console.print()

    try:
        sym = validate_symbol(symbol)
        lo  = validate_positive_decimal(lower,  "--lower")
        hi  = validate_positive_decimal(upper,  "--upper")
        lv  = validate_positive_int(levels, "--levels", minimum=2)
        q   = validate_quantity(qty)
    except ValueError as exc:
        _val_err(str(exc))

    if lo >= hi:
        _val_err("--lower must be less than --upper.")

    step = (hi - lo) / (lv - 1)

    t = Table(show_header=False, box=box.SIMPLE, padding=(0, 1))
    t.add_column("k", style="dim"); t.add_column("v", style="bold")
    for k, v in [
        ("Symbol",       sym),
        ("Range",        f"{lo} → {hi}"),
        ("Levels",       str(lv)),
        ("Step",         f"~{step:.2f}"),
        ("Qty/Level",    str(q)),
        ("Total Orders", str(lv)),
    ]: t.add_row(k, v)
    console.print(Panel(t, title="[bold]Grid Strategy[/bold]", border_style="cyan"))

    client  = _get_client()
    manager = OrderManager(client)

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]GRID[/bold cyan] placing orders..."),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        task = progress.add_task("grid", total=lv)

        original_place = manager.place_limit_order
        def tracked_place(*args, **kwargs):
            result = original_place(*args, **kwargs)
            progress.advance(task)
            return result
        manager.place_limit_order = tracked_place

        try:
            grid_result = manager.place_grid_order(
                symbol=sym, lower_price=lo, upper_price=hi,
                levels=lv, qty_per_grid=q,
            )
        except BinanceClientError as exc:
            _api_err(exc)

    if grid_result.buy_orders:
        bt = Table("Price", "Order ID", "Status", "Qty", box=box.ROUNDED, style="green")
        for o in grid_result.buy_orders:
            bt.add_row(o.price, str(o.order_id), o.status, o.orig_qty)
        console.print(Panel(bt, title="[bold green]BUY Limit Orders[/bold green]", border_style="green"))

    if grid_result.sell_orders:
        st = Table("Price", "Order ID", "Status", "Qty", box=box.ROUNDED, style="red")
        for o in grid_result.sell_orders:
            st.add_row(o.price, str(o.order_id), o.status, o.orig_qty)
        console.print(Panel(st, title="[bold red]SELL Limit Orders[/bold red]", border_style="red"))

    console.print(
        f"[bold green]✓ Grid placed[/bold green] — "
        f"[green]{len(grid_result.buy_orders)} BUY[/green] + "
        f"[red]{len(grid_result.sell_orders)} SELL[/red] = "
        f"[bold]{grid_result.total_orders}[/bold] total orders\n"
    )


@app.command("close-position")
def close_position(
    symbol: str           = typer.Option(...,  "--symbol", "-s", help="e.g. BTCUSDT"),
    qty:    Optional[str] = typer.Option(None, "--qty",    "-q", help="Quantity to close, or 'all' for full position"),
) -> None:
    """
    Close an open position by placing a market order on the opposite side.

    \b
    Examples:
      Close 0.03 BTC of a LONG position:
        python cli.py close-position --symbol BTCUSDT --qty 0.03

      Close the entire position:
        python cli.py close-position --symbol BTCUSDT --qty all
    """
    console.print()

    try:
        sym = validate_symbol(symbol)
    except ValueError as exc:
        _val_err(str(exc))

    client  = _get_client()
    manager = OrderManager(client)

    # Fetch current position
    try:
        info = client.get_account_info()
    except BinanceClientError as exc:
        _api_err(exc)

    positions = [
        p for p in info.get("positions", [])
        if p["symbol"] == sym and float(p.get("positionAmt", 0)) != 0
    ]

    if not positions:
        err_console.print(f"[bold red]✗ No open position found for [bold]{sym}[/bold].[/bold red]")
        raise typer.Exit(code=1)

    position     = positions[0]
    position_amt = float(position["positionAmt"])
    available    = Decimal(str(abs(position_amt)))
    side         = "LONG" if position_amt > 0 else "SHORT"
    close_side   = "SELL" if position_amt > 0 else "BUY"  # opposite to flatten

    if qty is None or qty.strip().lower() == "all":
        close_qty = available
    else:
        try:
            close_qty = validate_quantity(qty)
        except ValueError as exc:
            _val_err(str(exc))

        if close_qty > available:
            err_console.print(
                f"[bold red]✗ Insufficient position quantity.[/bold red]\n"
                f"  Requested  : [bold]{close_qty}[/bold] {sym[:3]}\n"
                f"  Available  : [bold]{available}[/bold] {sym[:3]}\n"
                f"  [dim]Tip: Use --qty {available} or --qty all to close the full position.[/dim]"
            )
            raise typer.Exit(code=1)
        
    t = Table(show_header=False, box=box.SIMPLE, padding=(0, 1))
    t.add_column("k", style="dim"); t.add_column("v", style="bold")
    for k, v in [
        ("Symbol",        sym),
        ("Position",      f"[{'green' if side == 'LONG' else 'red'}]{side}[/] {available}"),
        ("Closing Qty",   str(close_qty)),
        ("Order Side",    f"[{_side_colour(close_side)}]{close_side}[/] (market)"),
        ("Entry Price",   position.get("entryPrice", "—")),
        ("Unrealised PnL",position.get("unrealizedProfit", "—")),
    ]: t.add_row(k, v)
    console.print(Panel(t, title="[bold]Close Position Request[/bold]", border_style="yellow"))

    try:
        result = manager.place_market_order(sym, close_side, close_qty)
        _print_single_order(result, title="Close Position Response")
    except BinanceClientError as exc:
        _api_err(exc)
    except Exception as exc:
        err_console.print(f"[bold red]✗ Unexpected error:[/bold red] {exc}")
        logger.exception("Unexpected error in close-position")
        raise typer.Exit(code=1)

    console.print(f"[bold green]✓ Position closed successfully — {close_qty} {sym[:3]} {side} → {close_side}[/bold green]\n")


@app.command("cancel-order")
def cancel_order(
    symbol:   str = typer.Option(..., "--symbol", "-s",        help="e.g. BTCUSDT"),
    order_id: int = typer.Option(..., "--order-id", "-o",      help="Order ID to cancel"),
) -> None:
    """
    Cancel a specific open order by Order ID.

    \b
    Example:
      python cli.py cancel-order --symbol BTCUSDT --order-id 15008115849
    """
    console.print()

    try:
        sym = validate_symbol(symbol)
    except ValueError as exc:
        _val_err(str(exc))

    client = _get_client()

    try:
        response = client._request(
            "DELETE", "/fapi/v1/order",
            params={"symbol": sym, "orderId": order_id},
        )
    except BinanceClientError as exc:
        if exc.code == -2011:
            err_console.print(
                f"[bold red]✗ Order not found.[/bold red]\n"
                f"  Order ID [bold]{order_id}[/bold] does not exist or is already filled/cancelled.\n"
                f"  [dim]Tip: Run 'python cli.py open-orders --symbol {sym}' to see active orders.[/dim]"
            )
        else:
            _api_err(exc)
        raise typer.Exit(code=1)

    result = OrderResult.from_api_response(response)
    _print_single_order(result, title="Cancelled Order")
    console.print(f"[bold green]✓ Order {order_id} cancelled successfully.[/bold green]\n")



@app.command("cancel-all")
def cancel_all(
    symbol: str = typer.Option(..., "--symbol", "-s", help="e.g. BTCUSDT"),
) -> None:
    """
    Cancel ALL open orders for a symbol.

    \b
    Example:
      python cli.py cancel-all --symbol BTCUSDT
    """
    console.print()

    try:
        sym = validate_symbol(symbol)
    except ValueError as exc:
        _val_err(str(exc))

    client = _get_client()

    try:
        response = client._request(
            "DELETE", "/fapi/v1/allOpenOrders",
            params={"symbol": sym},
        )
    except BinanceClientError as exc:
        _api_err(exc)

    count = response.get("code", None)
    console.print(
        f"[bold green]✓ All open orders cancelled for [bold]{sym}[/bold].[/bold green]\n"
    )
    logger.info("cancel-all | symbol=%s | response=%s", sym, response)


@app.command("open-orders")
def open_orders(
    symbol: str = typer.Option(..., "--symbol", "-s", help="e.g. BTCUSDT"),
) -> None:
    """
    List all open orders for a symbol (with Order IDs for cancellation).

    \b
    Example:
      python cli.py open-orders --symbol BTCUSDT
    """
    console.print()

    try:
        sym = validate_symbol(symbol)
    except ValueError as exc:
        _val_err(str(exc))

    client = _get_client()

    try:
        orders = client._request(
            "GET", "/fapi/v1/openOrders",
            params={"symbol": sym},
        )
    except BinanceClientError as exc:
        _api_err(exc)

    if not orders:
        console.print(f"[dim]No open orders for {sym}.[/dim]\n")
        return

    t = Table("Order ID", "Side", "Type", "Qty", "Price", "Stop Price", "Status", box=box.ROUNDED)
    for o in orders:
        side   = o.get("side", "—")
        status = o.get("status", "—")
        t.add_row(
            str(o.get("orderId", "—")),
            f"[{_side_colour(side)}]{side}[/]",
            o.get("type", "—"),
            o.get("origQty", "—"),
            o.get("price", "—"),
            o.get("stopPrice", "—") if o.get("stopPrice", "0") != "0" else "—",
            f"[{_status_colour(status)}]{status}[/]",
        )
    console.print(Panel(t, title=f"[bold]Open Orders — {sym}[/bold]", border_style="cyan"))
    console.print(f"[dim]Total: {len(orders)} open order(s). Use cancel-order --order-id <ID> to cancel.[/dim]\n")



@app.command("account-info")
def account_info() -> None:
    """Display futures account balances and open positions."""
    client = _get_client()
    try:
        info = client.get_account_info()
    except BinanceClientError as exc:
        _api_err(exc)

    assets = [a for a in info.get("assets", []) if float(a.get("walletBalance", 0)) > 0]
    if assets:
        bt = Table("Asset", "Wallet Balance", "Available", "Unrealised PnL", box=box.ROUNDED)
        for a in assets:
            bt.add_row(a["asset"], a["walletBalance"], a["availableBalance"], a.get("unrealizedProfit", "—"))
        console.print(Panel(bt, title="[bold]Balances[/bold]", border_style="cyan"))

    positions = [p for p in info.get("positions", []) if float(p.get("positionAmt", 0)) != 0]
    if positions:
        pt = Table("Symbol", "Side", "Qty", "Entry Price", "Unrealised PnL", box=box.ROUNDED)
        for p in positions:
            amt  = float(p["positionAmt"])
            side = "LONG" if amt > 0 else "SHORT"
            c    = "green" if amt > 0 else "red"
            pt.add_row(
                p["symbol"],
                f"[{c}]{side}[/]",
                str(abs(amt)),
                p.get("entryPrice", "—"),
                p.get("unrealizedProfit", "—"),
            )
        console.print(Panel(pt, title="[bold]Open Positions[/bold]", border_style="yellow"))
    else:
        console.print("[dim]No open positions.[/dim]\n")


@app.command("ping")
def ping() -> None:
    client = _get_client()
    if client.ping():
        console.print("[bold green]✓ Connected to Binance Futures Testnet[/bold green]")
    else:
        err_console.print("[bold red]✗ Cannot reach Binance Futures Testnet[/bold red]")
        raise typer.Exit(code=1)



if __name__ == "__main__":
    app()
