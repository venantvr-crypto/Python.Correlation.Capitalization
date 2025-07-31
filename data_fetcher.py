from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, Dict

import ccxt
import pandas as pd
from pycoingecko import CoinGeckoAPI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from logger import logger
from service_bus import ServiceBus


class DataFetcher:
    """Récupère les données de marché, réactif aux événements du ServiceBus."""

    def __init__(self, session_guid: Optional[str] = None, service_bus: Optional[ServiceBus] = None):
        self.cg = CoinGeckoAPI()
        self.binance = ccxt.binance()
        self.binance.load_markets()
        self.symbols = self.binance.symbols
        self.session_guid = session_guid
        self.service_bus = service_bus

        if self.service_bus:
            self.service_bus.subscribe("FetchTopCoinsRequested", self._handle_fetch_top_coins_requested)
            self.service_bus.subscribe("FetchHistoricalPricesRequested", self._handle_fetch_historical_prices_requested)

    def _handle_fetch_top_coins_requested(self, payload: Dict):
        n = payload.get('n', 200)
        self._fetch_top_coins_task(n)

    def _handle_fetch_historical_prices_requested(self, payload: Dict):
        coin_id_symbol = payload.get('coin_id_symbol')
        weeks = payload.get('weeks', 52)
        self._fetch_historical_prices_task(coin_id_symbol, weeks)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((ccxt.NetworkError, ccxt.ExchangeError)),
        before_sleep=lambda retry_state: logger.warning(
            f"Réessai pour {retry_state.fn.__name__}: tentative {retry_state.attempt_number}")
    )
    def _fetch_top_coins_task(self, n: int = 200) -> None:
        coins = []
        pages = (n + 99) // 100
        for page in range(1, pages + 1):
            try:
                new_coins = self.cg.get_coins_markets(vs_currency='usd', per_page=100, page=page)
                coins.extend(new_coins)
            except Exception as e:
                logger.error(f"Erreur lors de la récupération des coins, page {page}: {e}")
                continue
        if self.service_bus:
            payload = {'coins': coins[:n], 'session_guid': self.session_guid}
            self.service_bus.publish("TopCoinsFetched", payload)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((ccxt.NetworkError, ccxt.ExchangeError)),
        before_sleep=lambda retry_state: logger.warning(
            f"Réessai pour {retry_state.fn.__name__}: tentative {retry_state.attempt_number}")
    )
    def _fetch_historical_prices_task(self, coin_id_symbol: Tuple[str, str], weeks: int = 52) -> None:
        coin_id, coin_symbol = coin_id_symbol
        symbol = f"{coin_symbol.upper()}/USDT"
        if symbol not in self.symbols:
            logger.warning(f"Symbole {symbol} introuvable sur Binance.")
            if self.service_bus:
                self.service_bus.publish("HistoricalPricesFetched",
                                         {'coin_id_symbol': coin_id_symbol, 'prices_df': None})
            return
        days = weeks * 7
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=days)
        timeframe = '1d'
        since = int(start_date.timestamp() * 1000)
        ohlc = self.binance.fetch_ohlcv(symbol, timeframe, since=since, limit=days)
        prices_df = pd.DataFrame(
            ohlc,
            columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'],
            index=[datetime.fromtimestamp(x[0] / 1000, tz=timezone.utc) for x in ohlc]
        )
        if self.service_bus:
            payload = {'coin_id_symbol': coin_id_symbol, 'prices_df': prices_df, 'session_guid': self.session_guid}
            self.service_bus.publish("HistoricalPricesFetched", payload)
