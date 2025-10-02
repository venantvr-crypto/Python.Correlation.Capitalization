#!/usr/bin/env python3
"""Quick test with reduced parameters to verify the fix works."""
import uuid

from pydantic import ValidationError
from async_threadsafe_logger import sqlite_business_logger

from configuration import AnalysisConfig
from crypto_analyzer import CryptoAnalyzer
from logger import logger

if __name__ == "__main__":
    session_guid = str(uuid.uuid4())
    logger.info(f"[TEST] Starting QUICK analysis session with GUID: {session_guid}")

    with sqlite_business_logger:
        sqlite_business_logger.log("test_quick", f"Starting QUICK analysis with GUID: {session_guid}")

        try:
            # Much smaller configuration for quick testing
            analysis_config = AnalysisConfig(
                weeks=2,  # Only 2 weeks instead of 50
                top_n_coins=10,  # Only 10 coins instead of 1000
                correlation_threshold=0.5,  # Lower threshold
                rsi_period=14,
                timeframes=["1h"],  # Only 1 timeframe instead of 2
                low_cap_percentile=25.0,
                pubsub_url="http://localhost:5000",
            )
            logger.info("[TEST] Quick test configuration loaded.")
        except ValidationError as e:
            logger.critical(f"Configuration error: \n{e}")
            exit(1)

        analyzer = CryptoAnalyzer(config=analysis_config, session_guid=session_guid)
        analyzer.run()

        logger.info("[TEST] Quick analysis completed successfully!")
        sqlite_business_logger.log("test_quick", f"QUICK analysis completed for GUID: {session_guid}")
