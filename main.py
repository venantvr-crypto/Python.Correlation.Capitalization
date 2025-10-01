import uuid

from pydantic import ValidationError
from threadsafe_logger import sqlite_business_logger

from configuration import AnalysisConfig
from crypto_analyzer import CryptoAnalyzer
from logger import logger

if __name__ == "__main__":
    session_guid = str(uuid.uuid4())
    logger.info(f"Starting analysis session with GUID: {session_guid}")

    with sqlite_business_logger:
        sqlite_business_logger.log("__main__", f"Starting analysis session with GUID: {session_guid}")

        try:
            analysis_config = AnalysisConfig(
                weeks=50,
                top_n_coins=1000,
                correlation_threshold=0.7,
                rsi_period=14,
                timeframes=["1h", "1d"],
                low_cap_percentile=25.0,
                pubsub_url="http://localhost:5000",

            )
            logger.info("Configuration loaded and validated.")
        except ValidationError as e:
            logger.critical(f"Configuration error, application cannot start: \n{e}")
            exit(1)

        # The session_guid is passed directly to the constructor
        analyzer = CryptoAnalyzer(config=analysis_config, session_guid=session_guid)

        # The run() method is called without arguments
        analyzer.run()
        sqlite_business_logger.log("__main__", f"Stopping analysis session with GUID: {session_guid}")
