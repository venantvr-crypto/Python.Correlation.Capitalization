import threading
from typing import List, Optional, Tuple

import pandas as pd
from threadsafe_logger import sqlite_business_logger

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

    def set_coins_to_process(self, coins: List[Tuple[str, str]]):
        self.coins_to_process = [c for c in coins if c[1].lower() != "btc"]
        with self._counter_lock:
            self._processing_counter = len(self.coins_to_process) + 1

    def decrement_counter(self):
        with self._counter_lock:
            self._processing_counter -= 1
            logger.debug(f"Counter for {self.timeframe}: {self._processing_counter}")
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
            self.analyzer.service_bus.publish("AnalysisJobCompleted", {"timeframe": self.timeframe}, self.__class__.__name__)
        except Exception as e:
            logger.error(f"Critical error in correlation analysis for {self.timeframe}: {e}", exc_info=True)
            # Ensure job completion is published even on error to prevent deadlock
            self.analyzer.service_bus.publish("AnalysisJobCompleted", {"timeframe": self.timeframe}, self.__class__.__name__)
