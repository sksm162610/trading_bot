from __future__ import annotations
import time
import math
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN
from typing import Any, List, Optional
from bot.client import BinanceFuturesClient, BinanceClientError
from bot.logging_config import setup_logging

logger = setup_logging()

ORDER_ENDPOINT      = "/fapi/v1/order"
OCO_ENDPOINT        = "/fapi/v1/order/oco"          
BATCH_ORDER_EP      = "/fapi/v1/batchOrders"


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class OrderResult:

    order_id: int
    symbol: str
    side: str
    order_type: str
    status: str
    orig_qty: str
    executed_qty: str
    avg_price: str
    price: str
    time_in_force: Optional[str] = None
    stop_price: Optional[str] = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_api_response(cls, data: dict) -> "OrderResult":
        return cls(
            order_id     = data["orderId"],
            symbol       = data["symbol"],
            side         = data["side"],
            order_type   = data["type"],
            status       = data["status"],
            orig_qty     = data.get("origQty", "—"),
            executed_qty = data.get("executedQty", "0"),
            avg_price    = data.get("avgPrice", "—"),
            price        = data.get("price", "—"),
            time_in_force= data.get("timeInForce"),
            stop_price   = data.get("stopPrice"),
            raw          = data,
        )


@dataclass
class OCOResult:
    take_profit: OrderResult
    stop_loss:   OrderResult

    @property
    def symbol(self) -> str:
        return self.take_profit.symbol


@dataclass
class TWAPResult:
    symbol:        str
    side:          str
    total_qty:     Decimal
    slices:        int
    interval_sec:  int
    orders:        List[OrderResult] = field(default_factory=list)
    failed_slices: int = 0

    @property
    def filled_qty(self) -> Decimal:
        return sum(Decimal(o.executed_qty) for o in self.orders)

    @property
    def success_rate(self) -> str:
        placed = len(self.orders)
        return f"{placed}/{self.slices} slices placed" + (
            f" ({self.failed_slices} failed)" if self.failed_slices else ""
        )


@dataclass
class GridResult:
    symbol:      str
    lower_price: Decimal
    upper_price: Decimal
    levels:      int
    qty_per_grid: Decimal
    buy_orders:  List[OrderResult] = field(default_factory=list)
    sell_orders: List[OrderResult] = field(default_factory=list)

    @property
    def total_orders(self) -> int:
        return len(self.buy_orders) + len(self.sell_orders)



