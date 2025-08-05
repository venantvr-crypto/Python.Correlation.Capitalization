from dataclasses import dataclass, field
from typing import List


@dataclass
class AnalysisConfig:
    """Contient tous les paramètres statiques pour une session d'analyse."""
    weeks: int = 50
    top_n_coins: int = 200
    correlation_threshold: float = 0.7
    rsi_period: int = 14
    timeframes: List[str] = field(default_factory=lambda: ['1d'])
    low_cap_percentile: float = 25.0
