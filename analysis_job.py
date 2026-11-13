import threading
from typing import TYPE_CHECKING

import pandas as pd

# noinspection PyPackageRequirements
from python_pubsub_client import IdempotencyTracker
from python_threadsafe_logger import sqlite_business_logger

from events import AnalysisJobCompleted, CoinRef
from logger import logger

if TYPE_CHECKING:
    from crypto_analyzer import CryptoAnalyzer


class AnalysisJob:
    """Contains the state of an analysis for a single timeframe."""

    def __init__(self, timeframe: str, parent_analyzer: "CryptoAnalyzer"):
        self.timeframe = timeframe
        self.analyzer = parent_analyzer
        self.btc_rsi: pd.Series | None = None
        self.coins_to_process: list[CoinRef] = []
        self._processing_counter = 0
        self._completed = False
        self._counter_lock = threading.Lock()
        self._idempotency_tracker = IdempotencyTracker(maxlen=1000)

    def set_coins_to_process(self, coins: list[CoinRef]) -> None:
        self.coins_to_process = [c for c in coins if c[1].lower() != "btc"]
        with self._counter_lock:
            # +1 for BTC, which is always fetched as the reference series.
            self._processing_counter = len(self.coins_to_process) + 1
            # The tracker must remember every coin of the job, otherwise late duplicates slip through.
            self._idempotency_tracker = IdempotencyTracker(maxlen=max(1000, self._processing_counter))

    def decrement_counter(self, coin_id_symbol: CoinRef | None = None) -> None:
        """Decrements the processing counter once per coin; starts correlations when it reaches zero."""
        event_data = {"timeframe": self.timeframe, "coin_id_symbol": coin_id_symbol, "event_type": "decrement"}

        with self._counter_lock:
            if self._idempotency_tracker.is_duplicate(event_data):
                logger.debug(f"[IDEMPOTENCY] Duplicate decrement detected for {coin_id_symbol} on {self.timeframe}. Skipping.")
                return

            self._idempotency_tracker.mark_processed(event_data)
            self._processing_counter -= 1
            logger.debug(f"[CORRELATION-DEBUG] Counter for {self.timeframe}: {self._processing_counter}")

            if self._processing_counter > 0 or self._completed:
                return

            self._completed = True

        logger.info(f"All RSI for timeframe {self.timeframe} have been processed. Starting correlations.")
        self.start_correlation_analysis()

    def start_correlation_analysis(self) -> None:
        """Starts correlation analysis for all processed coins in this job."""

        try:
            if self.btc_rsi is None:
                logger.error(f"Cannot start analysis for {self.timeframe}: BTC RSI missing.")
                return

            for coin_id, symbol in self.coins_to_process:
                coin_rsi = self.analyzer.rsi_results.get((coin_id, symbol, self.timeframe))

                if coin_rsi is None:
                    continue

                try:
                    self.analyzer.analyze_correlation(
                        coin_id_symbol=(coin_id, symbol), coin_rsi=coin_rsi, btc_rsi=self.btc_rsi, timeframe=self.timeframe
                    )
                except Exception as e:
                    # Continue with next coin instead of stopping
                    logger.error(f"Error analyzing correlation for {coin_id}/{symbol} on {self.timeframe}: {e}", exc_info=True)

            sqlite_business_logger.log(self.__class__.__name__, f"AnalysisJobCompleted for {self.timeframe}")
        except Exception as e:
            logger.error(f"Critical error in correlation analysis for {self.timeframe}: {e}", exc_info=True)
        finally:
            # Always publish job completion, even on error, to prevent a deadlock.
            self.analyzer.service_bus.publish("AnalysisJobCompleted", AnalysisJobCompleted(timeframe=self.timeframe), self.__class__.__name__)
