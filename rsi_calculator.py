from io import StringIO
from typing import Optional, Tuple

import numpy as np
import pandas as pd
# noinspection PyPackageRequirements
from pubsub import QueueWorkerThread, ServiceBus

from events import AnalysisConfigurationProvided, CalculateRSIRequested, RSICalculated
from logger import logger


class RSICalculator(QueueWorkerThread):
    """Calcule le RSI pour une série de prix dans son propre thread."""

    def __init__(self, service_bus: Optional[ServiceBus] = None):
        super().__init__(service_bus=service_bus, name="RSICalculator")
        self.periods: Optional[int] = None
        self.session_guid: Optional[str] = None

    def setup_event_subscriptions(self) -> None:
        self.service_bus.subscribe("AnalysisConfigurationProvided", self._handle_configuration_provided)
        self.service_bus.subscribe("CalculateRSIRequested", self._handle_calculate_rsi_requested)

    def _handle_configuration_provided(self, event: AnalysisConfigurationProvided):
        self.session_guid = event.session_guid
        config: dict = event.config
        self.periods = config.get('rsi_period', 14)
        logger.info(f"RSICalculator a reçu la configuration pour la session {self.session_guid}.")

    def _handle_calculate_rsi_requested(self, event: CalculateRSIRequested):
        prices_series = None
        if event.prices_series_json:
            try:
                prices_series = pd.read_json(StringIO(event.prices_series_json), orient="split", typ="series")
                prices_series.index = pd.to_datetime(prices_series.index, unit="ms", utc=True)
            except Exception as e:
                logger.error(f"Impossible de reconstruire la Series de prix pour {event.coin_id_symbol}: {e}")

        # On passe la Series reconstruite (ou None) à la tâche de calcul.
        self.add_task("_calculate_rsi_task", event.coin_id_symbol, prices_series, event.timeframe)


    # def _calculate_rsi_task(self, coin_id_symbol: Tuple[str, str], data: Optional[pd.Series], timeframe: str) -> None:
    #     try:
    #         if self.periods is None:
    #             raise ValueError("La période RSI n'a pas été configurée.")
    #         if data is None or data.empty or len(data) < self.periods + 1:
    #             raise ValueError("Données insuffisantes pour calculer le RSI")
    #
    #         delta = data.astype(float).diff()
    #         gain = (delta.where(delta > 0, 0)).rolling(window=self.periods).mean()
    #         loss = (-delta.where(delta < 0, 0)).rolling(window=self.periods).mean()
    #         loss = loss.replace(0, np.nan)
    #         rs = gain / loss
    #         rsi = 100 - (100 / (1 + rs))
    #         rsi = rsi.dropna()
    #
    #         if self.service_bus:
    #             self.service_bus.publish(
    #                 "RSICalculated", {"coin_id_symbol": coin_id_symbol, "rsi": rsi, "timeframe": timeframe}
    #             )
    #     except Exception as e:
    #         logger.error(f"Erreur lors du calcul du RSI pour {coin_id_symbol}: {e}")
    #         if self.service_bus:
    #             self.service_bus.publish(
    #                 "RSICalculated", {"coin_id_symbol": coin_id_symbol, "rsi": None, "timeframe": timeframe}
    #             )

    def _calculate_rsi_task(self, coin_id_symbol: Tuple[str, str], data: Optional[pd.Series], timeframe: str) -> None:
        rsi_series = None
        try:
            if self.periods is None:
                raise ValueError("La période RSI n'a pas été configurée.")
            if data is None or data.empty or len(data) < self.periods + 1:
                raise ValueError("Données insuffisantes pour calculer le RSI")

            delta = data.astype(float).diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=self.periods).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=self.periods).mean()
            loss = loss.replace(0, np.nan)
            rs = gain / loss
            rsi_series = 100 - (100 / (1 + rs))
            rsi_series = rsi_series.dropna()

        except Exception as e:
            logger.error(f"Erreur lors du calcul du RSI pour {coin_id_symbol}: {e}")
            # rsi_series reste None en cas d'erreur

        if self.service_bus:
            rsi_json = rsi_series.to_json(orient="split") if rsi_series is not None and not rsi_series.empty else None
            event = RSICalculated(
                coin_id_symbol=coin_id_symbol,
                rsi_series_json=rsi_json,
                timeframe=timeframe
            )
            self.service_bus.publish("RSICalculated", event)
