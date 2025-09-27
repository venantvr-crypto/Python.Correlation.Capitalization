from typing import List

from pydantic import BaseModel, Field


class AnalysisConfig(BaseModel):
    weeks: int = Field(50, gt=0, description="Le nombre de semaines de données historiques à récupérer.")
    top_n_coins: int = Field(200, gt=0, description="Le nombre de top cryptos à analyser.")
    correlation_threshold: float = Field(0.7, ge=0.0, le=1.0, description="Le seuil de corrélation pour retenir un résultat.")
    rsi_period: int = Field(14, gt=1, description="La période pour le calcul du RSI.")
    timeframes: List[str] = Field(default_factory=lambda: ["1d"])
    low_cap_percentile: float = Field(25.0, ge=0.0, le=100.0, description="Le percentile pour définir la 'faible capitalisation'.")
    pubsub_url: str = Field(default="http://localhost:5000")
