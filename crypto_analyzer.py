import threading
from typing import Optional, Tuple, List, Dict

import pandas as pd

from data_fetcher import DataFetcher
from database_manager import DatabaseManager
from display_agent import DisplayAgent
from events import RunAnalysisRequested, TopCoinsFetched, MarketCapThresholdCalculated, HistoricalPricesFetched, \
    RSICalculated, CorrelationAnalyzed, CoinProcessingFailed, DisplayCompleted, PrecisionDataFetched
from logger import logger
from market_cap_agent import MarketCapAgent
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
        self.coins: List[Dict] = []
        self.precision_data: Dict[str, Dict] = {}

        # Attributs de synchronisation et de comptage
        self._processing_counter = 0
        self._counter_lock = threading.Lock()
        self._all_processing_completed = threading.Event()
        self._precision_data_loaded = threading.Event()
        self._top_coins_loaded = threading.Event()

        # Initialisation des composants
        self.service_bus = ServiceBus()
        self.db_manager = DatabaseManager(service_bus=self.service_bus)
        self.data_fetcher = DataFetcher(session_guid=self.session_guid, service_bus=self.service_bus)
        self.rsi_calculator = RSICalculator(periods=self.rsi_period, service_bus=self.service_bus)
        self.display_agent = DisplayAgent(service_bus=self.service_bus)
        self.market_cap_agent = MarketCapAgent(service_bus=self.service_bus)

        self._setup_event_subscriptions()

    def _setup_event_subscriptions(self):
        """Définit les abonnements aux événements du bus de services."""
        self.service_bus.subscribe("RunAnalysisRequested", self._handle_run_analysis_requested)
        self.service_bus.subscribe("TopCoinsFetched", self._handle_top_coins_fetched)
        self.service_bus.subscribe("MarketCapThresholdCalculated", self._handle_market_cap_threshold_calculated)
        self.service_bus.subscribe("HistoricalPricesFetched", self._handle_historical_prices_fetched)
        self.service_bus.subscribe("RSICalculated", self._handle_rsi_calculated)
        self.service_bus.subscribe("CorrelationAnalyzed", self._handle_correlation_analyzed)
        self.service_bus.subscribe("CoinProcessingFailed", self._handle_coin_processing_failed)
        self.service_bus.subscribe("DisplayCompleted", self._handle_display_completed)
        self.service_bus.subscribe("PrecisionDataFetched", self._handle_precision_data_fetched)

    def _handle_run_analysis_requested(self, event: RunAnalysisRequested):
        """Lance les premières requêtes de données."""
        logger.info("Démarrage de l'analyse, demande des données de précision et de la liste des top coins.")
        # Publie les deux requêtes de manière asynchrone
        self.service_bus.publish("FetchPrecisionDataRequested", {'session_guid': event.session_guid})
        self.service_bus.publish("FetchTopCoinsRequested", {'n': self.top_n_coins, 'session_guid': event.session_guid})

    def _handle_precision_data_fetched(self, event: PrecisionDataFetched):
        """Reçoit TOUTES les paires, les stocke et signale que les données sont prêtes."""
        logger.info(f"Données de précision reçues pour {len(event.precision_data)} paires.")
        # Stocke les données de précision en interne, l'enregistrement en BDD est géré par le DatabaseManager
        self.precision_data = {item['symbol']: item for item in event.precision_data}
        self._precision_data_loaded.set()
        self._start_analysis_if_ready()

    def _handle_top_coins_fetched(self, event: TopCoinsFetched):
        """Reçoit les top coins et signale que les données sont prêtes."""
        self.coins = event.coins
        self._top_coins_loaded.set()
        self._start_analysis_if_ready()

    def _start_analysis_if_ready(self):
        """Vérifie si toutes les données initiales sont là, puis filtre et lance l'analyse."""
        if not (self._precision_data_loaded.is_set() and self._top_coins_loaded.is_set()):
            return

        logger.info("Données de précision et top coins reçus. Démarrage du filtrage des paires USDC.")

        usdc_base_symbols = {
            m['base_asset'] for m in self.precision_data.values() if m['quote_asset'] == 'USDC'
        }

        original_coin_count = len(self.coins)
        self.coins = [
            c for c in self.coins if c.get('symbol', '').upper() in usdc_base_symbols
        ]
        logger.info(
            f"Filtrage des top coins : {original_coin_count} -> {len(self.coins)} ayant une paire USDC sur Binance.")

        for coin in self.coins:
            self.service_bus.publish("SingleCoinFetched", {'coin': coin, 'session_guid': self.session_guid})

        self.service_bus.publish("CalculateMarketCapThresholdRequested", {
            'coins': self.coins,
            'session_guid': self.session_guid
        })

    def _handle_market_cap_threshold_calculated(self, event: MarketCapThresholdCalculated):
        """Stocke les données de capitalisation et lance la récupération des prix de BTC."""
        self.market_caps = event.market_caps
        self.low_cap_threshold = event.low_cap_threshold
        self._coins_to_process = [(coin['id'], coin['symbol']) for coin in self.coins if
                                  coin['symbol'].lower() != 'btc']

        logger.info("Demande des données pour Bitcoin...")
        self.service_bus.publish("FetchHistoricalPricesRequested", {
            'coin_id_symbol': ('bitcoin', 'btc'),
            'weeks': self.weeks,
            'session_guid': event.session_guid
        })

    def _handle_historical_prices_fetched(self, event: HistoricalPricesFetched):
        """Lance le calcul du RSI pour les prix reçus."""
        coin_id_symbol = event.coin_id_symbol
        prices_df = event.prices_df
        session_guid = event.session_guid
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

    def _handle_rsi_calculated(self, event: RSICalculated):
        """Traite le RSI calculé : stocke celui de BTC ou lance l'analyse de corrélation."""
        coin_id_symbol = event.coin_id_symbol
        rsi_series = event.rsi
        session_guid = event.session_guid
        coin_symbol = coin_id_symbol[1]

        if rsi_series is None or rsi_series.empty:
            logger.warning(f"Série RSI manquante pour {coin_symbol}.")
            self.service_bus.publish("CoinProcessingFailed", {'coin_id_symbol': coin_id_symbol})
            return

        if coin_symbol == 'btc':
            self.btc_rsi = rsi_series
            with self._counter_lock:
                self._processing_counter = len(self._coins_to_process)
            for coin_id, symbol in self._coins_to_process:
                self.service_bus.publish("FetchHistoricalPricesRequested", {
                    'coin_id_symbol': (coin_id, symbol),
                    'weeks': self.weeks,
                    'session_guid': session_guid
                })
        else:
            self._analyze_correlation(coin_id_symbol, rsi_series, session_guid)

    def _analyze_correlation(self, coin_id_symbol: Tuple[str, str], coin_rsi: pd.Series, session_guid: str):
        """Analyse la corrélation entre le RSI d'un coin et celui de BTC."""
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

    def _handle_correlation_analyzed(self, event: CorrelationAnalyzed):
        """Ajoute un résultat d'analyse réussi et décrémente le compteur."""
        if event.result:
            self.results.append(event.result)
        self._decrement_processing_counter()

    # noinspection PyUnusedLocal
    def _handle_coin_processing_failed(self, event: CoinProcessingFailed):
        """Gère l'échec du traitement d'un coin en décrémentant le compteur."""
        self._decrement_processing_counter()

    def _decrement_processing_counter(self):
        """Décrémente le compteur de coins à traiter et publie les résultats finaux si terminé."""
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

    # noinspection PyUnusedLocal
    def _handle_display_completed(self, event: DisplayCompleted):
        """Signale que l'ensemble du processus est terminé."""
        self._all_processing_completed.set()
        logger.info("L'analyse, l'affichage et l'arrêt des services sont terminés.")

    def run(self) -> None:
        """Démarre tous les services, lance l'analyse et attend la fin."""
        services = [
            self.service_bus, self.db_manager, self.data_fetcher,
            self.rsi_calculator, self.display_agent, self.market_cap_agent
        ]

        for service in services:
            service.start()

        self.service_bus.publish("RunAnalysisRequested", {'session_guid': self.session_guid})
        self._all_processing_completed.wait()

        for service in reversed(services):
            service.stop()
