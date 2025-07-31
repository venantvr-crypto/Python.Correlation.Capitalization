import threading
from typing import Optional, Tuple, List, Dict

import numpy as np
import pandas as pd

from data_fetcher import DataFetcher
from database_manager import DatabaseManager
from display_agent import DisplayAgent
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

        self._processing_counter = 0
        self._counter_lock = threading.Lock()
        self._all_processing_completed = threading.Event()

        self.service_bus = ServiceBus()
        self.db_manager = DatabaseManager(service_bus=self.service_bus)
        self.data_fetcher = DataFetcher(session_guid=self.session_guid, service_bus=self.service_bus)
        self.rsi_calculator = RSICalculator(periods=self.rsi_period, service_bus=self.service_bus)
        self.display_agent = DisplayAgent(service_bus=self.service_bus)

        self._setup_event_subscriptions()

    def _setup_event_subscriptions(self):
        self.service_bus.subscribe("RunAnalysisRequested", self._handle_run_analysis_requested)
        self.service_bus.subscribe("TopCoinsFetched", self._handle_top_coins_fetched)
        self.service_bus.subscribe("HistoricalPricesFetched", self._handle_historical_prices_fetched)
        self.service_bus.subscribe("RSICalculated", self._handle_rsi_calculated)
        self.service_bus.subscribe("CorrelationAnalyzed", self._handle_correlation_analyzed)
        self.service_bus.subscribe("CoinProcessingFailed", self._handle_coin_processing_failed)
        self.service_bus.subscribe("DisplayCompleted", self._handle_display_completed)

    def _handle_run_analysis_requested(self, payload: Dict):
        logger.info("Démarrage de l'analyse, demande de la liste des top coins.")
        self.service_bus.publish("FetchTopCoinsRequested", {'n': self.top_n_coins, 'session_guid': self.session_guid})

    def _handle_top_coins_fetched(self, payload: Dict):
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
        coin_id_symbol = payload.get('coin_id_symbol')
        prices_df = payload.get('prices_df')
        session_guid = payload.get('session_guid')
        coin_symbol = coin_id_symbol[1]

        if prices_df is None or prices_df.empty:
            logger.warning(f"Données de prix manquantes pour {coin_symbol}. Le coin ne sera pas analysé.")
            self.service_bus.publish("CoinProcessingFailed", {'coin_id_symbol': coin_id_symbol})
            return

        self.service_bus.publish("CalculateRSIRequested", {
            'coin_id_symbol': coin_id_symbol,
            'prices_series': prices_df['close'],
            'session_guid': session_guid
        })

    def _handle_rsi_calculated(self, payload: Dict):
        coin_id_symbol = payload.get('coin_id_symbol')
        rsi_series = payload.get('rsi')
        session_guid = payload.get('session_guid')
        coin_symbol = coin_id_symbol[1]

        if rsi_series is None or rsi_series.empty:
            logger.warning(f"Série RSI manquante pour {coin_symbol}.")
            self.service_bus.publish("CoinProcessingFailed", {'coin_id_symbol': coin_id_symbol})
            return

        if coin_symbol == 'btc':
            self.btc_rsi = rsi_series
            with self._counter_lock:
                self._processing_counter = len(self._coins_to_process)
            for coin_id, coin_symbol in self._coins_to_process:
                self.service_bus.publish("FetchHistoricalPricesRequested", {
                    'coin_id_symbol': (coin_id, coin_symbol),
                    'weeks': self.weeks,
                    'session_guid': session_guid
                })
        else:
            self._analyze_correlation(coin_id_symbol, rsi_series, session_guid)

    def _analyze_correlation(self, coin_id_symbol: Tuple[str, str], coin_rsi: pd.Series, session_guid: str):
        if self.btc_rsi is None:
            logger.warning("RSI de BTC non disponible pour l'analyse. Ignoré.")
            self.service_bus.publish("CoinProcessingFailed", {'coin_id_symbol': coin_id_symbol})
            return

        common_index_rsi = self.btc_rsi.index.intersection(coin_rsi.index)
        if len(common_index_rsi) < self.rsi_period:
            logger.warning(f"Index commun RSI insuffisant pour {coin_id_symbol[1]}.")
            self.service_bus.publish("CoinProcessingFailed", {'coin_id_symbol': coin_id_symbol})
            return

        aligned_btc_rsi = self.btc_rsi.loc[common_index_rsi]
        aligned_coin_rsi = coin_rsi.loc[common_index_rsi]
        correlation = aligned_coin_rsi.corr(aligned_btc_rsi)

        market_cap = self.market_caps.get(coin_id_symbol[1], 0)
        low_cap_quartile = market_cap <= self.low_cap_threshold

        if pd.isna(correlation) or abs(correlation) < self.correlation_threshold or not low_cap_quartile:
            logger.info(f"Corrélation pour {coin_id_symbol[1]} non pertinente.")
            self.service_bus.publish("CoinProcessingFailed", {'coin_id_symbol': coin_id_symbol})
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
        result = payload.get('result')
        if result:
            self.results.append(result)
        self._decrement_processing_counter()

    def _handle_coin_processing_failed(self, payload: Dict):
        self._decrement_processing_counter()

    def _decrement_processing_counter(self):
        with self._counter_lock:
            self._processing_counter -= 1
            logger.debug(f"Compteur de traitement : {self._processing_counter}")
            if self._processing_counter <= 0:
                logger.info("Toutes les analyses de corrélation sont terminées.")
                self.service_bus.publish("FinalResultsReady", {
                    'results': self.results,
                    'weeks': self.weeks,
                    'session_guid': self.session_guid,
                    'db_manager': self.db_manager
                })

    def _handle_display_completed(self, payload: Dict):
        """Gère l'événement de fin d'affichage pour déclencher la fermeture."""
        self._all_processing_completed.set()
        logger.info("L'analyse, l'affichage et l'arrêt des services sont terminés.")

    def run(self) -> None:
        self.service_bus.start()
        self.db_manager.start()
        self.data_fetcher.start()
        self.rsi_calculator.start()
        self.display_agent.start()

        self.service_bus.publish("RunAnalysisRequested", {'session_guid': self.session_guid})

        self._all_processing_completed.wait()

        self.data_fetcher.stop()
        self.rsi_calculator.stop()
        self.db_manager.stop()
        self.service_bus.stop()
        self.display_agent.stop()
