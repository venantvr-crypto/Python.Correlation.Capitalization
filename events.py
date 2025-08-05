from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

import pandas as pd

# Importation de la nouvelle classe de configuration
from configuration import AnalysisConfig


@dataclass(frozen=True)
class AnalysisConfigurationProvided:
    """
    Événement de configuration diffusé à tous les services au démarrage.
    Contient tous les paramètres transversaux de la session d'analyse.
    """
    session_guid: str
    config: AnalysisConfig


@dataclass(frozen=True)
class RunAnalysisRequested:
    """Événement pour démarrer une nouvelle session d'analyse (signal de départ)."""
    pass


@dataclass(frozen=True)
class FetchTopCoinsRequested:
    """Événement de requête pour récupérer les N top coins."""
    n: int


@dataclass(frozen=True)
class TopCoinsFetched:
    """Événement indiquant que la liste des top coins a été récupérée."""
    coins: List[Dict]


@dataclass(frozen=True)
class SingleCoinFetched:
    """Événement pour une seule pièce, utilisé par le DatabaseManager."""
    coin: Dict


@dataclass(frozen=True)
class FetchHistoricalPricesRequested:
    """Événement de requête pour récupérer les prix historiques."""
    coin_id_symbol: Tuple[str, str]
    weeks: int
    timeframe: str


@dataclass(frozen=True)
class HistoricalPricesFetched:
    """Événement indiquant que les prix historiques ont été récupérés."""
    coin_id_symbol: Tuple[str, str]
    prices_df: Optional[pd.DataFrame]
    timeframe: str


@dataclass(frozen=True)
class CalculateRSIRequested:
    """Événement de requête pour calculer le RSI."""
    coin_id_symbol: Tuple[str, str]
    prices_series: Optional[pd.Series]
    timeframe: str


@dataclass(frozen=True)
class RSICalculated:
    """Événement indiquant que le RSI a été calculé."""
    coin_id_symbol: Tuple[str, str]
    rsi: Optional[pd.Series]
    timeframe: str


@dataclass(frozen=True)
class CorrelationAnalyzed:
    """Événement indiquant qu'une corrélation a été analysée."""
    result: Optional[Dict]
    timeframe: str


@dataclass(frozen=True)
class CoinProcessingFailed:
    """Événement signalant l'échec du traitement d'une pièce."""
    coin_id_symbol: Tuple[str, str]
    timeframe: str


@dataclass(frozen=True)
class FinalResultsReady:
    """Événement final avec les résultats agrégés, prêt pour l'affichage."""
    results: List[Dict]
    weeks: int
    timeframes: List[str]


@dataclass(frozen=True)
class DisplayCompleted:
    """Événement de fin signalant que l'affichage est terminé."""
    pass


@dataclass(frozen=True)
class AnalysisJobCompleted:
    """Signale que l'analyse pour un timeframe est terminée."""
    timeframe: str


@dataclass(frozen=True)
class FetchPrecisionDataRequested:
    """Événement de requête pour récupérer les données de précision des marchés."""
    pass


@dataclass(frozen=True)
class PrecisionDataFetched:
    """Événement indiquant que les données de précision ont été récupérées."""
    precision_data: List[Dict]
