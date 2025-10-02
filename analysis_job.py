import threading
from typing import List, Optional, Tuple

import pandas as pd
# noinspection PyPackageRequirements
from python_pubsub_client import IdempotencyTracker
from async_threadsafe_logger import sqlite_business_logger

from logger import logger


class AnalysisJob:
    """Contains the state of an analysis for a single timeframe."""

    def __init__(self, timeframe: str, parent_analyzer):
        self.timeframe = timeframe
        self.analyzer = parent_analyzer
        self.btc_rsi: Optional[pd.Series] = None
        self.coins_to_process: List[Tuple[str, str]] = []
        self._processing_counter = 0
        self._counter_lock = threading.Lock()
        self._idempotency_tracker = IdempotencyTracker(maxlen=1000)

    def set_coins_to_process(self, coins: List[Tuple[str, str]]):
        self.coins_to_process = [c for c in coins if c[1].lower() != "btc"]
        with self._counter_lock:
            self._processing_counter = len(self.coins_to_process) + 1

    def decrement_counter(self, coin_id_symbol: Optional[Tuple[str, str]] = None):
        """
        Decrement the processing counter for this job.

        Args:
            coin_id_symbol: Optional coin identifier for idempotency tracking
        """
        # Create event data for idempotency check
        event_data = {
            "timeframe": self.timeframe,
            "coin_id_symbol": coin_id_symbol,
            "event_type": "decrement"
        }

        # Check if we've already processed this decrement

        if self._idempotency_tracker.is_duplicate(event_data):
            logger.debug(
                f"[IDEMPOTENCY] Duplicate decrement detected for {coin_id_symbol} on {self.timeframe}. Skipping."
            )
            return

        # Mark as processed and proceed with decrement
        self._idempotency_tracker.mark_processed(event_data)

        with self._counter_lock:
            self._processing_counter -= 1
            logger.debug(f"[CORRELATION-DEBUG] Counter for {self.timeframe}: {self._processing_counter}")

            if self._processing_counter <= 0:
                logger.info(
                    f"All RSI for timeframe {self.timeframe} have been processed. Starting correlations."
                )
                self.start_correlation_analysis()

    def start_correlation_analysis(self):
        """Starts correlation analysis for all processed coins in this job."""

        try:
            if self.btc_rsi is None:
                logger.error(f"Cannot start analysis for {self.timeframe}: BTC RSI missing.")

                if self.analyzer.service_bus is not None:
                    self.analyzer.service_bus.publish("AnalysisJobCompleted", {"timeframe": self.timeframe}, self.__class__.__name__)

                return

            for coin_id, symbol in self.coins_to_process:
                try:
                    rsi_key = (coin_id, symbol, self.timeframe)
                    coin_rsi = self.analyzer.rsi_results.get(rsi_key)

                    if coin_rsi is not None:
                        self.analyzer.analyze_correlation(
                            coin_id_symbol=(coin_id, symbol), coin_rsi=coin_rsi, btc_rsi=self.btc_rsi, timeframe=self.timeframe
                        )
                except Exception as e:
                    logger.error(f"Error analyzing correlation for {coin_id}/{symbol} on {self.timeframe}: {e}", exc_info=True)
                    # Continue with next coin instead of stopping

            sqlite_business_logger.log(self.__class__.__name__, f"AnalysisJobCompleted for {self.timeframe}")

            if self.analyzer.service_bus is not None:
                self.analyzer.service_bus.publish("AnalysisJobCompleted", {"timeframe": self.timeframe}, self.__class__.__name__)
        except Exception as e:
            logger.error(f"Critical error in correlation analysis for {self.timeframe}: {e}", exc_info=True)
            # Ensure job completion is published even on error to prevent deadlock

            if self.analyzer.service_bus is not None:
                self.analyzer.service_bus.publish("AnalysisJobCompleted", {"timeframe": self.timeframe}, self.__class__.__name__)
