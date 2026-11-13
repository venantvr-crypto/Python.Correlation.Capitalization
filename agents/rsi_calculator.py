import numpy as np
import pandas as pd

# noinspection PyPackageRequirements
from python_pubsub_client import QueueWorkerThread, ServiceBus
from python_threadsafe_logger import sqlite_business_logger

from events import AnalysisConfigurationProvided, CalculateRSIRequested, CoinProcessingFailed, CoinRef, RSICalculated
from logger import logger
from serialization import frame_to_json, series_from_json


def compute_rsi(prices: pd.Series, period: int) -> pd.Series:
    """
    Computes the RSI of a price series using simple moving averages of gains and losses.

    Returns an empty series when there are not enough valid prices (`period + 1`).
    """
    valid_prices = prices.dropna().astype(float)

    if len(valid_prices) < period + 1:
        return pd.Series(dtype=float)

    delta = valid_prices.diff()
    gain = delta.clip(lower=0).fillna(0).rolling(window=period).mean()
    loss = (-delta.clip(upper=0)).fillna(0).rolling(window=period).mean()

    # No loss over the window means a saturated RSI of 100.
    rs = (gain / loss).mask(loss == 0, np.inf)
    return (100 - 100 / (1 + rs)).dropna()


class RSICalculator(QueueWorkerThread):
    """Calculates RSI for a price series in its own thread."""

    def __init__(self, service_bus: ServiceBus | None = None):
        super().__init__(service_bus=service_bus, name="RSICalculator")
        self.periods: int | None = None
        self.session_guid: str | None = None

    def setup_event_subscriptions(self) -> None:
        self.service_bus.subscribe("AnalysisConfigurationProvided", self._handle_configuration_provided)
        self.service_bus.subscribe("CalculateRSIRequested", self._handle_calculate_rsi_requested)

    def _publish(self, event) -> None:

        if self.service_bus is not None:
            self.service_bus.publish(type(event).__name__, event, self.__class__.__name__)

    def _handle_configuration_provided(self, event: AnalysisConfigurationProvided) -> None:
        self.session_guid = event.session_guid
        self.periods = event.config.rsi_period
        logger.info(f"RSICalculator received configuration for session {self.session_guid}.")

    def _handle_calculate_rsi_requested(self, event: CalculateRSIRequested) -> None:
        prices_series = self._deserialize_prices_series(event.prices_series_json, event.coin_id_symbol)
        self.add_task("_calculate_rsi_task", event.coin_id_symbol, prices_series, event.timeframe)

    def _deserialize_prices_series(self, prices_json: str | None, coin_id_symbol: CoinRef) -> pd.Series | None:

        if not prices_json:
            return None

        try:
            return series_from_json(prices_json)
        except Exception as e:
            error_msg = f"Cannot reconstruct price Series for {coin_id_symbol}: {e}"
            logger.error(error_msg)
            self.log_message(error_msg)
            return None

    def _calculate_rsi_task(self, coin_id_symbol: CoinRef, data: pd.Series | None, timeframe: str) -> None:
        rsi_series = None

        try:
            if self.periods is None:
                error_msg = "RSI period has not been configured. Stopping calculation."
                logger.error(error_msg)
                self.log_message(error_msg)
            elif data is not None:
                rsi_series = compute_rsi(data, self.periods)
        except Exception as e:
            error_msg = f"Unexpected error calculating RSI for {coin_id_symbol}: {e}"
            logger.error(error_msg, exc_info=True)
            self.log_message(error_msg)

        if rsi_series is None or rsi_series.empty:
            logger.warning(f"No RSI for {coin_id_symbol} ({timeframe}): insufficient or invalid data.")
            # Always publish a failure so the job counter can be decremented.
            self._publish(CoinProcessingFailed(coin_id_symbol=coin_id_symbol, timeframe=timeframe))
            return

        sqlite_business_logger.log(self.__class__.__name__, f"RSICalculated pour {coin_id_symbol}")
        self._publish(RSICalculated(coin_id_symbol=coin_id_symbol, rsi_series_json=frame_to_json(rsi_series), timeframe=timeframe))
