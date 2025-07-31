import uuid

from crypto_analyzer import CryptoAnalyzer
from logger import logger

if __name__ == "__main__":
    session_guid = str(uuid.uuid4())
    logger.info(f"Démarrage de la session d'analyse avec le GUID: {session_guid}")
    analyzer = CryptoAnalyzer(weeks=50, top_n_coins=100, correlation_threshold=0.8, rsi_period=14,
                              session_guid=session_guid)
    analyzer.run()
