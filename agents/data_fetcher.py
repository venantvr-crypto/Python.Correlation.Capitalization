import logging
from datetime import UTC, datetime, timedelta

import ccxt
import pandas as pd
import requests
from pycoingecko import CoinGeckoAPI

# noinspection PyPackageRequirements
from python_pubsub_client import QueueWorkerThread, ServiceBus
from python_threadsafe_logger import sqlite_business_logger
from tenacity import before_sleep_log, retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from events import (
    AnalysisConfigurationProvided,
    CoinProcessingFailed,
    CoinRef,
    FetchHistoricalPricesRequested,
    FetchPrecisionDataRequested,
    FetchTopCoinsRequested,
    HistoricalPricesFetched,
    PrecisionDataFetched,
    TopCoinsFetched,
)
from logger import logger
from serialization import frame_to_json

QUOTE_ASSET = "USDC"
COINGECKO_PAGE_SIZE = 100
OHLCV_PAGE_SIZE = 1000
OHLCV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]

_log_retry = before_sleep_log(logger, logging.WARNING)
_retry_coingecko = retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=5, max=30),
    retry=retry_if_exception_type(requests.exceptions.RequestException),
    before_sleep=_log_retry,
    reraise=True,
)
_retry_binance = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=5, max=20),
    retry=retry_if_exception_type(ccxt.NetworkError),
    before_sleep=_log_retry,
    reraise=True,
)


def _market_filter(market_info: dict, filter_type: str) -> dict | None:
    """Returns the Binance exchange filter of the given type for a market, if any."""
    filters = market_info.get("info", {}).get("filters", [])
    return next((f for f in filters if f.get("filterType") == filter_type), None)


