from typing import Optional, Dict, Tuple

import numpy as np
import pandas as pd

from logger import logger
from service_bus import ServiceBus


class RSICalculator:
    """Calcule le RSI pour une série de prix, réactif aux événements du ServiceBus."""

    def __init__(self, periods: int = 14, service_bus: Optional[ServiceBus] = None):
        self.periods = periods
        self.service_bus = service_bus

        if self.service_bus:
            self.service_bus.subscribe("CalculateRSIRequested", self._handle_calculate_rsi_requested)

    def _handle_calculate_rsi_requested(self, payload: Dict):
        coin_id_symbol = payload.get('coin_id_symbol')
        prices_series = payload.get('prices_series')
        session_guid = payload.get('session_guid')
        self._calculate_rsi_task(coin_id_symbol, prices_series, session_guid)

    def _calculate_rsi_task(self, coin_id_symbol: Tuple[str, str], data: pd.Series,
                            session_guid: Optional[str]) -> None:
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
        except Exception as e:
            logger.error(f"Erreur lors du calcul du RSI pour {coin_id_symbol}: {e}")
            if self.service_bus:
                self.service_bus.publish("RSICalculated", {'coin_id_symbol': coin_id_symbol, 'rsi': None})
