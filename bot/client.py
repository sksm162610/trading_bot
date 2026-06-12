
from __future__ import annotations

import hashlib
import hmac
import time
import urllib.parse
from typing import Any
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bot.logging_config import setup_logging

logger = setup_logging()

TESTNET_BASE_URL = "https://testnet.binancefuture.com"
REQUEST_TIMEOUT  = 10   # seconds
MAX_RETRIES      = 3    # retries on network errors only


class BinanceClientError(Exception):

    def __init__(self, message: str, code: int | None = None, http_status: int | None = None):
        super().__init__(message)
        self.code        = code
        self.http_status = http_status


class BinanceFuturesClient:

    def __init__(self, api_key: str, api_secret: str, base_url: str = TESTNET_BASE_URL):
        self.api_key    = api_key
        self.api_secret = api_secret.encode()  # bytes for hmac
        self.base_url   = base_url.rstrip("/")

        self._session = requests.Session()
        retry = Retry(
            total=MAX_RETRIES,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"],   
        )
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("https://", adapter)
        self._session.headers.update({
            "X-MBX-APIKEY": self.api_key,
            "Content-Type": "application/x-www-form-urlencoded",
        })
        logger.debug("BinanceFuturesClient initialised | base_url=%s", self.base_url)

    

    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        params["timestamp"]  = int(time.time() * 1000)
        params["recvWindow"] = 10000
        query_string = urllib.parse.urlencode(params)
        signature = hmac.new(
            self.api_secret,
            query_string.encode(),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = signature
        return params


    def _request(
        self,
        method: str,
        path:   str,
        params: dict[str, Any] | None = None,
        signed: bool = True,
    ) -> Any:
        params = params or {}
        if signed:
            params = self._sign(params)

        url = f"{self.base_url}{path}"

        log_params = {k: v for k, v in params.items() if k not in ("signature", "timestamp")}
        logger.debug("REQUEST  %s %s | params=%s", method, path, log_params)

        try:
            if method == "GET":
                response = self._session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            elif method == "POST":
                response = self._session.post(url, data=params, timeout=REQUEST_TIMEOUT)
            elif method == "DELETE":
                response = self._session.delete(url, params=params, timeout=REQUEST_TIMEOUT)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

        except requests.exceptions.ConnectionError as exc:
            logger.error("NETWORK ERROR | %s %s | %s", method, path, exc)
            raise BinanceClientError(
                f"Network error — could not reach {self.base_url}. "
                "Check your internet connection."
            ) from exc

        except requests.exceptions.Timeout as exc:
            logger.error("TIMEOUT | %s %s | timeout=%ss", method, path, REQUEST_TIMEOUT)
            raise BinanceClientError(
                f"Request timed out after {REQUEST_TIMEOUT}s. "
                "Binance testnet may be slow — please retry."
            ) from exc

        logger.debug(
            "RESPONSE %s %s | status=%s | body=%s",
            method, path, response.status_code, response.text[:500],
        )

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "unknown")
            logger.warning("RATE LIMITED | Retry-After=%s", retry_after)
            raise BinanceClientError(
                f"Rate limit hit. Please wait {retry_after}s before retrying.",
                http_status=429,
            )

        if response.status_code == 418:
            logger.error("IP BANNED | Binance has temporarily banned this IP for repeated rate limit violations.")
            raise BinanceClientError(
                "Your IP has been temporarily banned by Binance for excessive requests. "
                "Wait a few minutes before retrying.",
                http_status=418,
            )

        if not response.ok:
            try:
                err  = response.json()
                code = err.get("code")
                msg  = err.get("msg", response.text)
            except Exception:
                code = None
                msg  = response.text
            logger.error(
                "API ERROR | http=%s | code=%s | msg=%s",
                response.status_code, code, msg,
            )
            raise BinanceClientError(msg, code=code, http_status=response.status_code)

        return response.json()


    def get_account_info(self) -> dict:
        return self._request("GET", "/fapi/v2/account")

    def get_exchange_info(self) -> dict:
        return self._request("GET", "/fapi/v1/exchangeInfo", signed=False)

    def ping(self) -> bool:
        try:
            self._request("GET", "/fapi/v1/ping", signed=False)
            return True
        except BinanceClientError:
            return False
