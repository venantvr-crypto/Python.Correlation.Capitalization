from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from configuration import AnalysisConfig


@dataclass(frozen=True)
class AnalysisConfigurationProvided:
    session_guid: str
    config: AnalysisConfig


@dataclass(frozen=True)
class RunAnalysisRequested:
    payload: bool = True


@dataclass(frozen=True)
class FetchTopCoinsRequested:
    n: int


@dataclass(frozen=True)
class TopCoinsFetched:
    coins: List[Dict]


@dataclass(frozen=True)
class SingleCoinFetched:
    coin: Dict


@dataclass(frozen=True)
class FetchHistoricalPricesRequested:
    coin_id_symbol: Tuple[str, str]
    weeks: int
    timeframe: str


@dataclass(frozen=True)
class HistoricalPricesFetched:
    """Événement indiquant que les prix historiques ont été récupérés."""

    coin_id_symbol: Tuple[str, str]
    prices_df_json: Optional[str]
    timeframe: str


@dataclass(frozen=True)
class CalculateRSIRequested:
    """Événement de requête pour calculer le RSI."""

    coin_id_symbol: Tuple[str, str]
    prices_series_json: Optional[str]
    timeframe: str


@dataclass(frozen=True)
class RSICalculated:
    coin_id_symbol: Tuple[str, str]
    rsi_series_json: Optional[str]
    timeframe: str


@dataclass(frozen=True)
class CorrelationAnalyzed:
    result: Optional[Dict]
    timeframe: str


@dataclass(frozen=True)
class CoinProcessingFailed:
    coin_id_symbol: Tuple[str, str]
    timeframe: str


@dataclass(frozen=True)
class FinalResultsReady:
    results: List[Dict]
    weeks: int
    timeframes: List[str]


@dataclass(frozen=True)
class DisplayCompleted:
    payload: bool = True  # Ajout d'un payload


@dataclass(frozen=True)
class AnalysisJobCompleted:
    timeframe: str


@dataclass(frozen=True)
class FetchPrecisionDataRequested:
    payload: bool = True  # Ajout d'un payload


@dataclass(frozen=True)
class PrecisionDataFetched:
    precision_data: List[Dict]
