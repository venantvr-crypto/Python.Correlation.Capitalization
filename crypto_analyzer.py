import threading
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

        self._processed_coins_count = 0
        self._total_coins_to_process = 0

        self._all_processing_completed = threading.Event()

        self.service_bus = ServiceBus()
        # On passe le ServiceBus à chaque composant, qui gérera ses propres abonnements.
        self.db_manager = DatabaseManager(service_bus=self.service_bus)
        self.data_fetcher = DataFetcher(session_guid=self.session_guid, service_bus=self.service_bus)
        self.rsi_calculator = RSICalculator(periods=self.rsi_period, service_bus=self.service_bus)

        self._setup_event_subscriptions()

    def _setup_event_subscriptions(self):
        """Abonne les méthodes de l'analyseur aux événements du bus."""
        self.service_bus.subscribe("RunAnalysisRequested", self._handle_run_analysis_requested)
        self.service_bus.subscribe("TopCoinsFetched", self._handle_top_coins_fetched)
        self.service_bus.subscribe("HistoricalPricesFetched", self._handle_historical_prices_fetched)
        self.service_bus.subscribe("RSICalculated", self._handle_rsi_calculated)
        self.service_bus.subscribe("CorrelationAnalyzed", self._handle_correlation_analyzed)

    def _handle_run_analysis_requested(self, payload: Dict):
        """Déclenche la récupération des top coins."""
        logger.info("Démarrage de l'analyse, demande de la liste des top coins.")
        self.service_bus.publish("FetchTopCoinsRequested", {'n': self.top_n_coins, 'session_guid': self.session_guid})

    def _handle_top_coins_fetched(self, payload: Dict):
        """Gère la liste des top coins, publie les événements de demande de prix."""
        coins = payload.get('coins', [])
        for coin in coins:
            self.service_bus.publish("SingleCoinFetched", {'coin': coin, 'session_guid': self.session_guid})

        self._coins_to_process = [(coin['id'], coin['symbol']) for coin in coins if coin['symbol'].lower() != 'btc']
        self.market_caps = {coin['symbol']: coin['market_cap'] for coin in coins}

        market_caps_values = list(self.market_caps.values())
        self.low_cap_threshold = np.percentile(market_caps_values, 25) if market_caps_values else float('inf')
        logger.info(f"Seuil de faible capitalisation (25e percentile): ${self.low_cap_threshold:,.2f}")

        logger.info("Demande des données pour Bitcoin...")
        self.service_bus.publish("FetchHistoricalPricesRequested", {
            'coin_id_symbol': ('bitcoin', 'btc'),
            'weeks': self.weeks,
            'session_guid': self.session_guid
        })

    def _handle_historical_prices_fetched(self, payload: Dict):
        """Gère l'événement HistoricalPricesFetched et demande le calcul du RSI."""
        coin_id_symbol = payload.get('coin_id_symbol')
        prices_df = payload.get('prices_df')
        session_guid = payload.get('session_guid')
        coin_symbol = coin_id_symbol[1]

        if prices_df is None or prices_df.empty:
            logger.warning(f"Données de prix manquantes pour {coin_symbol}. Le coin ne sera pas analysé.")
            self._check_completion()
            return

        self.service_bus.publish("CalculateRSIRequested", {
            'coin_id_symbol': coin_id_symbol,
            'prices_series': prices_df['close'],
            'session_guid': session_guid
        })

    def _handle_rsi_calculated(self, payload: Dict):
        """Gère l'événement RSICalculated et, si c'est BTC, lance l'analyse des autres coins."""
        coin_id_symbol = payload.get('coin_id_symbol')
        rsi_series = payload.get('rsi')
        session_guid = payload.get('session_guid')
        coin_symbol = coin_id_symbol[1]

        if rsi_series is None or rsi_series.empty:
            logger.warning(f"Série RSI manquante pour {coin_symbol}.")
            self._check_completion()
            return

        if coin_symbol == 'btc':
            self.btc_rsi = rsi_series
            self._total_coins_to_process = len(self._coins_to_process)
            for coin_id, coin_symbol in self._coins_to_process:
                self.service_bus.publish("FetchHistoricalPricesRequested", {
                    'coin_id_symbol': (coin_id, coin_symbol),
                    'weeks': self.weeks,
                    'session_guid': session_guid
                })
        else:
            self._analyze_correlation(coin_id_symbol, rsi_series, session_guid)

    def _analyze_correlation(self, coin_id_symbol: Tuple[str, str], coin_rsi: pd.Series, session_guid: str):
        """Calcule la corrélation et publie le résultat."""
        if self.btc_rsi is None:
            logger.warning("RSI de BTC non disponible pour l'analyse. Ignoré.")
            self._check_completion()
            return

        common_index_rsi = self.btc_rsi.index.intersection(coin_rsi.index)
        if len(common_index_rsi) < self.rsi_period:
            logger.warning(f"Index commun RSI insuffisant pour {coin_id_symbol[1]}.")
            self._check_completion()
            return

        aligned_btc_rsi = self.btc_rsi.loc[common_index_rsi]
        aligned_coin_rsi = coin_rsi.loc[common_index_rsi]
        correlation = aligned_coin_rsi.corr(aligned_btc_rsi)

        market_cap = self.market_caps.get(coin_id_symbol[1], 0)
        low_cap_quartile = market_cap <= self.low_cap_threshold

        if pd.isna(correlation) or abs(correlation) < self.correlation_threshold or not low_cap_quartile:
            logger.info(f"Corrélation pour {coin_id_symbol[1]} non pertinente.")
            self._check_completion()
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
        """Gère l'événement CorrelationAnalyzed et incrémente le compteur."""
        result = payload.get('result')
        if result:
            self.results.append(result)
        self._check_completion()

    def _check_completion(self):
        """Vérifie si toutes les tâches sont terminées et signale l'événement de fin."""
        self._processed_coins_count += 1
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
        # Démarrage des services
        self.service_bus.start()
        self.db_manager.start()
        # DataFetcher et RSICalculator ne sont plus des threads eux-mêmes.

        # Le thread principal envoie le tout premier événement pour lancer le processus.
        self.service_bus.publish("RunAnalysisRequested", {'session_guid': self.session_guid})

        # Le thread principal attend que tout le traitement soit terminé.
        self._all_processing_completed.wait()

        # Récupération et affichage des résultats.
        self.db_manager.get_correlations(session_guid=self.session_guid)
        self._print_results()

        # Arrêt des services
        self.service_bus.stop()
        self.db_manager.stop()
