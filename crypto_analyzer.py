import threading
from io import StringIO
from typing import Dict, List, Optional, Tuple

import pandas as pd
# noinspection PyPackageRequirements
from pubsub import OrchestratorBase, ServiceBus, AllProcessingCompleted

from analysis_job import AnalysisJob
from configuration import AnalysisConfig
from data_fetcher import DataFetcher
from database_manager import DatabaseManager
from display_agent import DisplayAgent
from events import (
    AnalysisConfigurationProvided,
    AnalysisJobCompleted,
    CalculateRSIRequested,
    CoinProcessingFailed,
    CorrelationAnalyzed,
    DisplayCompleted,
    FetchPrecisionDataRequested,
    HistoricalPricesFetched,
    PrecisionDataFetched,
    RSICalculated,
    RunAnalysisRequested,
    TopCoinsFetched,
)
from logger import logger
from rsi_calculator import RSICalculator


class CryptoAnalyzer(OrchestratorBase):
    """Orchestre les analyses de corrélations RSI pour plusieurs timeframes."""

    def __init__(self, config: AnalysisConfig, session_guid: str):
        self.config = config
        self.session_guid = session_guid

        service_bus = ServiceBus(url=self.config.pubsub_url, consumer_name=self.__class__.__name__)
        super().__init__(service_bus)

        self.db_manager = None
        self.data_fetcher = None
        self.rsi_calculator = None
        self.display_agent = None

        # Initialiser à None pour un état de départ clair.
        self.coins: Optional[List[Dict]] = None
        self.precision_data: Optional[Dict[str, Dict]] = None

        self.market_caps: Dict[str, float] = {}
        self.low_cap_threshold: float = float("inf")
        self.results: List[Dict] = []
        self.rsi_results: Dict[Tuple[str, str, str], pd.Series] = {}
        self.analysis_jobs: Dict[str, AnalysisJob] = {tf: AnalysisJob(tf, self) for tf in self.config.timeframes}

        self._job_completion_counter = len(self.config.timeframes)
        self._job_lock = threading.Lock()
        self._initial_data_loaded = threading.Event()

    def register_services(self) -> None:
        """Crée et enregistre les services gérés par l'orchestrateur."""
        self.db_manager = DatabaseManager(service_bus=self.service_bus)
        self.data_fetcher = DataFetcher(service_bus=self.service_bus)
        self.rsi_calculator = RSICalculator(service_bus=self.service_bus)
        self.display_agent = DisplayAgent(service_bus=self.service_bus)

        self.services = [self.db_manager, self.data_fetcher, self.rsi_calculator, self.display_agent]

    def setup_event_subscriptions(self) -> None:
        """Abonne les méthodes de l'orchestrateur aux événements du bus de services."""
        self.service_bus.subscribe("RunAnalysisRequested", self._handle_run_analysis_requested)
        self.service_bus.subscribe("TopCoinsFetched", self._handle_top_coins_fetched)
        self.service_bus.subscribe("PrecisionDataFetched", self._handle_precision_data_fetched)
        self.service_bus.subscribe("HistoricalPricesFetched", self._handle_historical_prices_fetched)
        self.service_bus.subscribe("RSICalculated", self._handle_rsi_calculated)
        self.service_bus.subscribe("CorrelationAnalyzed", self._handle_correlation_analyzed)
        self.service_bus.subscribe("CoinProcessingFailed", self._handle_coin_processing_failed)
        self.service_bus.subscribe("AnalysisJobCompleted", self._handle_analysis_job_completed)
        self.service_bus.subscribe("DisplayCompleted", self._handle_display_completed)

    def start_workflow(self) -> None:
        """Démarre le workflow d'analyse."""
        config_payload = AnalysisConfigurationProvided(session_guid=self.session_guid, config=self.config)
        self.service_bus.publish("AnalysisConfigurationProvided", config_payload, self.__class__.__name__)
        self.service_bus.publish("RunAnalysisRequested", RunAnalysisRequested(), self.__class__.__name__)

    def _handle_run_analysis_requested(self, _event: RunAnalysisRequested):
        logger.info("Analyse démarrée. Demande des données initiales (top coins et précision).")
        self.service_bus.publish("FetchPrecisionDataRequested", FetchPrecisionDataRequested(), self.__class__.__name__)
        self.service_bus.publish("FetchTopCoinsRequested", {"n": self.config.top_n_coins}, self.__class__.__name__)

    def _handle_precision_data_fetched(self, event: PrecisionDataFetched):
        self.precision_data = {item["symbol"]: item for item in event.precision_data}
        self._start_analysis_if_ready()

    def _handle_top_coins_fetched(self, event: TopCoinsFetched):
        self.coins = event.coins
        self._start_analysis_if_ready()

    def _start_analysis_if_ready(self):
        """
        Vérifie si les deux flux de données initiaux ont été REÇUS (même s'ils sont vides),
        puis lance l'analyse ou s'arrête proprement.
        """
        # Vérifier que les variables ne sont plus None.
        if not (self.coins is not None and self.precision_data is not None and not self._initial_data_loaded.is_set()):
            logger.debug("En attente de toutes les données initiales...")
            return

        # Gérer le cas où les données reçues sont vides.
        if not self.coins or not self.precision_data:
            logger.warning("Aucune crypto ou paire de marché n'a été trouvée. L'analyse ne peut pas continuer.")
            self._processing_completed.set()
            return

        self._initial_data_loaded.set()
        logger.info("Données initiales (coins et précision) reçues. Démarrage du traitement.")

        usdc_base_symbols = {m["base_asset"] for m in self.precision_data.values() if m["quote_asset"] == "USDC"}
        original_coin_count = len(self.coins)
        self.coins = [c for c in self.coins if c.get("symbol", "").upper() in usdc_base_symbols]
        logger.info(f"Filtrage des cryptos : {original_coin_count} -> {len(self.coins)} avec une paire USDC.")

        for coin in self.coins:
            self.service_bus.publish("SingleCoinFetched", {"coin": coin}, self.__class__.__name__)

        self.market_caps = {c["symbol"].lower(): c.get("market_cap", 0) for c in self.coins}
        market_cap_values = [mc for mc in self.market_caps.values() if mc > 0]
        if market_cap_values:
            self.low_cap_threshold = pd.Series(market_cap_values).quantile(self.config.low_cap_percentile / 100)
        logger.info(
            f"Seuil de faible capitalisation ({self.config.low_cap_percentile}e percentile) : ${self.low_cap_threshold:,.2f}"
        )

        coins_to_process = [(c["id"], c["symbol"]) for c in self.coins]
        for timeframe, job in self.analysis_jobs.items():
            job.set_coins_to_process(coins_to_process)
            logger.info(f"Lancement du job pour {timeframe} avec {len(job.coins_to_process) + 1} paires.")

            self.service_bus.publish(
                "FetchHistoricalPricesRequested",
                {"coin_id_symbol": ("bitcoin", "btc"), "weeks": self.config.weeks, "timeframe": timeframe},
                self.__class__.__name__
            )

            for coin_id, symbol in job.coins_to_process:
                self.service_bus.publish(
                    "FetchHistoricalPricesRequested",
                    {"coin_id_symbol": (coin_id, symbol), "weeks": self.config.weeks, "timeframe": timeframe},
                    self.__class__.__name__
                )

    def _handle_historical_prices_fetched(self, event: HistoricalPricesFetched):
        prices_df = None
        if event.prices_df_json:
            try:
                prices_df = pd.read_json(StringIO(event.prices_df_json), orient="split")
                prices_df.index = pd.to_datetime(prices_df.index, unit="ms", utc=True)
            except Exception as e:
                logger.error(f"Impossible de reconstruire le DataFrame des prix pour {event.coin_id_symbol}: {e}")

        if prices_df is None or prices_df.empty:
            self.service_bus.publish(
                "CoinProcessingFailed",
                {"coin_id_symbol": event.coin_id_symbol, "timeframe": event.timeframe},
                self.__class__.__name__
            )
            return

        close_series = prices_df["close"]
        series_json = close_series.to_json(orient="split")

        # On publie la dataclass correspondante avec la chaîne JSON.
        rsi_request_event = CalculateRSIRequested(
            coin_id_symbol=event.coin_id_symbol, prices_series_json=series_json, timeframe=event.timeframe
        )
        self.service_bus.publish("CalculateRSIRequested", rsi_request_event, self.__class__.__name__)

    def _handle_rsi_calculated(self, event: RSICalculated):
        job = self.analysis_jobs.get(event.timeframe)
        if not job:
            return

        rsi_series = None
        # NOUVEAU : Bloc de désérialisation
        if event.rsi_series_json:
            try:
                rsi_series = pd.read_json(StringIO(event.rsi_series_json), orient="split", typ="series")
                rsi_series.index = pd.to_datetime(rsi_series.index, unit="ms", utc=True)
            except Exception as e:
                logger.error(f"Impossible de reconstruire la Series RSI pour {event.coin_id_symbol}: {e}")

        # La suite de la logique utilise la Series reconstruite
        if rsi_series is None or rsi_series.empty:
            self.service_bus.publish(
                "CoinProcessingFailed",
                {"coin_id_symbol": event.coin_id_symbol, "timeframe": event.timeframe},
                self.__class__.__name__
            )
            return

        key = (event.coin_id_symbol[0], event.coin_id_symbol[1], event.timeframe)
        self.rsi_results[key] = rsi_series

        if event.coin_id_symbol[1].lower() == "btc":
            job.btc_rsi = rsi_series

        job.decrement_counter()

    def _handle_coin_processing_failed(self, event: CoinProcessingFailed):
        job = self.analysis_jobs.get(event.timeframe)
        if job:
            job.decrement_counter()

    def analyze_correlation(
            self, coin_id_symbol: Tuple[str, str], coin_rsi: pd.Series, btc_rsi: pd.Series, timeframe: str
    ):
        common_index_rsi = btc_rsi.index.intersection(coin_rsi.index)
        if len(common_index_rsi) < self.config.rsi_period:
            return

        correlation = coin_rsi.loc[common_index_rsi].corr(btc_rsi.loc[common_index_rsi])

        if pd.isna(correlation) or abs(correlation) < self.config.correlation_threshold:
            return

        market_cap = self.market_caps.get(coin_id_symbol[1].lower(), 0)
        low_cap_quartile = market_cap <= self.low_cap_threshold

        result = {
            "coin_id": coin_id_symbol[0],
            "coin_symbol": coin_id_symbol[1],
            "correlation": float(correlation),  # Conversion de np.float64 -> float
            "market_cap": market_cap,
            "low_cap_quartile": bool(low_cap_quartile),  # Conversion de np.bool_ -> bool
        }
        self.service_bus.publish("CorrelationAnalyzed", {"result": result, "timeframe": timeframe}, self.__class__.__name__)

    def _handle_correlation_analyzed(self, event: CorrelationAnalyzed):
        if event.result:
            self.results.append(event.result)

    def _handle_analysis_job_completed(self, event: AnalysisJobCompleted):
        with self._job_lock:
            self._job_completion_counter -= 1
            logger.info(f"Job pour {event.timeframe} terminé. Restants : {self._job_completion_counter}")
            if self._job_completion_counter <= 0:
                logger.info("Tous les jobs d'analyse sont terminés. Préparation des résultats finaux.")
                self.service_bus.publish(
                    "FinalResultsReady",
                    {"results": self.results, "weeks": self.config.weeks, "timeframes": self.config.timeframes},
                    self.__class__.__name__
                )

    def _handle_display_completed(self, _event: DisplayCompleted):
        logger.info("Affichage terminé. Déclenchement de l'arrêt du programme.")
        self.service_bus.publish("AllProcessingCompleted", AllProcessingCompleted(), self.__class__.__name__)
