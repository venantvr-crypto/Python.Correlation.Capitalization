# File: data_fetcher.py
import queue
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import ccxt
import pandas as pd
from pycoingecko import CoinGeckoAPI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from logger import logger
from service_bus import ServiceBus


class DataFetcher(threading.Thread):
    """Récupère les données de marché dans son propre thread."""

    def __init__(self, session_guid: Optional[str] = None, service_bus: Optional[ServiceBus] = None):
        super().__init__()
        self.cg = CoinGeckoAPI()
        self.binance = ccxt.binance()
        self.binance.load_markets()
        self.symbols = self.binance.symbols
        self.session_guid = session_guid
        self.service_bus = service_bus
        self.work_queue = queue.Queue()
        self._running = True

    def run(self):
        """Boucle d'exécution du thread."""
        logger.info("Thread DataFetcher démarré.")
        while self._running:
            task = self.work_queue.get()
            if task is None:
                self._running = False
                break
            method, args, kwargs = task
            try:
                method(*args, **kwargs)
            except Exception as e:
                logger.error(f"Erreur d'exécution de la tâche de récupération de données: {e}")
            finally:
                self.work_queue.task_done()
        logger.info("Thread DataFetcher arrêté.")

    def stop(self):
        """Arrête le thread en toute sécurité."""
        self.work_queue.put(None)
        self.join()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((ccxt.NetworkError, ccxt.ExchangeError)),
        before_sleep=lambda retry_state: logger.warning(
            f"Réessai pour {retry_state.fn.__name__}: tentative {retry_state.attempt_number}")
    )
    def _fetch_top_coins_task(self, n: int = 200) -> None:
        """Tâche interne pour récupérer les n tokens avec la plus grande capitalisation."""
        coins = []
        pages = (n + 99) // 100
        for page in range(1, pages + 1):
            try:
                new_coins = self.cg.get_coins_markets(vs_currency='usd', per_page=100, page=page)
                coins.extend(new_coins)
            except Exception as e:
                logger.error(f"Erreur lors de la récupération des coins, page {page}: {e}")
                continue

        # Nouvelle logique : publier un événement pour chaque coin.
        for coin in coins[:n]:
            payload = {'coin': coin, 'session_guid': self.session_guid}
            self.service_bus.publish("SingleCoinFetched", payload)

        # Et un événement pour signaler la fin du fetch global.
        self.service_bus.publish("TopCoinsFetchingCompleted",
                                 {'coins_count': len(coins[:n]), 'session_guid': self.session_guid})

    def fetch_top_coins(self, n: int = 200) -> None:
        self.work_queue.put((self._fetch_top_coins_task, (n,), {}))

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((ccxt.NetworkError, ccxt.ExchangeError)),
        before_sleep=lambda retry_state: logger.warning(
            f"Réessai pour {retry_state.fn.__name__}: tentative {retry_state.attempt_number}")
    )
    def _fetch_historical_prices_task(self, coin_id_symbol: Tuple[str, str], weeks: int = 52) -> Optional[pd.Series]:
        """Tâche interne pour récupérer les prix OHLC via Binance."""
        coin_id = coin_id_symbol[0]
        coin_symbol = coin_id_symbol[1]
        symbol = f"{coin_symbol.upper()}/USDT"
        if symbol not in self.symbols:
            logger.warning(f"Symbole {symbol} introuvable sur Binance.")
            if self.service_bus:
                self.service_bus.publish("HistoricalPricesFetched", {'coin_id_symbol': coin_id_symbol, 'prices': None})
            return None
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
        return prices_df['close']

    def fetch_historical_prices(self, coin_id_symbol: Tuple[str, str], weeks: int = 52) -> None:
        self.work_queue.put((self._fetch_historical_prices_task, (coin_id_symbol, weeks), {}))
