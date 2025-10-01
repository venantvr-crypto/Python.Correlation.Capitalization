import threading
from io import StringIO
from typing import Dict, List, Optional, Tuple

import pandas as pd
# noinspection PyPackageRequirements
from pubsub import OrchestratorBase, ServiceBus, AllProcessingCompleted, WorkerFailed
from threadsafe_logger import sqlite_business_logger

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
    """Orchestrates RSI correlation analyses for multiple timeframes."""

    def __init__(self, config: AnalysisConfig, session_guid: str):
        self.config = config
        self.session_guid = session_guid

        url = self.config.pubsub_url
        service_bus = ServiceBus(url=url, consumer_name=self.__class__.__name__)
        super().__init__(service_bus, enable_status_page=True)

        self.db_manager = None
        self.data_fetcher = None
        self.rsi_calculator = None
        self.display_agent = None

        # Initialize to None for a clear initial state.
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
        """Creates and registers services managed by the orchestrator."""
        self.db_manager = DatabaseManager(service_bus=self.service_bus)
        self.data_fetcher = DataFetcher(service_bus=self.service_bus)
        self.rsi_calculator = RSICalculator(service_bus=self.service_bus)
        self.display_agent = DisplayAgent(service_bus=self.service_bus)

        self.services = [self.db_manager, self.data_fetcher, self.rsi_calculator, self.display_agent]

    def setup_event_subscriptions(self) -> None:
        """Subscribes orchestrator methods to service bus events."""
        self.service_bus.subscribe("RunAnalysisRequested", self._handle_run_analysis_requested)
        self.service_bus.subscribe("TopCoinsFetched", self._handle_top_coins_fetched)
        self.service_bus.subscribe("PrecisionDataFetched", self._handle_precision_data_fetched)
        self.service_bus.subscribe("HistoricalPricesFetched", self._handle_historical_prices_fetched)
        self.service_bus.subscribe("RSICalculated", self._handle_rsi_calculated)
        self.service_bus.subscribe("CorrelationAnalyzed", self._handle_correlation_analyzed)
        self.service_bus.subscribe("CoinProcessingFailed", self._handle_coin_processing_failed)
        self.service_bus.subscribe("AnalysisJobCompleted", self._handle_analysis_job_completed)
        self.service_bus.subscribe("DisplayCompleted", self._handle_display_completed)
        self.service_bus.subscribe("WorkerFailed", self._handle_worker_failed)

    def start_workflow(self) -> None:
        """Starts the analysis workflow."""
        config_payload = AnalysisConfigurationProvided(session_guid=self.session_guid, config=self.config)
        self.service_bus.publish("AnalysisConfigurationProvided", config_payload, self.__class__.__name__)
        self.service_bus.publish("RunAnalysisRequested", RunAnalysisRequested(), self.__class__.__name__)

    def _handle_run_analysis_requested(self, _event: RunAnalysisRequested):

        try:
            logger.info("Analysis started. Requesting initial data (top coins and precision).")

            if self.service_bus is not None:
                self.service_bus.publish("FetchPrecisionDataRequested", FetchPrecisionDataRequested(), self.__class__.__name__)

            if self.service_bus is not None:
                from events import FetchTopCoinsRequested

                self.service_bus.publish("FetchTopCoinsRequested", FetchTopCoinsRequested(n=self.config.top_n_coins), self.__class__.__name__)
        except Exception as e:
            error_msg = f"Error handling run analysis requested: {e}"
            logger.critical(error_msg, exc_info=True)
            self.service_bus.publish("AllProcessingCompleted", AllProcessingCompleted(), self.__class__.__name__)

    def _handle_precision_data_fetched(self, event: PrecisionDataFetched):

        try:
            self.precision_data = {item["symbol"]: item for item in event.precision_data}
            self._start_analysis_if_ready()
        except Exception as e:
            error_msg = f"Error handling precision data fetched: {e}"
            logger.critical(error_msg, exc_info=True)
            self.service_bus.publish("AllProcessingCompleted", AllProcessingCompleted(), self.__class__.__name__)

    def _handle_top_coins_fetched(self, event: TopCoinsFetched):

        try:
            self.coins = event.coins
            self._start_analysis_if_ready()
        except Exception as e:
            error_msg = f"Error handling top coins fetched: {e}"
            logger.critical(error_msg, exc_info=True)
            self.service_bus.publish("AllProcessingCompleted", AllProcessingCompleted(), self.__class__.__name__)

    def _start_analysis_if_ready(self):
        """
        Checks if both initial data streams have been RECEIVED (even if empty),
        then starts the analysis or stops cleanly.
        """

        try:
            # Check that variables are no longer None.
            # Using early return pattern to avoid race conditions
            if self._initial_data_loaded.is_set():
                return

            if self.coins is None or self.precision_data is None:
                logger.debug("Waiting for all initial data...")
                return

            # Handle the case where received data is empty.
            if not self.coins or not self.precision_data:
                logger.warning("No crypto or market pair found. Analysis cannot continue.")
                self._processing_completed.set()
                return

            self._initial_data_loaded.set()
            logger.info("Initial data (coins and precision) received. Starting processing.")

            usdc_base_symbols = {m["base_asset"] for m in self.precision_data.values() if m["quote_asset"] == "USDC"}
            original_coin_count = len(self.coins)
            self.coins = [c for c in self.coins if c.get("symbol", "").upper() in usdc_base_symbols]
            logger.info(f"Filtering cryptos: {original_coin_count} -> {len(self.coins)} with USDC pair.")

            for coin in self.coins:
                self.service_bus.publish("SingleCoinFetched", {"coin": coin}, self.__class__.__name__)

            self.market_caps = {c["symbol"].lower(): c.get("market_cap", 0) for c in self.coins}
            market_cap_values = [mc for mc in self.market_caps.values() if mc > 0]
            if market_cap_values:
                self.low_cap_threshold = pd.Series(market_cap_values).quantile(self.config.low_cap_percentile / 100)

            logger.info(
                f"Low capitalization threshold ({self.config.low_cap_percentile}th percentile): ${self.low_cap_threshold:,.2f}"
            )

            coins_to_process = [(c["id"], c["symbol"]) for c in self.coins]
            for timeframe, job in self.analysis_jobs.items():
                job.set_coins_to_process(coins_to_process)
                logger.info(f"Launching job for {timeframe} with {len(job.coins_to_process) + 1} pairs.")

                self.service_bus.publish(
                    "FetchHistoricalPricesRequested",
                    {"coin_id_symbol": ("bitcoin", "btc"), "weeks": self.config.weeks, "timeframe": timeframe},
                    self.__class__.__name__
                )

                for coin_id, symbol in job.coins_to_process:
                    sqlite_business_logger.log(self.__class__.__name__, f"FetchHistoricalPricesRequested pour {coin_id}")
                    self.service_bus.publish(
                        "FetchHistoricalPricesRequested",
                        {"coin_id_symbol": (coin_id, symbol), "weeks": self.config.weeks, "timeframe": timeframe},
                        self.__class__.__name__
                    )
        except Exception as e:
            logger.error(
                f"Critical error during analysis initialization: {e}. Stopping application.", exc_info=True
            )
            # Publier l'événement de fin pour éviter le blocage
            self.service_bus.publish("AllProcessingCompleted", AllProcessingCompleted(), self.__class__.__name__)

    def _handle_historical_prices_fetched(self, event: HistoricalPricesFetched):

        try:
            prices_df = self._deserialize_prices_dataframe(event.prices_df_json, event.coin_id_symbol)

            if prices_df is None or prices_df.empty:
                self.service_bus.publish(
                    "CoinProcessingFailed",
                    {"coin_id_symbol": event.coin_id_symbol, "timeframe": event.timeframe},
                    self.__class__.__name__
                )
                return

            close_series = prices_df["close"]
            # noinspection PyUnresolvedReferences
            series_json = close_series.to_json(orient="split")

            rsi_request_event = CalculateRSIRequested(
                coin_id_symbol=event.coin_id_symbol, prices_series_json=series_json, timeframe=event.timeframe
            )
            sqlite_business_logger.log(self.__class__.__name__, f"CalculateRSIRequested pour {event.coin_id_symbol}")
            self.service_bus.publish("CalculateRSIRequested", rsi_request_event, self.__class__.__name__)
        except Exception as e:
            error_msg = f"Error handling historical prices fetched: {e}"
            logger.critical(error_msg, exc_info=True)
            self.service_bus.publish(
                "CoinProcessingFailed",
                {"coin_id_symbol": event.coin_id_symbol, "timeframe": event.timeframe},
                self.__class__.__name__
            )

    # noinspection PyMethodMayBeStatic
    def _deserialize_prices_dataframe(self, prices_json: Optional[str], coin_id_symbol: Tuple[str, str]) -> Optional[pd.DataFrame]:

        if not prices_json:
            return None

        try:
            prices_df = pd.read_json(StringIO(prices_json), orient="split")
            prices_df.index = pd.to_datetime(prices_df.index, unit="ms", utc=True)
            return prices_df
        except Exception as e:
            error_msg = f"Impossible de reconstruire le DataFrame des prix pour {coin_id_symbol}: {e}"
            logger.error(error_msg)
            return None

    def _handle_rsi_calculated(self, event: RSICalculated):

        try:
            logger.debug(f"[CORRELATION-DEBUG] Received RSI for {event.coin_id_symbol} on {event.timeframe}")
            job = self.analysis_jobs.get(event.timeframe)

            if not job:
                logger.warning(f"[CORRELATION-DEBUG] No job found for timeframe {event.timeframe}")
                return

            rsi_series = self._deserialize_rsi_series(event.rsi_series_json, event.coin_id_symbol)

            if rsi_series is None or rsi_series.empty:
                logger.debug(f"[CORRELATION-DEBUG] RSI series is None or empty for {event.coin_id_symbol}")

                if self.service_bus is not None:
                    self.service_bus.publish(
                        "CoinProcessingFailed",
                        {"coin_id_symbol": event.coin_id_symbol, "timeframe": event.timeframe},
                        self.__class__.__name__
                    )
                return

            key = (event.coin_id_symbol[0], event.coin_id_symbol[1], event.timeframe)
            self.rsi_results[key] = rsi_series
            logger.debug(f"[CORRELATION-DEBUG] Stored RSI for {event.coin_id_symbol}")

            if event.coin_id_symbol[1].lower() == "btc":
                job.btc_rsi = rsi_series
                logger.debug(f"[CORRELATION-DEBUG] Set BTC RSI for {event.timeframe}")

            logger.debug(f"[CORRELATION-DEBUG] About to decrement counter for {event.timeframe}")
            job.decrement_counter(coin_id_symbol=event.coin_id_symbol)
        except Exception as e:
            error_msg = f"Error handling RSI calculated: {e}"
            logger.critical(error_msg, exc_info=True)

            if self.service_bus is not None:
                self.service_bus.publish(
                    "CoinProcessingFailed",
                    {"coin_id_symbol": event.coin_id_symbol, "timeframe": event.timeframe},
                    self.__class__.__name__
                )

    # noinspection PyMethodMayBeStatic
    def _deserialize_rsi_series(self, rsi_json: Optional[str], coin_id_symbol: Tuple[str, str]) -> Optional[pd.Series]:

        if not rsi_json:
            return None

        try:
            rsi_series = pd.read_json(StringIO(rsi_json), orient="split", typ="series")
            rsi_series.index = pd.to_datetime(rsi_series.index, unit="ms", utc=True)
            return rsi_series
        except Exception as e:
            error_msg = f"Impossible de reconstruire la Series RSI pour {coin_id_symbol}: {e}"
            logger.error(error_msg)
            return None

    def _handle_coin_processing_failed(self, event: CoinProcessingFailed):

        try:
            job = self.analysis_jobs.get(event.timeframe)
            if job:
                job.decrement_counter(coin_id_symbol=event.coin_id_symbol)
        except Exception as e:
            error_msg = f"Error handling coin processing failed: {e}"
            logger.critical(error_msg, exc_info=True)

    def analyze_correlation(
            self, coin_id_symbol: Tuple[str, str], coin_rsi: pd.Series, btc_rsi: pd.Series, timeframe: str
    ):
        common_index_rsi = btc_rsi.index.intersection(coin_rsi.index)

        if len(common_index_rsi) < self.config.rsi_period:
            logger.debug(f"[CORRELATION-DEBUG] Skipping {coin_id_symbol}: insufficient data ({len(common_index_rsi)} < {self.config.rsi_period})")
            return

        correlation = coin_rsi.loc[common_index_rsi].corr(btc_rsi.loc[common_index_rsi])

        if pd.isna(correlation) or abs(correlation) < self.config.correlation_threshold:
            logger.debug(f"[CORRELATION-DEBUG] Skipping {coin_id_symbol}: correlation {correlation} below threshold {self.config.correlation_threshold}")
            return

        logger.info(f"[CORRELATION-DEBUG] Found valid correlation for {coin_id_symbol}: {correlation}")
        market_cap = self.market_caps.get(coin_id_symbol[1].lower(), 0)
        low_cap_quartile = market_cap <= self.low_cap_threshold

        result = {
            "coin_id": coin_id_symbol[0],
            "coin_symbol": coin_id_symbol[1],
            "correlation": float(correlation),  # Conversion de np.float64 -> float
            "market_cap": market_cap,
            "low_cap_quartile": bool(low_cap_quartile),  # Conversion de np.bool_ -> bool
            "timeframe": timeframe,
        }
        sqlite_business_logger.log(self.__class__.__name__, f"CorrelationAnalyzed avec {result}")
        self.service_bus.publish("CorrelationAnalyzed", {"result": result, "timeframe": timeframe}, self.__class__.__name__)

    def _handle_correlation_analyzed(self, event: CorrelationAnalyzed):

        try:
            if event.result:
                self.results.append(event.result)
        except Exception as e:
            error_msg = f"Error handling correlation analyzed: {e}"
            logger.critical(error_msg, exc_info=True)

    def _handle_analysis_job_completed(self, event: AnalysisJobCompleted):

        try:
            with self._job_lock:
                self._job_completion_counter -= 1
                logger.info(f"Job for {event.timeframe} completed. Remaining: {self._job_completion_counter}")
                if self._job_completion_counter <= 0:
                    logger.info("All analysis jobs completed. Preparing final results.")
                    self.service_bus.publish(
                        "FinalResultsReady",
                        {"results": self.results, "weeks": self.config.weeks, "timeframes": self.config.timeframes},
                        self.__class__.__name__
                    )
        except Exception as e:
            error_msg = f"Error handling analysis job completed: {e}"
            logger.critical(error_msg, exc_info=True)
            self.service_bus.publish("AllProcessingCompleted", AllProcessingCompleted(), self.__class__.__name__)

    def _handle_worker_failed(self, event: WorkerFailed):

        try:
            logger.critical(
                f"Worker '{event.worker_name}' reported a critical failure: {event.reason}. "
                "Triggering general application shutdown."
            )
            # Publish the end event to unblock the main loop and stop everything cleanly
            self.service_bus.publish("AllProcessingCompleted", AllProcessingCompleted(), self.__class__.__name__)
        except Exception as e:
            error_msg = f"Error handling worker failed: {e}"
            logger.critical(error_msg, exc_info=True)
            self.service_bus.publish("AllProcessingCompleted", AllProcessingCompleted(), self.__class__.__name__)

    def _handle_display_completed(self, _event: DisplayCompleted):

        try:
            logger.info("Display completed. Triggering program shutdown.")
            self.service_bus.publish("AllProcessingCompleted", AllProcessingCompleted(), self.__class__.__name__)
        except Exception as e:
            error_msg = f"Error handling display completed: {e}"
            logger.critical(error_msg, exc_info=True)
            self.service_bus.publish("AllProcessingCompleted", AllProcessingCompleted(), self.__class__.__name__)

    def _stop_services(self) -> None:
        """Override to wait for DatabaseManager to finish processing before stopping."""
        logger.info("Waiting for DatabaseManager to finish processing all tasks...")

        if self.db_manager and self.db_manager.is_alive():
            if not self.db_manager.wait_for_queue_completion(timeout=30.0):
                logger.warning("DatabaseManager did not complete all tasks within timeout.")
            else:
                logger.info("DatabaseManager has processed all tasks successfully.")

        # Call parent implementation to stop all services
        super()._stop_services()
