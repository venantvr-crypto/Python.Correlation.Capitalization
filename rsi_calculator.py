import queue
import threading
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from events import CalculateRSIRequested, AnalysisConfigurationProvided
from logger import logger
from service_bus import ServiceBus


class RSICalculator(threading.Thread):
    """Calcule le RSI pour une série de prix dans son propre thread."""

    def __init__(self, service_bus: Optional[ServiceBus] = None):
        super().__init__()
        self.periods: Optional[int] = None
        self.service_bus = service_bus
        self.session_guid: Optional[str] = None
        self.work_queue = queue.Queue()
        self._running = True

        if self.service_bus:
            self.service_bus.subscribe("AnalysisConfigurationProvided", self._handle_configuration_provided)
            self.service_bus.subscribe("CalculateRSIRequested", self._handle_calculate_rsi_requested)

    def _handle_configuration_provided(self, event: AnalysisConfigurationProvided):
        """Stocke la configuration de la session."""
        self.session_guid = event.session_guid
        self.periods = event.config.rsi_period
        logger.info(f"RSICalculator a reçu la configuration pour la session {self.session_guid}.")

    def _handle_calculate_rsi_requested(self, event: CalculateRSIRequested):
        self.work_queue.put(('_calculate_rsi_task', (event.coin_id_symbol, event.prices_series, event.timeframe)))

    def run(self):
        logger.info("Thread RSICalculator démarré.")
        while self._running:
            try:
                task = self.work_queue.get(timeout=1)
                if task is None:
                    continue

                method_name, args = task
                method = getattr(self, method_name)
                method(*args)
                self.work_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Erreur d'exécution de la tâche dans RSICalculator: {e}")
        logger.info("Thread RSICalculator arrêté.")

    def stop(self):
        self._running = False
        self.work_queue.put(None)
        self.join()

    def _calculate_rsi_task(self, coin_id_symbol: Tuple[str, str], data: pd.Series, timeframe: str) -> None:
        try:
            if self.periods is None:
                raise ValueError("La période RSI n'a pas été configurée.")
            if data is None or data.empty or len(data) < self.periods + 1:
                raise ValueError("Données insuffisantes pour calculer le RSI")

            # Calculer le RSI sans supprimer la dernière valeur
            # La suppression pourrait être liée à un problème de synchronisation, mais
            # elle n'est pas nécessaire pour le calcul du RSI
            delta = data.diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=self.periods).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=self.periods).mean()

            # Éviter la division par zéro
            loss = loss.replace(0, np.nan)
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))

            # Retirer les valeurs NaN du début (les premières périodes n'ont pas assez de données)
            rsi = rsi.dropna()

            if self.service_bus:
                self.service_bus.publish("RSICalculated",
                                         {'coin_id_symbol': coin_id_symbol, 'rsi': rsi, 'timeframe': timeframe})
        except Exception as e:
            logger.error(f"Erreur lors du calcul du RSI pour {coin_id_symbol}: {e}")
            if self.service_bus:
                self.service_bus.publish("RSICalculated",
                                         {'coin_id_symbol': coin_id_symbol, 'rsi': None, 'timeframe': timeframe})
