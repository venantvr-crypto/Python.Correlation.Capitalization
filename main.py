import uuid

from pydantic import ValidationError
from threadsafe_logger import sqlite_business_logger

from configuration import AnalysisConfig
from crypto_analyzer import CryptoAnalyzer
from logger import logger

if __name__ == "__main__":
    session_guid = str(uuid.uuid4())
    logger.info(f"Démarrage de la session d'analyse avec le GUID: {session_guid}")

    with sqlite_business_logger:
        # noinspection PyPackageRequirements
        sqlite_business_logger.log("__main__", f"Démarrage de la session d'analyse avec le GUID: {session_guid}")

        try:
            analysis_config = AnalysisConfig(
                weeks=50,
                top_n_coins=500,
                correlation_threshold=0.7,
                rsi_period=14,
                timeframes=["1h", "1d"],
                low_cap_percentile=25.0,
                pubsub_url="http://localhost:5000",
            )
            logger.info("Configuration chargée et validée.")
        except ValidationError as e:
            logger.critical(f"Erreur de configuration, l'application ne peut pas démarrer : \n{e}")
            exit(1)

        # Le session_guid est passé directement au constructeur
        analyzer = CryptoAnalyzer(config=analysis_config, session_guid=session_guid)

        # La méthode run() est appelée sans argument
        analyzer.run()
        sqlite_business_logger.log("__main__", f"Arrêt de la session d'analyse avec le GUID: {session_guid}")
