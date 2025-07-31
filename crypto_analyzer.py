import time
from datetime import datetime, timezone
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from data_fetcher import DataFetcher
from database_manager import DatabaseManager
from logger import logger
from rsi_calculator import RSICalculator


class CryptoAnalyzer:
    """Orchestre l'analyse des corrélations RSI pour le scalping."""

    def __init__(self, weeks: int = 50, top_n_coins: int = 200, correlation_threshold: float = 0.7,
                 rsi_period: int = 14, session_guid: Optional[str] = None):
        self.weeks = weeks
        self.top_n_coins = top_n_coins
        self.correlation_threshold = correlation_threshold
        self.rsi_period = rsi_period
        self.db_manager = DatabaseManager()
        self.data_fetcher = DataFetcher(session_guid)
        self.rsi_calculator = RSICalculator()
        self.session_guid = session_guid

    def _analyze_coin(self, coin_id_symbol: Tuple[str, str], btc_prices: pd.Series, btc_rsi: pd.Series,
                      market_caps: dict,
                      low_cap_threshold: float, run_timestamp: str) -> Optional[dict]:
        """Analyse un token individuel."""

        coin_id = coin_id_symbol[0]
        coin_symbol = coin_id_symbol[1]

        try:
            prices: Optional[pd.Series] = self.data_fetcher.fetch_historical_prices(coin_id_symbol, self.weeks,
                                                                                    self.db_manager)
            if prices is None:
                return None

            # Aligner les index avec BTC
            common_index = btc_prices.index.intersection(prices.index)
            if len(common_index) < self.rsi_period:
                logger.warning(f"Index commun insuffisant pour {coin_symbol} ({len(common_index)} dates).")
                return None
            # aligned_btc_prices = btc_prices.loc[common_index]
            aligned_prices = prices.loc[common_index]

            coin_rsi: Optional[pd.Series] = self.rsi_calculator.calculate(aligned_prices, self.rsi_period)
            if coin_rsi is None:
                logger.warning(f"RSI non calculé pour {coin_symbol}.")
                return None
            self.db_manager.save_rsi(coin_id_symbol, coin_rsi, self.session_guid)

            # Intersection des index pour RSI
            common_index_rsi = btc_rsi.index.intersection(coin_rsi.index)
            if len(common_index_rsi) < self.rsi_period:
                logger.warning(f"Index commun RSI insuffisant pour {coin_symbol} ({len(common_index_rsi)} dates).")
                return None
            aligned_btc_rsi = btc_rsi.loc[common_index_rsi]
            aligned_coin_rsi = coin_rsi.loc[common_index_rsi]

            correlation = aligned_coin_rsi.corr(aligned_btc_rsi)
            if pd.isna(correlation):
                logger.warning(f"Corrélation non calculée pour {coin_symbol}.")
                return None

            market_cap = market_caps.get(coin_symbol, 0)
            low_cap_quartile = market_cap <= low_cap_threshold
            if abs(correlation) >= self.correlation_threshold and low_cap_quartile:
                self.db_manager.save_correlation(coin_id_symbol, run_timestamp, correlation, market_cap,
                                                 low_cap_quartile,
                                                 self.session_guid)
                return {
                    'coin_id': coin_id,
                    'coin_symbol': coin_symbol,
                    'correlation': correlation,
                    'market_cap': market_cap
                }
            return None
        except Exception as e:
            logger.warning(f"Erreur pour {coin_symbol}: {e}")
            return None

    # def _retry_failed_coins(self, failed_coins: List[Tuple[str, str]], btc_prices: pd.Series, btc_rsi: pd.Series,
    #                         market_caps: dict, low_cap_threshold: float, run_timestamp: str) -> List[dict]:
    #     """Tente de réanalyser les coins échoués."""
    #     results = []
    #     max_retries = 2
    #     retry_count = 0
    #     while failed_coins and retry_count < max_retries:
    #         logger.info(f"Reprise pour {len(failed_coins)} coins échoués (tentative {retry_count + 1})...")
    #         retry_coins = failed_coins.copy()
    #         failed_coins.clear()
    #         for (coin_id, coin_symbol) in retry_coins:
    #             logger.info(f"Réessai pour {coin_id}...")
    #             result = self._analyze_coin((coin_id, coin_symbol), btc_prices, btc_rsi, market_caps, low_cap_threshold,
    #                                         run_timestamp)
    #             if result:
    #                 results.append(result)
    #             else:
    #                 failed_coins.append((coin_id, coin_symbol))
    #             time.sleep(0.5)
    #         retry_count += 1
    #     return results

    def run(self) -> None:
        """Exécute l'analyse complète."""
        logger.info("Récupération des top coins...")
        try:
            coins = self.data_fetcher.fetch_top_coins(self.top_n_coins, self.db_manager)
            # id, symbol, cap...
        except Exception as e:
            logger.error(f"Erreur lors de la récupération des top coins: {e}")
            return
        coin_ids_symbols = [(coin[0], coin[1]) for coin in coins]
        # id, symbol...
        # coin_ids = [coin[1] for coin in coins]
        market_caps = {coin[1]: coin[2] for coin in coins}

        market_caps_values = [cap for _, _, cap in coins]
        low_cap_threshold = np.percentile(market_caps_values, 25) if market_caps_values else float('inf')
        logger.info(f"Seuil de faible capitalisation (25e percentile): ${low_cap_threshold:,.2f}")

        logger.info("Récupération des données pour Bitcoin...")
        btc_prices: Optional[pd.Series] = self.data_fetcher.fetch_historical_prices(
            ('bitcoin', 'btc'),
            self.weeks,
            self.db_manager)
        if btc_prices is None:
            logger.error("Impossible de récupérer les données de BTC.")
            self.db_manager.close()
            return

        btc_rsi: Optional[pd.Series] = self.rsi_calculator.calculate(btc_prices, self.rsi_period)
        if btc_rsi is None:
            logger.error("Erreur lors du calcul du RSI pour BTC.")
            self.db_manager.close()
            return
        self.db_manager.save_rsi(('bitcoin', 'btc'), btc_rsi, self.session_guid)

        run_timestamp = datetime.now(timezone.utc).isoformat()
        results = []
        # failed_coins = []
        for coin_id, coin_symbol in coin_ids_symbols:
            if coin_symbol == 'btc':
                continue
            result = self._analyze_coin((coin_id, coin_symbol), btc_prices, btc_rsi, market_caps, low_cap_threshold,
                                        run_timestamp)
            if result:
                results.append(result)
            # else:
            # failed_coins.append((coin_id, coin_symbol))
            time.sleep(0.5)

        # results.extend(
        # self._retry_failed_coins(failed_coins, btc_prices, btc_rsi, market_caps, low_cap_threshold, run_timestamp))

        results = sorted(results, key=lambda x: (-abs(x['correlation']), x['market_cap']))

        logger.info(f"\nTokens à faible capitalisation avec forte corrélation RSI avec BTC ({self.weeks} semaines) :")
        for result in results:
            logger.info(
                f"Coin: {result['coin_id']}/{result['coin_symbol']}, Correlation RSI: {result['correlation']:.3f}, "
                f"Market Cap: ${result['market_cap']:,.2f}")

        logger.info("\nRésumé de l'historique des corrélations :")
        correlations = self.db_manager.get_correlations(session_guid=self.session_guid)
        for row in correlations:
            logger.info(
                f"Run: {row[2]}, Coin: {row[0]}/{row[1]}, Correlation: {row[3]:.3f}, Market Cap: ${row[4]:,.2f}, "
                f"Low Cap Quartile: {row[5]}")

        self.db_manager.close()
