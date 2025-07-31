import threading
from datetime import datetime, timezone
from typing import Optional, Tuple, List, Dict

import numpy as np
import pandas as pd

from data_fetcher import DataFetcher
from database_manager import DatabaseManager
from logger import logger
from rsi_calculator import RSICalculator
from service_bus import ServiceBus


class CryptoAnalyzer:
    """Orchestre l'analyse des corrélations RSI pour le scalping, piloté par les événements."""

    def __init__(self, weeks: int = 50, top_n_coins: int = 200, correlation_threshold: float = 0.7,
                 rsi_period: int = 14, session_guid: Optional[str] = None):
        self.weeks = weeks
        self.top_n_coins = top_n_coins
        self.correlation_threshold = correlation_threshold
        self.rsi_period = rsi_period
        self.session_guid = session_guid
        self.market_caps: Dict[str, float] = {}
        self.low_cap_threshold: float = float('inf')
        self.btc_prices: Optional[pd.Series] = None
        self.btc_rsi: Optional[pd.Series] = None
        self.results: List[Dict] = []
        self._coins_to_process: List[Tuple[str, str]] = []

        self._results_lock = threading.Lock()

        # Compteur pour suivre les pièces traitées et éviter les blocages
        self._processed_coins_count = 0
        self._total_coins_to_process = 0

        # Événement pour la synchronisation finale
        self._all_processing_completed = threading.Event()

        self.service_bus = ServiceBus()
        self.db_manager = DatabaseManager(service_bus=self.service_bus)
        self.data_fetcher = DataFetcher(session_guid, service_bus=self.service_bus)
        self.rsi_calculator = RSICalculator(periods=self.rsi_period, service_bus=self.service_bus)

        self._setup_event_subscriptions()

    def _setup_event_subscriptions(self):
        """Abonne les méthodes de l'analyseur aux événements du bus."""
        self.service_bus.subscribe("SingleCoinFetched", self._handle_single_coin_fetched)
        self.service_bus.subscribe("TopCoinsFetchingCompleted", self._handle_top_coins_fetching_completed)
        self.service_bus.subscribe("HistoricalPricesFetched", self._handle_historical_prices_fetched)
        self.service_bus.subscribe("RSICalculated", self._handle_rsi_calculated)
        self.service_bus.subscribe("CorrelationAnalyzed", self._handle_correlation_analyzed)

    def _handle_single_coin_fetched(self, payload: Dict):
        """Gère l'événement SingleCoinFetched, qui arrive pour chaque coin."""
        coin = payload.get('coin')
        session_guid = payload.get('session_guid')
        if not coin:
            return

        self.db_manager.save_token(coin, session_guid)

        with self._results_lock:
            if coin['symbol'].lower() != 'btc':
                self._coins_to_process.append((coin['id'], coin['symbol']))
            self.market_caps[coin['symbol']] = coin['market_cap']

    def _handle_top_coins_fetching_completed(self, payload: Dict):
        """Déclenche la suite des opérations après la récupération de tous les coins."""
        logger.info(f"Récupération de {payload['coins_count']} coins terminée. Démarrage de l'analyse.")

        market_caps_values = list(self.market_caps.values())
        self.low_cap_threshold = np.percentile(market_caps_values, 25) if market_caps_values else float('inf')
        logger.info(f"Seuil de faible capitalisation (25e percentile): ${self.low_cap_threshold:,.2f}")

        logger.info("Récupération des données pour Bitcoin...")
        self.data_fetcher.fetch_historical_prices(('bitcoin', 'btc'), self.weeks)

    def _handle_historical_prices_fetched(self, payload: Dict):
        """Gère l'événement HistoricalPricesFetched."""
        coin_id_symbol = payload.get('coin_id_symbol')
        prices_df = payload.get('prices_df')
        session_guid = payload.get('session_guid')
        coin_symbol = coin_id_symbol[1]

        if prices_df is None or prices_df.empty:
            logger.warning(f"Données de prix manquantes pour {coin_symbol}. Le coin ne sera pas analysé.")
            # Point de sortie 1: les données sont manquantes. On incrémente le compteur.
            with self._results_lock:
                self._processed_coins_count += 1
                self._check_and_set_completion()
            return

        self.db_manager.save_prices(coin_id_symbol, prices_df, session_guid)
        prices_series = prices_df['close']

        if coin_symbol == 'btc':
            self.btc_prices = prices_series

        self.rsi_calculator.calculate(coin_id_symbol, prices_series, session_guid)

    def _handle_rsi_calculated(self, payload: Dict):
        """Gère l'événement RSICalculated."""
        coin_id_symbol = payload.get('coin_id_symbol')
        rsi_series = payload.get('rsi')
        session_guid = payload.get('session_guid')
        coin_symbol = coin_id_symbol[1]

        if rsi_series is None or rsi_series.empty:
            logger.warning(f"Série RSI manquante pour {coin_symbol}.")
            # Point de sortie 2: le calcul du RSI échoue. On incrémente le compteur.
            with self._results_lock:
                self._processed_coins_count += 1
                self._check_and_set_completion()
            return

        self.db_manager.save_rsi(coin_id_symbol, rsi_series, session_guid)

        if coin_symbol == 'btc':
            self.btc_rsi = rsi_series
            # Le RSI de BTC est prêt. On peut déclencher les requêtes pour les autres coins.
            with self._results_lock:
                self._total_coins_to_process = len(self._coins_to_process)
                for coin_id, coin_symbol in self._coins_to_process:
                    self.data_fetcher.fetch_historical_prices((coin_id, coin_symbol), self.weeks)
        else:
            self._analyze_correlation(coin_id_symbol, rsi_series, session_guid)

    def _analyze_correlation(self, coin_id_symbol: Tuple[str, str], coin_rsi: pd.Series, session_guid: str):
        """Calcule et publie la corrélation."""
        if self.btc_rsi is None:
            logger.warning("RSI de BTC non disponible pour l'analyse. Ignoré.")
            # Point de sortie 3: le RSI de BTC est manquant. On incrémente le compteur.
            with self._results_lock:
                self._processed_coins_count += 1
                self._check_and_set_completion()
            return

        common_index_rsi = self.btc_rsi.index.intersection(coin_rsi.index)
        if len(common_index_rsi) < self.rsi_period:
            logger.warning(f"Index commun RSI insuffisant pour {coin_id_symbol[1]}.")
            # Point de sortie 4: données insuffisantes. On incrémente le compteur.
            with self._results_lock:
                self._processed_coins_count += 1
                self._check_and_set_completion()
            return

        aligned_btc_rsi = self.btc_rsi.loc[common_index_rsi]
        aligned_coin_rsi = coin_rsi.loc[common_index_rsi]
        correlation = aligned_coin_rsi.corr(aligned_btc_rsi)

        market_cap = self.market_caps.get(coin_id_symbol[1], 0)
        low_cap_quartile = market_cap <= self.low_cap_threshold

        if pd.isna(correlation) or abs(correlation) < self.correlation_threshold or not low_cap_quartile:
            logger.info(f"Corrélation pour {coin_id_symbol[1]} non pertinente.")
            # Point de sortie 5: conditions non remplies. On incrémente le compteur.
            with self._results_lock:
                self._processed_coins_count += 1
                self._check_and_set_completion()
            return

        result = {
            'coin_id': coin_id_symbol[0],
            'coin_symbol': coin_id_symbol[1],
            'correlation': correlation,
            'market_cap': market_cap,
            'low_cap_quartile': low_cap_quartile
        }
        self.service_bus.publish("CorrelationAnalyzed", {'result': result, 'session_guid': session_guid})

    def _handle_correlation_analyzed(self, payload: Dict):
        """Gère l'événement CorrelationAnalyzed."""
        result = payload.get('result')
        session_guid = payload.get('session_guid')

        with self._results_lock:
            if result:
                self.results.append(result)
                run_timestamp = datetime.now(timezone.utc).isoformat()
                self.db_manager.save_correlation(
                    (result['coin_id'], result['coin_symbol']),
                    run_timestamp,
                    result['correlation'],
                    result['market_cap'],
                    result['low_cap_quartile'],
                    session_guid
                )
            # Point de sortie 6: analyse de corrélation réussie. On incrémente le compteur.
            self._processed_coins_count += 1
            self._check_and_set_completion()

    def _check_and_set_completion(self):
        """Vérifie si toutes les tâches sont terminées et signale l'événement de fin."""
        # Note: Cette méthode est maintenant appelée à l'intérieur d'un bloc `with self._results_lock`
        # et n'est donc plus une cause de blocage récursif.
        logger.debug(f"Pièces traitées : {self._processed_coins_count}/{self._total_coins_to_process}")
        if self._processed_coins_count >= self._total_coins_to_process:
            self._all_processing_completed.set()
            logger.info(f"Toutes les analyses de corrélation sont terminées.")

    def _print_results(self):
        """Affiche les résultats finaux."""
        results = sorted(self.results, key=lambda x: (-abs(x['correlation']), x['market_cap']))
        logger.info(f"\nTokens à faible capitalisation avec forte corrélation RSI avec BTC ({self.weeks} semaines) :")
        for result in results:
            logger.info(
                f"Coin: {result['coin_id']}/{result['coin_symbol']}, Correlation RSI: {result['correlation']:.3f}, "
                f"Market Cap: ${result['market_cap']:,.2f}")
        logger.info("\nRésumé de l'historique des corrélations :")

        # L'appel est maintenant bloquant et passe par la file d'attente du thread DatabaseManager
        try:
            correlations = self.db_manager.get_correlations(session_guid=self.session_guid)
            for row in correlations:
                logger.info(
                    f"Run: {row[2]}, Coin: {row[0]}/{row[1]}, Correlation: {row[3]:.3f}, Market Cap: ${row[4]:,.2f}, "
                    f"Low Cap Quartile: {row[5]}")
        except Exception as e:
            logger.error(f"Erreur lors de l'affichage des corrélations : {e}")

    def run(self) -> None:
        """Exécute l'analyse complète."""
        self.service_bus.start()
        self.db_manager.start()
        self.data_fetcher.start()
        self.rsi_calculator.start()

        logger.info("Démarrage de l'analyse...")

        self.data_fetcher.fetch_top_coins(self.top_n_coins)

        # La boucle principale attend simplement la fin du processus complet
        self._all_processing_completed.wait()

        self.db_manager.get_correlations(session_guid=self.session_guid)
        self._print_results()

        self.data_fetcher.stop()
        self.rsi_calculator.stop()
        self.db_manager.stop()
        self.service_bus.stop()
