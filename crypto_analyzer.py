import threading

import pandas as pd
from pydantic import BaseModel

# noinspection PyPackageRequirements
from python_pubsub_client import AllProcessingCompleted, OrchestratorBase, ServiceBus, WorkerFailed
from python_threadsafe_logger import sqlite_business_logger

from agents.data_fetcher import DataFetcher
from agents.database_manager import DatabaseManager
from agents.display_agent import DisplayAgent
from agents.rsi_calculator import RSICalculator
from analysis_job import AnalysisJob
from configuration import AnalysisConfig
from events import (
    AnalysisConfigurationProvided,
    AnalysisJobCompleted,
    CalculateRSIRequested,
    CoinProcessingFailed,
    CoinRef,
    CorrelationAnalyzed,
    DisplayCompleted,
    FetchHistoricalPricesRequested,
    FetchPrecisionDataRequested,
    FetchTopCoinsRequested,
    FinalResultsReady,
    HistoricalPricesFetched,
    PrecisionDataFetched,
    RSICalculated,
    RunAnalysisRequested,
    SingleCoinFetched,
    TopCoinsFetched,
)
from logger import logger
from serialization import frame_from_json, frame_to_json, series_from_json

BTC: CoinRef = ("bitcoin", "btc")
QUOTE_ASSET = "USDC"


