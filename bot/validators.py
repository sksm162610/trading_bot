from __future__ import annotations
from decimal import Decimal, InvalidOperation
from typing import Optional


VALID_SIDES = {"BUY", "SELL"}
VALID_ORDER_TYPES = {"MARKET", "LIMIT", "STOP_MARKET", "STOP_LIMIT", "OCO", "TWAP", "GRID"}
KNOWN_QUOTE_CURRENCIES = {"USDT", "BUSD"}


def validate_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if not symbol:
        raise ValueError("Symbol cannot be empty.")
    if not symbol.isalnum():
        raise ValueError(f"Symbol '{symbol}' contains invalid characters (alphanumeric only, e.g. BTCUSDT).")
    if not any(symbol.endswith(q) for q in KNOWN_QUOTE_CURRENCIES):
        raise ValueError(
            f"Symbol '{symbol}' must end with a recognised quote currency "
            f"({', '.join(sorted(KNOWN_QUOTE_CURRENCIES))}). Example: BTCUSDT"
        )
    return symbol


def validate_side(side: str) -> str:
    side = side.strip().upper()
    if side not in VALID_SIDES:
        raise ValueError(f"Side must be BUY or SELL. Got: '{side}'")
    return side


def validate_order_type(order_type: str) -> str:
    order_type = order_type.strip().upper()
    if order_type not in VALID_ORDER_TYPES:
        raise ValueError(
            f"Order type must be one of: {', '.join(sorted(VALID_ORDER_TYPES))}. Got: '{order_type}'"
        )
    return order_type


def validate_quantity(quantity: str | float) -> Decimal:
    try:
        qty = Decimal(str(quantity))
    except InvalidOperation:
        raise ValueError(f"Quantity '{quantity}' is not a valid number.")
    if qty <= 0:
        raise ValueError(f"Quantity must be > 0. Got: {qty}")
    return qty


def validate_price(price: str | float | None, order_type: str) -> Optional[Decimal]:
    if order_type in {"MARKET", "STOP_MARKET", "TWAP", "GRID"}:
        return None
    if order_type == "OCO":
        return None   
    if order_type in {"LIMIT", "STOP_LIMIT"}:
        if price is None:
            raise ValueError(f"--price is required for {order_type} orders.")
        try:
            p = Decimal(str(price))
        except InvalidOperation:
            raise ValueError(f"Price '{price}' is not a valid number.")
        if p <= 0:
            raise ValueError(f"Price must be > 0. Got: {p}")
        return p
    return None


def validate_stop_price(stop_price: str | float | None, order_type: str) -> Optional[Decimal]:
    if order_type not in {"STOP_MARKET", "STOP_LIMIT"}:
        return None
    if stop_price is None:
        raise ValueError(f"--stop-price is required for {order_type} orders.")
    try:
        sp = Decimal(str(stop_price))
    except InvalidOperation:
        raise ValueError(f"Stop price '{stop_price}' is not a valid number.")
    if sp <= 0:
        raise ValueError(f"Stop price must be > 0. Got: {sp}")
    return sp


def validate_positive_decimal(value: str | float | None, name: str) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"{name} '{value}' is not a valid number.")
    if d <= 0:
        raise ValueError(f"{name} must be > 0. Got: {d}")
    return d


def validate_positive_int(value: int | None, name: str, minimum: int = 1) -> Optional[int]:
    if value is None:
        return None
    if not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}. Got: {value}")
    return value
