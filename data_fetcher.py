from datetime import datetime, timedelta, timezone
from typing import Optional, List, Tuple

import ccxt
import pandas as pd
from pycoingecko import CoinGeckoAPI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from database_manager import DatabaseManager
from logger import logger


class DataFetcher:
    """Récupère les données de marché."""

    def __init__(self, session_guid: Optional[str] = None):
        self.cg = CoinGeckoAPI()
        self.binance = ccxt.binance()
        self.binance.load_markets()
        self.symbols = self.binance.symbols
        self.session_guid = session_guid

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((ccxt.NetworkError, ccxt.ExchangeError)),
        before_sleep=lambda retry_state: logger.warning(
            f"Réessai pour {retry_state.fn.__name__}: tentative {retry_state.attempt_number}")
    )
    def fetch_top_coins(self, n: int = 200, db_manager: Optional[DatabaseManager] = None) -> List[
        Tuple[str, str, float]]:
        """Récupère les n tokens avec la plus grande capitalisation."""
        coins = []
        pages = (n + 99) // 100
        for page in range(1, pages + 1):
            try:
                new_coins = self.cg.get_coins_markets(vs_currency='usd', per_page=100, page=page)
                coins.extend(new_coins)
            except Exception as e:
                logger.error(f"Erreur lors de la récupération des coins, page {page}: {e}")
                continue

        if db_manager:
            for coin in coins:
                db_manager.save_token(coin, self.session_guid)

        return [(coin['id'], coin['symbol'], coin['market_cap']) for coin in coins[:n]]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((ccxt.NetworkError, ccxt.ExchangeError)),
        before_sleep=lambda retry_state: logger.warning(
            f"Réessai pour {retry_state.fn.__name__}: tentative {retry_state.attempt_number}")
    )
    def fetch_historical_prices(self, coin_id_symbol: Tuple[str, str], weeks: int = 52,
                                db_manager: Optional[DatabaseManager] = None) -> \
            Optional[pd.Series]:
        """Récupère les prix OHLC via Binance."""

        coin_id = coin_id_symbol[0]
        coin_symbol = coin_id_symbol[1]

        symbol = f"{coin_symbol.upper()}/USDT"
        if symbol not in self.symbols:
            logger.warning(f"Symbole {symbol} introuvable sur Binance.")
            return None

        days = weeks * 7
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=days)

        if db_manager:
            prices_df = db_manager.get_prices(coin_id_symbol, start_date, self.session_guid)
            if prices_df is not None and len(prices_df) >= days - 10:
                return prices_df['close']

        timeframe = '1d'
        since = int(start_date.timestamp() * 1000)
        ohlc = self.binance.fetch_ohlcv(symbol, timeframe, since=since, limit=days)
        prices_df = pd.DataFrame(
            ohlc,
            columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'],
            index=[datetime.fromtimestamp(x[0] / 1000, tz=timezone.utc) for x in ohlc]
        )
        # Vérifier la cohérence des timestamps
        time_diffs = prices_df.index.to_series().diff().dropna()
        expected_diff = timedelta(days=1)
        if not all((expected_diff - timedelta(hours=1) <= diff <= expected_diff + timedelta(hours=1)) for diff in
                   time_diffs):
            logger.warning(f"Incohérence dans les timestamps OHLC pour {coin_id}.")
        if db_manager:
            db_manager.save_prices(coin_id_symbol, prices_df, self.session_guid)
        return prices_df['close']
