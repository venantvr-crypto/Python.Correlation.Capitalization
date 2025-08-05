import threading
from typing import Optional, Tuple, List, Dict

import pandas as pd

from analysis_job import AnalysisJob
from data_fetcher import DataFetcher
from database_manager import DatabaseManager
from display_agent import DisplayAgent
from events import RunAnalysisRequested, TopCoinsFetched, PrecisionDataFetched, HistoricalPricesFetched, RSICalculated, CorrelationAnalyzed, CoinProcessingFailed, DisplayCompleted, AnalysisJobCompleted, AnalysisConfigurationProvided
from logger import logger
from rsi_calculator import RSICalculator
from service_bus import ServiceBus


class CryptoAnalyzer:
    """Orchestre les analyses de corrélations RSI pour plusieurs timeframes."""

    def __init__(self, weeks: int = 50, top_n_coins: int = 200, correlation_threshold: float = 0.7,
                 rsi_period: int = 14, timeframes=None, low_cap_percentile: float = 25.0):
        if timeframes is None:
            timeframes = ['1d']
        self.weeks = weeks
        self.top_n_coins = top_n_coins
        # TODO
        self.correlation_threshold = correlation_threshold
        self.rsi_period = rsi_period
        # TODO...
        self.low_cap_percentile = low_cap_percentile  # 25e percentile pour définir une "faible capitalisation"
        self.session_guid: Optional[str] = None
        self.timeframes = timeframes
        self.market_caps: Dict[str, float] = {}
        self.low_cap_threshold: float = float('inf')
        self.coins: List[Dict] = []
        self.precision_data: Dict[str, Dict] = {}
        self.results: List[Dict] = []
        self.rsi_results: Dict[Tuple[str, str, str], pd.Series] = {}
        self.analysis_jobs: Dict[str, AnalysisJob] = {tf: AnalysisJob(tf, self) for tf in self.timeframes}
        self._job_completion_counter = len(self.timeframes)
        self._job_lock = threading.Lock()
        self._all_processing_completed = threading.Event()
        self._initial_data_loaded = threading.Event()

        self.service_bus = ServiceBus()
        self.db_manager = DatabaseManager(service_bus=self.service_bus)
        self.data_fetcher = DataFetcher(service_bus=self.service_bus)
        self.rsi_calculator = RSICalculator(periods=self.rsi_period, service_bus=self.service_bus)
        self.display_agent = DisplayAgent(service_bus=self.service_bus)
        self._setup_event_subscriptions()

    def _setup_event_subscriptions(self):
        self.service_bus.subscribe("RunAnalysisRequested", self._handle_run_analysis_requested)
        self.service_bus.subscribe("TopCoinsFetched", self._handle_top_coins_fetched)
        self.service_bus.subscribe("PrecisionDataFetched", self._handle_precision_data_fetched)
        self.service_bus.subscribe("HistoricalPricesFetched", self._handle_historical_prices_fetched)
        self.service_bus.subscribe("RSICalculated", self._handle_rsi_calculated)
        self.service_bus.subscribe("CorrelationAnalyzed", self._handle_correlation_analyzed)
        self.service_bus.subscribe("CoinProcessingFailed", self._handle_coin_processing_failed)
        self.service_bus.subscribe("AnalysisJobCompleted", self._handle_analysis_job_completed)
        self.service_bus.subscribe("DisplayCompleted", self._handle_display_completed)

    def _handle_run_analysis_requested(self, event: RunAnalysisRequested):
        logger.info("Démarrage de l'analyse, demande des données de précision et de la liste des top coins.")
        self.service_bus.publish("FetchPrecisionDataRequested", {})
        self.service_bus.publish("FetchTopCoinsRequested", {'n': self.top_n_coins})

    def _handle_precision_data_fetched(self, event: PrecisionDataFetched):
        self.precision_data = {item['symbol']: item for item in event.precision_data}
        self._start_analysis_if_ready()

    def _handle_top_coins_fetched(self, event: TopCoinsFetched):
        self.coins = event.coins
        self._start_analysis_if_ready()

    def _start_analysis_if_ready(self):
        if self.coins and self.precision_data and not self._initial_data_loaded.is_set():
            self._initial_data_loaded.set()
            logger.info("Données initiales reçues. Filtrage des paires et lancement des jobs.")

            usdc_base_symbols = {m['base_asset'] for m in self.precision_data.values() if m['quote_asset'] == 'USDC'}
            original_coin_count = len(self.coins)
            self.coins = [c for c in self.coins if c.get('symbol', '').upper() in usdc_base_symbols]
            logger.info(
                f"Filtrage des top coins : {original_coin_count} -> {len(self.coins)} ayant une paire USDC sur Binance.")

            # AJOUT : Publier les métadonnées de chaque coin pour l'enregistrement en BDD
            logger.info(f"Enregistrement des métadonnées pour les {len(self.coins)} coins filtrés.")
            for coin in self.coins:
                self.service_bus.publish("SingleCoinFetched", {'coin': coin})

            self.market_caps = {c['symbol'].lower(): c.get('market_cap', 0) for c in self.coins}
            market_cap_values = [mc for mc in self.market_caps.values() if mc > 0]
            if market_cap_values:
                self.low_cap_threshold = pd.Series(market_cap_values).quantile(self.low_cap_percentile / 100)
            else:
                self.low_cap_threshold = float('inf')
            logger.info(f"Seuil de faible capitalisation ({self.low_cap_percentile}e percentile) fixé à : ${self.low_cap_threshold:,.2f}")

            coins_to_process = [(c['id'], c['symbol']) for c in self.coins]

            for timeframe, job in self.analysis_jobs.items():
                job.set_coins_to_process(coins_to_process)
                logger.info(f"Lancement du job pour le timeframe {timeframe} avec {len(job.coins_to_process) + 1} paires.")

                self.service_bus.publish("FetchHistoricalPricesRequested", {
                    'coin_id_symbol': ('bitcoin', 'btc'), 'weeks': self.weeks, 'timeframe': timeframe
                })
                for coin_id, symbol in job.coins_to_process:
                    self.service_bus.publish("FetchHistoricalPricesRequested", {
                        'coin_id_symbol': (coin_id, symbol), 'weeks': self.weeks, 'timeframe': timeframe
                    })

    def _handle_historical_prices_fetched(self, event: HistoricalPricesFetched):
        if event.prices_df is None or event.prices_df.empty:
            self.service_bus.publish("CoinProcessingFailed", {'coin_id_symbol': event.coin_id_symbol, 'timeframe': event.timeframe})
            return
        self.service_bus.publish("CalculateRSIRequested", {
            'coin_id_symbol': event.coin_id_symbol, 'prices_series': event.prices_df['close'], 'timeframe': event.timeframe
        })

    def _handle_rsi_calculated(self, event: RSICalculated):
        job = self.analysis_jobs.get(event.timeframe)
        if not job:
            return

        if event.rsi is None or event.rsi.empty:
            self.service_bus.publish("CoinProcessingFailed", {'coin_id_symbol': event.coin_id_symbol, 'timeframe': event.timeframe})
            return

        key = (event.coin_id_symbol[0], event.coin_id_symbol[1], event.timeframe)
        self.rsi_results[key] = event.rsi

        if event.coin_id_symbol[1].lower() == 'btc':
            job.btc_rsi = event.rsi

        job.decrement_counter()

    def _handle_coin_processing_failed(self, event: CoinProcessingFailed):
        job = self.analysis_jobs.get(event.timeframe)
        if job:
            job.decrement_counter()

    def analyze_correlation(self, coin_id_symbol: Tuple[str, str], coin_rsi: pd.Series, btc_rsi: pd.Series, timeframe: str):
        common_index_rsi = btc_rsi.index.intersection(coin_rsi.index)
        if len(common_index_rsi) < self.rsi_period:
            return

        aligned_btc_rsi = btc_rsi.loc[common_index_rsi]
        aligned_coin_rsi = coin_rsi.loc[common_index_rsi]
        correlation = aligned_coin_rsi.corr(aligned_btc_rsi)

        market_cap = self.market_caps.get(coin_id_symbol[1].lower(), 0)
        low_cap_quartile = market_cap <= self.low_cap_threshold

        if pd.isna(correlation) or abs(correlation) < self.correlation_threshold or not low_cap_quartile:
            return

        result = {
            'coin_id': coin_id_symbol[0],
            'coin_symbol': coin_id_symbol[1],
            'correlation': correlation,
            'market_cap': market_cap,
            'low_cap_quartile': low_cap_quartile
        }
        self.service_bus.publish("CorrelationAnalyzed", {'result': result, 'timeframe': timeframe})

    def _handle_correlation_analyzed(self, event: CorrelationAnalyzed):
        if event.result:
            self.results.append(event.result)

    def _handle_analysis_job_completed(self, event: AnalysisJobCompleted):
        with self._job_lock:
            self._job_completion_counter -= 1
            logger.info(f"Job pour timeframe {event.timeframe} terminé. Jobs restants : {self._job_completion_counter}")
            if self._job_completion_counter <= 0:
                logger.info("Tous les jobs d'analyse sont terminés.")
                self.service_bus.publish("FinalResultsReady", {
                    'results': self.results, 'weeks': self.weeks, 'timeframes': self.timeframes
                })

    # noinspection PyUnusedLocal
    def _handle_display_completed(self, event: DisplayCompleted):
        self._all_processing_completed.set()
        logger.info("L'analyse, l'affichage et l'arrêt des services sont terminés.")

    def run(self, session_guid: str) -> None:
        self.session_guid = session_guid
        services = [self.service_bus, self.db_manager, self.data_fetcher, self.rsi_calculator, self.display_agent]
        for service in services:
            service.start()

        # Publier la configuration globale pour tous les services
        config_payload = AnalysisConfigurationProvided(
            session_guid=self.session_guid,
            weeks=self.weeks,
            top_n_coins=self.top_n_coins,
            correlation_threshold=self.correlation_threshold,
            rsi_period=self.rsi_period,
            timeframes=self.timeframes,
            low_cap_percentile=self.low_cap_percentile
        )
        self.service_bus.publish("AnalysisConfigurationProvided", config_payload)

        # Lancer l'analyse
        self.service_bus.publish("RunAnalysisRequested", {})

        self._all_processing_completed.wait()
        for service in reversed(services):
            service.stop()
