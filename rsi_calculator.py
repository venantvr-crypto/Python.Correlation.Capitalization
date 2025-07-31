from typing import Optional

import numpy as np
import pandas as pd

from logger import logger


class RSICalculator:
    """Calcule le RSI pour une série de prix."""

    @staticmethod
    def calculate(data: pd.Series, periods: int = 14) -> Optional[pd.Series]:
        """Calcule le RSI en excluant la dernière donnée."""
        try:
            if len(data) < periods + 1:
                raise ValueError("Données insuffisantes pour calculer le RSI")
            data = data[:-1]  # Exclure la dernière donnée
            delta = data.diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=periods).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=periods).mean()
            loss = loss.replace(0, np.nan)  # Éviter division par zéro
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            return rsi
        except Exception as e:
            logger.error(f"Erreur lors du calcul du RSI: {e}")
            return None
