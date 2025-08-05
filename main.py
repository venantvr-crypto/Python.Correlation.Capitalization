import uuid

from configuration import AnalysisConfig
from crypto_analyzer import CryptoAnalyzer
from logger import logger

if __name__ == "__main__":
    session_guid = str(uuid.uuid4())
    logger.info(f"Démarrage de la session d'analyse avec le GUID: {session_guid}")

    # Création d'un objet de configuration centralisé
    # Les valeurs par défaut peuvent être surchargées ici
    analysis_config = AnalysisConfig(
        weeks=50,
        top_n_coins=5000,
        correlation_threshold=0.7,  # Seuil réaliste
        rsi_period=14,
        timeframes=['1h', '1d'],
        low_cap_percentile=25.0  # Seuil réaliste pour les "low caps"
    )

    analyzer = CryptoAnalyzer(config=analysis_config)
    analyzer.run(session_guid=session_guid)
