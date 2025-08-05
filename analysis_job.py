import threading
from typing import Optional, Tuple, List

import pandas as pd

from logger import logger


class AnalysisJob:
    """Contient l'état d'une analyse pour un seul timeframe."""

    def __init__(self, timeframe: str, parent_analyzer):
        self.timeframe = timeframe
        self.analyzer = parent_analyzer
        self.btc_rsi: Optional[pd.Series] = None
        self.coins_to_process: List[Tuple[str, str]] = []
        self._processing_counter = 0
        self._counter_lock = threading.Lock()

    def set_coins_to_process(self, coins: List[Tuple[str, str]]):
        self.coins_to_process = [c for c in coins if c[1].lower() != 'btc']
        with self._counter_lock:
            self._processing_counter = len(self.coins_to_process) + 1

    def decrement_counter(self):
        with self._counter_lock:
            self._processing_counter -= 1
            logger.debug(f"Compteur pour {self.timeframe}: {self._processing_counter}")
            if self._processing_counter <= 0:
                logger.info(f"Tous les RSI pour le timeframe {self.timeframe} ont été traités. Lancement des corrélations.")
                self.start_correlation_analysis()

    def start_correlation_analysis(self):
        """Lance l'analyse de corrélation pour toutes les pièces traitées de ce job."""
        if self.btc_rsi is None:
            logger.error(f"Impossible de lancer l'analyse pour {self.timeframe}: RSI de BTC manquant.")
            self.analyzer.service_bus.publish("AnalysisJobCompleted", {'timeframe': self.timeframe})
            return

        for coin_id, symbol in self.coins_to_process:
            rsi_key = (coin_id, symbol, self.timeframe)
            coin_rsi = self.analyzer.rsi_results.get(rsi_key)
            if coin_rsi is not None:
                self.analyzer.analyze_correlation(
                    coin_id_symbol=(coin_id, symbol),
                    coin_rsi=coin_rsi,
                    btc_rsi=self.btc_rsi,
                    timeframe=self.timeframe
                )

        self.analyzer.service_bus.publish("AnalysisJobCompleted", {'timeframe': self.timeframe})
