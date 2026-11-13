import argparse
import uuid

from pydantic import ValidationError
from python_threadsafe_logger import sqlite_business_logger

from configuration import AnalysisConfig
from crypto_analyzer import CryptoAnalyzer
from logger import logger


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyse de corrélation RSI entre les cryptos et BTC.")
    parser.add_argument("--weeks", type=int, default=50, help="Semaines d'historique à récupérer.")
    parser.add_argument("--top-n-coins", type=int, default=1000, help="Nombre de top cryptos à analyser.")
    parser.add_argument("--correlation-threshold", type=float, default=0.7, help="Seuil de corrélation (0 à 1).")
    parser.add_argument("--rsi-period", type=int, default=14, help="Période du RSI.")
    parser.add_argument("--timeframes", nargs="+", default=["1h", "1d"], help="Unités de temps (ex: 1h 1d).")
    parser.add_argument("--low-cap-percentile", type=float, default=25.0, help="Percentile de faible capitalisation.")
    parser.add_argument("--pubsub-url", default="http://localhost:5000", help="URL du serveur Pub/Sub.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    session_guid = str(uuid.uuid4())
    logger.info(f"Starting analysis session with GUID: {session_guid}")

    try:
        analysis_config = AnalysisConfig(**vars(args))
    except ValidationError as e:
        logger.critical(f"Configuration error, application cannot start: \n{e}")
        return 1

    logger.info("Configuration loaded and validated.")

    with sqlite_business_logger:
        sqlite_business_logger.log("__main__", f"Starting analysis session with GUID: {session_guid}")
        CryptoAnalyzer(config=analysis_config, session_guid=session_guid).run()
        sqlite_business_logger.log("__main__", f"Stopping analysis session with GUID: {session_guid}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
