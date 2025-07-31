# File: rsi_calculator.py
import queue
import threading
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from logger import logger
from service_bus import ServiceBus


class RSICalculator(threading.Thread):
    """Calcule le RSI pour une série de prix dans son propre thread."""

    def __init__(self, periods: int = 14, service_bus: Optional[ServiceBus] = None):
        super().__init__()
        self.periods = periods
        self.service_bus = service_bus
        self.work_queue = queue.Queue()
        self._running = True

    def run(self):
        """Boucle d'exécution du thread."""
        logger.info("Thread RSICalculator démarré.")
        while self._running:
            task = self.work_queue.get()
            if task is None:
                self._running = False
                break
            coin_id_symbol, prices_series, session_guid = task
            self._calculate_rsi_task(coin_id_symbol, prices_series, session_guid)
            self.work_queue.task_done()
        logger.info("Thread RSICalculator arrêté.")

    def stop(self):
        """Arrête le thread en toute sécurité."""
        self.work_queue.put(None)
        self.join()

    def calculate(self, coin_id_symbol: Tuple[str, str], prices_series: pd.Series, session_guid: Optional[str]) -> None:
        self.work_queue.put((coin_id_symbol, prices_series, session_guid))

    def _calculate_rsi_task(self, coin_id_symbol: Tuple[str, str], data: pd.Series, session_guid: Optional[str]) -> \
            Optional[pd.Series]:
        """Tâche interne pour calculer le RSI en excluant la dernière donnée."""
        try:
            if len(data) < self.periods + 1:
                raise ValueError("Données insuffisantes pour calculer le RSI")
            data = data[:-1]
            delta = data.diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=self.periods).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=self.periods).mean()
            loss = loss.replace(0, np.nan)
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            if self.service_bus:
                payload = {'coin_id_symbol': coin_id_symbol, 'rsi': rsi, 'session_guid': session_guid}
                self.service_bus.publish("RSICalculated", payload)
            return rsi
        except Exception as e:
            logger.error(f"Erreur lors du calcul du RSI pour {coin_id_symbol}: {e}")
            return None