class DataFetcher(QueueWorkerThread):
    """Fetches market data with robust error handling and API rate limiting."""

    def __init__(self, service_bus: ServiceBus | None = None):
        super().__init__(service_bus=service_bus, name="DataFetcher")
        self.cg = CoinGeckoAPI()

        # Enable CCXT rate limiter to respect Binance API limits
        self.binance = ccxt.binance(
            {
                "enableRateLimit": True,
                "timeout": 30000,  # 30 seconds in milliseconds
            }
        )

        self.session_guid: str | None = None

    def setup_event_subscriptions(self) -> None:
        self.service_bus.subscribe("AnalysisConfigurationProvided", self._handle_configuration_provided)
        self.service_bus.subscribe("FetchTopCoinsRequested", self._handle_fetch_top_coins_requested)
        self.service_bus.subscribe("FetchHistoricalPricesRequested", self._handle_fetch_historical_prices_requested)
        self.service_bus.subscribe("FetchPrecisionDataRequested", self._handle_fetch_precision_data_requested)

    def _publish(self, event) -> None:

        if self.service_bus is not None:
            self.service_bus.publish(type(event).__name__, event, self.__class__.__name__)

    def _log_error(self, error_msg: str) -> None:
        logger.error(error_msg)
        self.log_message(error_msg)

    # --- Top coins (CoinGecko) ---

    @_retry_coingecko
    def _fetch_coins_page(self, page: int) -> list[dict]:
        return self.cg.get_coins_markets(vs_currency="usd", per_page=COINGECKO_PAGE_SIZE, page=page, timeout=30)

    def _fetch_top_coins_task(self, n: int) -> None:
        """Fetches the top N coins, retrying each page individually on network errors."""
        coins: list[dict] = []
        pages = -(-n // COINGECKO_PAGE_SIZE)
        logger.info(f"Starting to fetch {n} coins across {pages} pages from CoinGecko...")

        try:
            for page in range(1, pages + 1):
                logger.info(f"Fetching page {page}/{pages} from CoinGecko...")
                new_coins = self._fetch_coins_page(page)

                if not new_coins:
                    logger.warning(f"Page {page} from CoinGecko returned no data, stopping collection.")
                    break

                coins.extend(new_coins)
        except Exception as e:
            # Keep the pages already collected: a partial list is still usable.
            self._log_error(f"Stopping top coins collection after persistent error: {e}")

        logger.info(f"CoinGecko fetch completed. {len(coins)} coins found.")
        self._publish(TopCoinsFetched(coins=coins[:n]))

    # --- Historical prices (Binance) ---

    @_retry_binance
    def _fetch_ohlcv_page(self, symbol: str, timeframe: str, since: int) -> list[list]:
        return self.binance.fetch_ohlcv(symbol, timeframe, since=since, limit=OHLCV_PAGE_SIZE)

    @_retry_binance
    def _load_markets(self) -> dict:
        return self.binance.load_markets()

    def _fetch_ohlcv(self, symbol: str, timeframe: str, since: int) -> list[list]:
        """Fetches all candles from `since` to now, paginating past the exchange's per-request limit."""
        candles: list[list] = []

        while True:
            page = self._fetch_ohlcv_page(symbol, timeframe, since)
            # Guard against exchanges returning overlapping candles.
            page = [c for c in page if not candles or c[0] > candles[-1][0]]
            candles.extend(page)

            if len(page) < OHLCV_PAGE_SIZE:
                return candles

            since = candles[-1][0] + 1

    def _fetch_historical_prices_task(self, coin_id_symbol: CoinRef, weeks: int, timeframe: str) -> None:
        symbol = f"{coin_id_symbol[1].upper()}/{QUOTE_ASSET}"
        logger.info(f"Fetching prices for {symbol} on timeframe {timeframe}...")

        try:
            prices_json = None

            if not self.binance.markets:
                self._load_markets()

            if symbol in self.binance.markets:
                since = int((datetime.now(UTC) - timedelta(weeks=weeks)).timestamp() * 1000)
                ohlcv = self._fetch_ohlcv(symbol, timeframe, since)

                if ohlcv:
                    prices_df = pd.DataFrame(ohlcv, columns=OHLCV_COLUMNS)
                    prices_df["timestamp"] = pd.to_datetime(prices_df["timestamp"], unit="ms", utc=True)
                    prices_json = frame_to_json(prices_df.set_index("timestamp"))

            sqlite_business_logger.log(self.__class__.__name__, f"HistoricalPricesFetched pour {coin_id_symbol}")
            self._publish(HistoricalPricesFetched(coin_id_symbol=coin_id_symbol, prices_df_json=prices_json, timeframe=timeframe))
        except Exception as e:
            self._log_error(f"Final failure fetching prices for {symbol}: {e}")
            # Publish failure event so processing can continue
            self._publish(CoinProcessingFailed(coin_id_symbol=coin_id_symbol, timeframe=timeframe))

    # --- Precision data (Binance) ---

    def _fetch_precision_data_task(self) -> None:
        precision_data: list[dict] = []

        try:
            logger.info("Fetching precision data from all Binance markets...")

            for market_info in self._load_markets().values():
                if not market_info.get("active"):
                    continue

                lot_size = _market_filter(market_info, "LOT_SIZE")
                price = _market_filter(market_info, "PRICE_FILTER")
                notional = _market_filter(market_info, "NOTIONAL")

                if lot_size and price and notional:
                    precision_data.append(
                        {
                            "symbol": market_info.get("symbol"),
                            "quote_asset": market_info.get("quote"),
                            "base_asset": market_info.get("base"),
                            "status": market_info.get("active"),
                            "base_asset_precision": market_info.get("info", {}).get("baseAssetPrecision"),
                            "step_size": lot_size.get("stepSize"),
                            "min_qty": lot_size.get("minQty"),
                            "tick_size": price.get("tickSize"),
                            "min_notional": notional.get("minNotional"),
                        }
                    )

            logger.info(f"{len(precision_data)} active markets found on Binance.")
            sqlite_business_logger.log(self.__class__.__name__, f"PrecisionDataFetched pour {len(precision_data)} paires")
        except Exception as e:
            self._log_error(f"Failed to fetch precision data from Binance: {e}")
            # An empty list lets the orchestrator stop cleanly instead of waiting forever.
            precision_data = []

        self._publish(PrecisionDataFetched(precision_data=precision_data))

    # --- Event handlers ---

    def _handle_configuration_provided(self, event: AnalysisConfigurationProvided) -> None:
        self.session_guid = event.session_guid
        logger.info(f"DataFetcher received configuration for session {self.session_guid}.")

    def _handle_fetch_top_coins_requested(self, event: FetchTopCoinsRequested) -> None:
        self.add_task("_fetch_top_coins_task", event.n)

    def _handle_fetch_historical_prices_requested(self, event: FetchHistoricalPricesRequested) -> None:
        self.add_task("_fetch_historical_prices_task", event.coin_id_symbol, event.weeks, event.timeframe)

    def _handle_fetch_precision_data_requested(self, _event: FetchPrecisionDataRequested) -> None:
        self.add_task("_fetch_precision_data_task")