class OrderManager:

    def __init__(self, client: BinanceFuturesClient):
        self.client = client

    def _place_order(self, payload: dict[str, Any]) -> OrderResult:
        logger.info(
            "PLACING ORDER | symbol=%s | side=%s | type=%s | qty=%s",
            payload.get("symbol"), payload.get("side"),
            payload.get("type"),   payload.get("quantity"),
        )
        try:
            response = self.client._request("POST", ORDER_ENDPOINT, params=payload)
        except BinanceClientError as exc:
            logger.error(
                "ORDER FAILED | symbol=%s | side=%s | type=%s | error=%s | code=%s",
                payload.get("symbol"), payload.get("side"),
                payload.get("type"), exc, exc.code,
            )
            raise

        result = OrderResult.from_api_response(response)
        logger.info(
            "ORDER SUCCESS | orderId=%s | status=%s | executedQty=%s | avgPrice=%s",
            result.order_id, result.status, result.executed_qty, result.avg_price,
        )
        return result


    def place_market_order(self, symbol: str, side: str, quantity: Decimal) -> OrderResult:
        """Immediate fill at best available price."""
        return self._place_order({
            "symbol":   symbol,
            "side":     side,
            "type":     "MARKET",
            "quantity": str(quantity),
        })


    def place_limit_order(
        self, symbol: str, side: str, quantity: Decimal,
        price: Decimal, time_in_force: str = "GTC",
    ) -> OrderResult:
        return self._place_order({
            "symbol":      symbol,
            "side":        side,
            "type":        "LIMIT",
            "quantity":    str(quantity),
            "price":       str(price),
            "timeInForce": time_in_force,
        })


    def place_stop_market_order(
        self, symbol: str, side: str, quantity: Decimal, stop_price: Decimal,
    ) -> OrderResult:
        """Market order that fires when stopPrice is reached."""
        return self._place_order({
            "symbol":    symbol,
            "side":      side,
            "type":      "STOP_MARKET",
            "quantity":  str(quantity),
            "stopPrice": str(stop_price),
        })


    def place_stop_limit_order(
        self, symbol: str, side: str, quantity: Decimal,
        stop_price: Decimal, limit_price: Decimal, time_in_force: str = "GTC",
    ) -> OrderResult:
        return self._place_order({
            "symbol":      symbol,
            "side":        side,
            "type":        "STOP",           
            "quantity":    str(quantity),
            "stopPrice":   str(stop_price),
            "price":       str(limit_price),
            "timeInForce": time_in_force,
        })


    def place_oco_order(
        self,
        symbol:          str,
        side:            str,          
        quantity:        Decimal,
        take_profit_price: Decimal,    
        stop_loss_price:   Decimal,    
    ) -> OCOResult:
        logger.info(
            "PLACING OCO | symbol=%s | side=%s | qty=%s | TP=%s | SL=%s",
            symbol, side, quantity, take_profit_price, stop_loss_price,
        )

        tp_result = self.place_limit_order(
            symbol=symbol, side=side,
            quantity=quantity, price=take_profit_price,
        )

        try:
            sl_result = self.place_stop_market_order(
                symbol=symbol, side=side,
                quantity=quantity, stop_price=stop_loss_price,
            )
        except BinanceClientError as exc:
            logger.error(
                "OCO stop-loss leg failed after take-profit was placed | "
                "TP orderId=%s | SL error=%s", tp_result.order_id, exc,
            )
            raise

        logger.info(
            "OCO SUCCESS | TP orderId=%s | SL orderId=%s",
            tp_result.order_id, sl_result.order_id,
        )
        return OCOResult(take_profit=tp_result, stop_loss=sl_result)


    def place_twap_order(
        self,
        symbol:       str,
        side:         str,
        total_qty:    Decimal,
        slices:       int,
        interval_sec: int,
        progress_cb=None,   
    ) -> TWAPResult:

        if slices < 2:
            raise ValueError("TWAP requires at least 2 slices.")
        if interval_sec < 1:
            raise ValueError("Interval must be at least 1 second.")

        slice_qty   = (total_qty / slices).quantize(Decimal("0.001"), rounding=ROUND_DOWN)
        remainder   = total_qty - slice_qty * slices
        result      = TWAPResult(
            symbol=symbol, side=side, total_qty=total_qty,
            slices=slices, interval_sec=interval_sec,
        )

        logger.info(
            "TWAP START | symbol=%s | side=%s | total_qty=%s | slices=%s | "
            "slice_qty=%s | interval=%ss",
            symbol, side, total_qty, slices, slice_qty, interval_sec,
        )

        for i in range(1, slices + 1):
            qty = slice_qty + (remainder if i == slices else Decimal("0"))
            logger.info("TWAP slice %d/%d | qty=%s", i, slices, qty)
            try:
                order = self.place_market_order(symbol=symbol, side=side, quantity=qty)
                time.sleep(1)  
                order = self._fetch_order_status(symbol, order.order_id)  
                result.orders.append(order)
                if progress_cb:
                    progress_cb(i, slices, order)
            except BinanceClientError as exc:
                result.failed_slices += 1
                logger.error("TWAP slice %d failed | %s", i, exc)

            if i < slices:
                logger.debug("TWAP sleeping %ss before next slice", interval_sec)
                time.sleep(interval_sec)

        logger.info(
            "TWAP COMPLETE | filled_qty=%s | %s",
            result.filled_qty, result.success_rate,
        )
        return result


    def _fetch_order_status(self, symbol: str, order_id: int) -> OrderResult:
        response = self.client._request(
            "GET", ORDER_ENDPOINT,
            params={"symbol": symbol, "orderId": order_id},
        )
        return OrderResult.from_api_response(response)

    def place_grid_order(
        self,
        symbol:        str,
        lower_price:   Decimal,
        upper_price:   Decimal,
        levels:        int,
        qty_per_grid:  Decimal,
        current_price: Optional[Decimal] = None,
    ) -> GridResult:
        if levels < 2:
            raise ValueError("Grid requires at least 2 levels.")
        if lower_price >= upper_price:
            raise ValueError("lower_price must be less than upper_price.")

        if current_price is None:
            ticker = self.client._request(
                "GET", "/fapi/v1/ticker/price",
                params={"symbol": symbol}, signed=False,
            )
            current_price = Decimal(ticker["price"])
            logger.info("GRID fetched current price | symbol=%s | price=%s", symbol, current_price)

        step = (upper_price - lower_price) / (levels - 1)
        grid_prices = [
            (lower_price + step * i).quantize(Decimal("0.01"))
            for i in range(levels)
        ]

        result = GridResult(
            symbol=symbol, lower_price=lower_price, upper_price=upper_price,
            levels=levels, qty_per_grid=qty_per_grid,
        )

        logger.info(
            "GRID START | symbol=%s | range=[%s, %s] | levels=%s | "
            "step=%s | qty_per_level=%s | mid=%s",
            symbol, lower_price, upper_price, levels, step, qty_per_grid, current_price,
        )

        for price in grid_prices:
            if price >= current_price:
                try:
                    order = self.place_limit_order(
                        symbol=symbol, side="SELL",
                        quantity=qty_per_grid, price=price,
                    )
                    result.sell_orders.append(order)
                    logger.info("GRID SELL placed | price=%s | orderId=%s", price, order.order_id)
                except BinanceClientError as exc:
                    logger.error("GRID SELL failed | price=%s | %s", price, exc)
            else:
                try:
                    order = self.place_limit_order(
                        symbol=symbol, side="BUY",
                        quantity=qty_per_grid, price=price,
                    )
                    result.buy_orders.append(order)
                    logger.info("GRID BUY placed | price=%s | orderId=%s", price, order.order_id)
                except BinanceClientError as exc:
                    logger.error("GRID BUY failed | price=%s | %s", price, exc)

        logger.info(
            "GRID COMPLETE | buy_orders=%d | sell_orders=%d | total=%d",
            len(result.buy_orders), len(result.sell_orders), result.total_orders,
        )
        return result
