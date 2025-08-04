import queue
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import ccxt
import pandas as pd
from pycoingecko import CoinGeckoAPI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from events import FetchTopCoinsRequested, FetchHistoricalPricesRequested, FetchPrecisionDataRequested
from logger import logger
from service_bus import ServiceBus


class DataFetcher(threading.Thread):
    """Récupère les données de marché dans son propre thread."""

    def __init__(self, session_guid: Optional[str] = None, service_bus: Optional[ServiceBus] = None):
        super().__init__()
        self.cg = CoinGeckoAPI()
        self.binance = ccxt.binance()
        self.session_guid = session_guid
        self.service_bus = service_bus
        self.work_queue = queue.Queue()
        self._running = True

        if self.service_bus:
            self.service_bus.subscribe("FetchTopCoinsRequested", self._handle_fetch_top_coins_requested)
            self.service_bus.subscribe("FetchHistoricalPricesRequested", self._handle_fetch_historical_prices_requested)
            self.service_bus.subscribe("FetchPrecisionDataRequested", self._handle_fetch_precision_data_requested)

    def _handle_fetch_top_coins_requested(self, event: FetchTopCoinsRequested):
        self.work_queue.put(('_fetch_top_coins_task', (event.n, event.session_guid)))

    def _handle_fetch_historical_prices_requested(self, event: FetchHistoricalPricesRequested):
        self.work_queue.put(('_fetch_historical_prices_task', (event.coin_id_symbol, event.weeks, event.session_guid, event.timeframe)))

    def _handle_fetch_precision_data_requested(self, event: FetchPrecisionDataRequested):
        self.work_queue.put(('_fetch_precision_data_task', (event.session_guid,)))

    def run(self):
        logger.info("Thread DataFetcher démarré.")
        while self._running:
            try:
                task = self.work_queue.get(timeout=1)
                if task is None:
                    continue

                method_name, args = task
                method = getattr(self, method_name)
                method(*args)
                self.work_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Erreur d'exécution de la tâche dans DataFetcher: {e}")
        logger.info("Thread DataFetcher arrêté.")

    def stop(self):
        self._running = False
        self.work_queue.put(None)
        self.join()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((ccxt.NetworkError, ccxt.ExchangeError)),
        before_sleep=lambda retry_state: logger.warning(
            f"Réessai pour {retry_state.fn.__name__}: tentative {retry_state.attempt_number}")
    )
    def _fetch_top_coins_task(self, n: int, session_guid: str) -> None:
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
            self.service_bus.publish("TopCoinsFetched", {'coins': coins[:n], 'session_guid': session_guid})

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((ccxt.NetworkError, ccxt.ExchangeError)),
        before_sleep=lambda retry_state: logger.warning(
            f"Réessai pour {retry_state.fn.__name__}: tentative {retry_state.attempt_number}")
    )
    def _fetch_historical_prices_task(self, coin_id_symbol: Tuple[str, str], weeks: int, session_guid: str, timeframe: str) -> None:
        coin_id, coin_symbol = coin_id_symbol
        symbol = f"{coin_symbol.upper()}/USDC"
        if symbol not in self.binance.symbols:
            logger.warning(f"Symbole {symbol} introuvable sur Binance.")
            if self.service_bus:
                self.service_bus.publish("HistoricalPricesFetched",
                                         {'coin_id_symbol': coin_id_symbol, 'prices_df': None,
                                          'session_guid': session_guid, 'timeframe': timeframe})
            return
        days = weeks * 7
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=days)
        since = int(start_date.timestamp() * 1000)
        ohlc = self.binance.fetch_ohlcv(symbol, timeframe, since=since, limit=days)
        prices_df = pd.DataFrame(
            ohlc,
            columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'],
            index=[datetime.fromtimestamp(x[0] / 1000, tz=timezone.utc) for x in ohlc]
        )
        if self.service_bus:
            self.service_bus.publish("HistoricalPricesFetched",
                                     {'coin_id_symbol': coin_id_symbol, 'prices_df': prices_df,
                                      'session_guid': session_guid, 'timeframe': timeframe})

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((ccxt.NetworkError, ccxt.ExchangeError)),
        before_sleep=lambda retry_state: logger.warning(
            f"Réessai pour {retry_state.fn.__name__}: tentative {retry_state.attempt_number}")
    )
    def _fetch_precision_data_task(self, session_guid: str) -> None:
        """Récupère les données de précision pour TOUS les marchés actifs sur Binance."""
        try:
            markets = self.binance.load_markets()
            precision_data = []
            for symbol, market_info in markets.items():
                # MODIFICATION : On ne filtre plus sur 'USDC', on prend tous les marchés actifs.
                if market_info.get('active'):
                    # Chercher les filtres PRICE_FILTER et LOT_SIZE
                    lot_size_filter = next((f for f in market_info['info']['filters']
                                            if f['filterType'] == 'LOT_SIZE'), None)
                    price_filter = next(
                        (f for f in market_info['info']['filters'] if f['filterType'] == 'PRICE_FILTER'), None)
                    notional_filter = next((f for f in market_info['info']['filters']
                                            if f['filterType'] == 'NOTIONAL'), None)

                    if lot_size_filter and price_filter and notional_filter:
                        data = {
                            'symbol': market_info['symbol'],
                            'quote_asset': market_info['quote'],
                            'base_asset': market_info['base'],
                            'status': market_info['active'],
                            'base_asset_precision': market_info['info']['baseAssetPrecision'],
                            'step_size': lot_size_filter['stepSize'],
                            'min_qty': lot_size_filter['minQty'],
                            'tick_size': price_filter['tickSize'],
                            'min_notional': notional_filter['minNotional'],
                        }
                        precision_data.append(data)

            if self.service_bus:
                logger.info(f"Envoi de {len(precision_data)} paires de précision de marché depuis Binance.")
                self.service_bus.publish("PrecisionDataFetched",
                                         {'precision_data': precision_data, 'session_guid': session_guid})
        except Exception as e:
            logger.error(f"Erreur lors de la récupération des données de précision: {e}")
            if self.service_bus:
                self.service_bus.publish("PrecisionDataFetched", {'precision_data': [], 'session_guid': session_guid})
