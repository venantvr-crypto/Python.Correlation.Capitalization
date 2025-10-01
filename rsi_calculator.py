from io import StringIO
from typing import Optional, Tuple

import numpy as np
import pandas as pd
# noinspection PyPackageRequirements
from pubsub import QueueWorkerThread, ServiceBus
from threadsafe_logger import sqlite_business_logger

from events import AnalysisConfigurationProvided, CalculateRSIRequested, RSICalculated
from logger import logger


class RSICalculator(QueueWorkerThread):
    """Calculates RSI for a price series in its own thread."""

    def __init__(self, service_bus: Optional[ServiceBus] = None):
        super().__init__(service_bus=service_bus, name="RSICalculator")
        self.periods: Optional[int] = None
        self.session_guid: Optional[str] = None

    def setup_event_subscriptions(self) -> None:
        self.service_bus.subscribe("AnalysisConfigurationProvided", self._handle_configuration_provided)
        self.service_bus.subscribe("CalculateRSIRequested", self._handle_calculate_rsi_requested)

    def _handle_configuration_provided(self, event: AnalysisConfigurationProvided):
        try:
            self.session_guid = event.session_guid
            config = event.config
            self.periods = config.rsi_period
            logger.info(f"RSICalculator received configuration for session {self.session_guid}.")
        except Exception as e:
            error_msg = f"Error handling configuration provided: {e}"
            logger.critical(error_msg, exc_info=True)

    def _handle_calculate_rsi_requested(self, event: CalculateRSIRequested):
        try:
            prices_series = self._deserialize_prices_series(event.prices_series_json, event.coin_id_symbol)
            self.add_task("_calculate_rsi_task", event.coin_id_symbol, prices_series, event.timeframe)
        except Exception as e:
            error_msg = f"Error handling calculate RSI requested: {e}"
            logger.critical(error_msg, exc_info=True)

    def _deserialize_prices_series(self, prices_json: Optional[str], coin_id_symbol: Tuple[str, str]) -> Optional[pd.Series]:
        if not prices_json:
            return None
        try:
            prices_series = pd.read_json(StringIO(prices_json), orient="split", typ="series")
            prices_series.index = pd.to_datetime(prices_series.index, unit="ms", utc=True)
            return prices_series
        except Exception as e:
            error_msg = f"Cannot reconstruct price Series for {coin_id_symbol}: {e}"
            logger.error(error_msg)
            self.log_message(error_msg)
            return None

    def _calculate_rsi_task(self, coin_id_symbol: Tuple[str, str], data: Optional[pd.Series], timeframe: str) -> None:
        rsi_series = None
        try:
            if self.periods is None:
                error_msg = "RSI period has not been configured. Stopping calculation."
                logger.error(error_msg)
                self.log_message(error_msg)
            elif data is None or data.empty or len(data.dropna()) < self.periods + 1:
                logger.warning(
                    f"Skipping RSI calculation for {coin_id_symbol} ({timeframe}): "
                    f"insufficient or invalid data."
                )
            else:
                valid_data = data.dropna().astype(float)
                delta = valid_data.diff()
                gain = (delta.where(delta > 0, 0)).rolling(window=self.periods).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(window=self.periods).mean()
                rs = gain / loss
                rs[loss == 0] = np.inf
                rsi_series = 100 - (100 / (1 + rs))
                rsi_series[rs == np.inf] = 100
                rsi_series = rsi_series.dropna()

            if rsi_series is not None and not rsi_series.empty:
                rsi_json = rsi_series.to_json(orient="split")
                event = RSICalculated(
                    coin_id_symbol=coin_id_symbol,
                    rsi_series_json=rsi_json,
                    timeframe=timeframe
                )
                sqlite_business_logger.log(self.__class__.__name__, f"RSICalculated pour {coin_id_symbol}")
                self.service_bus.publish("RSICalculated", event, self.__class__.__name__)
        except Exception as e:
            error_msg = f"Unexpected error calculating RSI for {coin_id_symbol}: {e}"
            logger.error(error_msg, exc_info=True)
            self.log_message(error_msg)
            # rsi_series = None  # Ensure the result is None in case of failure