class CryptoAnalyzer(OrchestratorBase):
    """Orchestrates RSI correlation analyses for multiple timeframes."""

    def __init__(self, config: AnalysisConfig, session_guid: str):
        self.config = config
        self.session_guid = session_guid

        self.db_manager: DatabaseManager | None = None
        self.data_fetcher: DataFetcher | None = None
        self.rsi_calculator: RSICalculator | None = None
        self.display_agent: DisplayAgent | None = None

        # None means "not received yet", as opposed to "received but empty".
        self.coins: list[dict] | None = None
        self.precision_data: dict[str, dict] | None = None

        self.market_caps: dict[str, float] = {}
        self.low_cap_threshold: float = float("inf")
        self.results: list[dict] = []
        self.rsi_results: dict[tuple[str, str, str], pd.Series] = {}
        self.analysis_jobs: dict[str, AnalysisJob] = {tf: AnalysisJob(tf, self) for tf in self.config.timeframes}

        self._job_completion_counter = len(self.analysis_jobs)
        self._job_lock = threading.Lock()
        self._initial_data_lock = threading.Lock()
        self._initial_data_loaded = threading.Event()

        service_bus = ServiceBus(url=self.config.pubsub_url, consumer_name=self.__class__.__name__)
        super().__init__(service_bus, enable_status_page=True)

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
        self._publish(AnalysisConfigurationProvided(session_guid=self.session_guid, config=self.config))
        self._publish(RunAnalysisRequested())

    # --- Publishing helpers ---

    def _publish(self, event: BaseModel) -> None:
        """Publishes an event under the name of its class."""
        self.service_bus.publish(type(event).__name__, event, self.__class__.__name__)

    def _shutdown(self) -> None:
        """Unblocks the main loop so that everything stops cleanly."""
        self._publish(AllProcessingCompleted())

    def _fail_coin(self, coin_id_symbol: CoinRef, timeframe: str) -> None:
        self._publish(CoinProcessingFailed(coin_id_symbol=coin_id_symbol, timeframe=timeframe))

    # --- Initial data ---

    def _handle_run_analysis_requested(self, _event: RunAnalysisRequested) -> None:

        try:
            logger.info("Analysis started. Requesting initial data (top coins and precision).")
            self._publish(FetchPrecisionDataRequested())
            self._publish(FetchTopCoinsRequested(n=self.config.top_n_coins))
        except Exception as e:
            logger.critical(f"Error handling run analysis requested: {e}", exc_info=True)
            self._shutdown()

    def _handle_precision_data_fetched(self, event: PrecisionDataFetched) -> None:

        try:
            self.precision_data = {item["symbol"]: item for item in event.precision_data}
            self._start_analysis_if_ready()
        except Exception as e:
            logger.critical(f"Error handling precision data fetched: {e}", exc_info=True)
            self._shutdown()

    def _handle_top_coins_fetched(self, event: TopCoinsFetched) -> None:

        try:
            self.coins = event.coins
            self._start_analysis_if_ready()
        except Exception as e:
            logger.critical(f"Error handling top coins fetched: {e}", exc_info=True)
            self._shutdown()

    def _start_analysis_if_ready(self) -> None:
        """
        Checks if both initial data streams have been RECEIVED (even if empty),
        then starts the analysis or stops cleanly.
        """

        # Both initial events may arrive concurrently: the lock guarantees a single start.
        with self._initial_data_lock:
            if self._initial_data_loaded.is_set():
                return

            if self.coins is None or self.precision_data is None:
                logger.debug("Waiting for all initial data...")
                return

            self._initial_data_loaded.set()

        try:
            if not self.coins or not self.precision_data:
                logger.warning("No crypto or market pair found. Analysis cannot continue.")
                self._processing_completed.set()
                return

            logger.info("Initial data (coins and precision) received. Starting processing.")
            self._launch_jobs()
        except Exception as e:
            logger.error(f"Critical error during analysis initialization: {e}. Stopping application.", exc_info=True)
            # Publier l'événement de fin pour éviter le blocage
            self._shutdown()

    def _launch_jobs(self) -> None:
        quoted_base_symbols = {m["base_asset"] for m in self.precision_data.values() if m["quote_asset"] == QUOTE_ASSET}
        original_coin_count = len(self.coins)
        self.coins = [c for c in self.coins if c.get("symbol", "").upper() in quoted_base_symbols]
        logger.info(f"Filtering cryptos: {original_coin_count} -> {len(self.coins)} with {QUOTE_ASSET} pair.")

        for coin in self.coins:
            self._publish(SingleCoinFetched(coin=coin))

        # CoinGecko may return a null market cap.
        self.market_caps = {c["symbol"].lower(): c.get("market_cap") or 0 for c in self.coins}
        market_cap_values = [mc for mc in self.market_caps.values() if mc > 0]

        if market_cap_values:
            self.low_cap_threshold = float(pd.Series(market_cap_values).quantile(self.config.low_cap_percentile / 100))

        logger.info(f"Low capitalization threshold ({self.config.low_cap_percentile}th percentile): ${self.low_cap_threshold:,.2f}")

        coins_to_process = [(c["id"], c["symbol"]) for c in self.coins]

        for timeframe, job in self.analysis_jobs.items():
            job.set_coins_to_process(coins_to_process)
            logger.info(f"Launching job for {timeframe} with {len(job.coins_to_process) + 1} pairs.")

            for coin_id_symbol in [BTC, *job.coins_to_process]:
                sqlite_business_logger.log(self.__class__.__name__, f"FetchHistoricalPricesRequested pour {coin_id_symbol[0]}")
                self._publish(FetchHistoricalPricesRequested(coin_id_symbol=coin_id_symbol, weeks=self.config.weeks, timeframe=timeframe))

    # --- Per-coin pipeline ---

    def _handle_historical_prices_fetched(self, event: HistoricalPricesFetched) -> None:

        try:
            prices_df = frame_from_json(event.prices_df_json) if event.prices_df_json else None

            if prices_df is None or prices_df.empty:
                self._fail_coin(event.coin_id_symbol, event.timeframe)
                return

            sqlite_business_logger.log(self.__class__.__name__, f"CalculateRSIRequested pour {event.coin_id_symbol}")
            self._publish(
                CalculateRSIRequested(
                    coin_id_symbol=event.coin_id_symbol, prices_series_json=frame_to_json(prices_df["close"]), timeframe=event.timeframe
                )
            )
        except Exception as e:
            logger.error(f"Error handling historical prices fetched for {event.coin_id_symbol}: {e}", exc_info=True)
            self._fail_coin(event.coin_id_symbol, event.timeframe)

    def _handle_rsi_calculated(self, event: RSICalculated) -> None:
        job = self.analysis_jobs.get(event.timeframe)

        if not job:
            logger.warning(f"[CORRELATION-DEBUG] No job found for timeframe {event.timeframe}")
            return

        try:
            rsi_series = series_from_json(event.rsi_series_json) if event.rsi_series_json else None

            if rsi_series is None or rsi_series.empty:
                logger.debug(f"[CORRELATION-DEBUG] RSI series is None or empty for {event.coin_id_symbol}")
                self._fail_coin(event.coin_id_symbol, event.timeframe)
                return

            coin_id, symbol = event.coin_id_symbol
            self.rsi_results[(coin_id, symbol, event.timeframe)] = rsi_series

            if symbol.lower() == "btc":
                job.btc_rsi = rsi_series

            job.decrement_counter(coin_id_symbol=event.coin_id_symbol)
        except Exception as e:
            logger.error(f"Error handling RSI calculated for {event.coin_id_symbol}: {e}", exc_info=True)
            self._fail_coin(event.coin_id_symbol, event.timeframe)

    def _handle_coin_processing_failed(self, event: CoinProcessingFailed) -> None:
        job = self.analysis_jobs.get(event.timeframe)

        if job:
            job.decrement_counter(coin_id_symbol=event.coin_id_symbol)

    def analyze_correlation(self, coin_id_symbol: CoinRef, coin_rsi: pd.Series, btc_rsi: pd.Series, timeframe: str) -> None:
        common_index = btc_rsi.index.intersection(coin_rsi.index)

        if len(common_index) < self.config.rsi_period:
            logger.debug(f"[CORRELATION-DEBUG] Skipping {coin_id_symbol}: insufficient data ({len(common_index)} < {self.config.rsi_period})")
            return

        correlation = coin_rsi.loc[common_index].corr(btc_rsi.loc[common_index])

        if pd.isna(correlation) or abs(correlation) < self.config.correlation_threshold:
            logger.debug(f"[CORRELATION-DEBUG] Skipping {coin_id_symbol}: correlation {correlation} below threshold {self.config.correlation_threshold}")
            return

        logger.info(f"Found valid correlation for {coin_id_symbol}: {correlation:.3f}")
        coin_id, symbol = coin_id_symbol
        market_cap = self.market_caps.get(symbol.lower(), 0)

        result = {
            "coin_id": coin_id,
            "coin_symbol": symbol,
            "correlation": float(correlation),  # Conversion de np.float64 -> float
            "market_cap": market_cap,
            "low_cap_quartile": bool(market_cap <= self.low_cap_threshold),  # Conversion de np.bool_ -> bool
            "timeframe": timeframe,
        }
        sqlite_business_logger.log(self.__class__.__name__, f"CorrelationAnalyzed avec {result}")
        self._publish(CorrelationAnalyzed(result=result, timeframe=timeframe))

    # --- Aggregation and shutdown ---

    def _handle_correlation_analyzed(self, event: CorrelationAnalyzed) -> None:

        if event.result:
            self.results.append(event.result)

    def _handle_analysis_job_completed(self, event: AnalysisJobCompleted) -> None:

        try:
            with self._job_lock:
                self._job_completion_counter -= 1
                remaining = self._job_completion_counter

            logger.info(f"Job for {event.timeframe} completed. Remaining: {remaining}")

            if remaining == 0:
                logger.info("All analysis jobs completed. Preparing final results.")
                self._publish(FinalResultsReady(results=self.results, weeks=self.config.weeks, timeframes=self.config.timeframes))
        except Exception as e:
            logger.critical(f"Error handling analysis job completed: {e}", exc_info=True)
            self._shutdown()

    def _handle_worker_failed(self, event: WorkerFailed) -> None:
        logger.critical(f"Worker '{event.worker_name}' reported a critical failure: {event.reason}. Triggering general application shutdown.")
        self._shutdown()

    def _handle_display_completed(self, _event: DisplayCompleted) -> None:
        logger.info("Display completed. Triggering program shutdown.")
        self._shutdown()

    def _stop_services(self) -> None:
        """Override to wait for DatabaseManager to finish processing before stopping."""
        logger.info("Waiting for DatabaseManager to finish processing all tasks...")

        if self.db_manager and self.db_manager.is_alive():
            if self.db_manager.wait_for_queue_completion(timeout=30.0):
                logger.info("DatabaseManager has processed all tasks successfully.")
            else:
                logger.warning("DatabaseManager did not complete all tasks within timeout.")

        # Call parent implementation to stop all services
        super()._stop_services()
