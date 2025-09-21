import queue
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd

from events import (
    AnalysisConfigurationProvided,
    CorrelationAnalyzed,
    HistoricalPricesFetched,
    PrecisionDataFetched,
    RSICalculated,
    SingleCoinFetched,
)
from logger import logger
from service_bus import ServiceBus


class DatabaseManager(threading.Thread):
    """Gère les interactions avec la base de données SQLite dans son propre thread."""

    def __init__(self, db_name: str = "crypto_data.db", service_bus: Optional[ServiceBus] = None):
        super().__init__()
        self.db_name = db_name
        self.conn = None
        self.cursor = None
        self.service_bus = service_bus
        self.session_guid: Optional[str] = None
        self.work_queue = queue.Queue()
        self._running = True

        if self.service_bus:
            self._setup_event_subscriptions()

    def _setup_event_subscriptions(self):
        self.service_bus.subscribe("AnalysisConfigurationProvided", self._handle_configuration_provided)
        self.service_bus.subscribe("SingleCoinFetched", self._handle_single_coin_fetched)
        self.service_bus.subscribe("HistoricalPricesFetched", self._handle_historical_prices_fetched)
        self.service_bus.subscribe("RSICalculated", self._handle_rsi_calculated)
        self.service_bus.subscribe("CorrelationAnalyzed", self._handle_correlation_analyzed)
        self.service_bus.subscribe("PrecisionDataFetched", self._handle_precision_data_fetched)

    def _handle_configuration_provided(self, event: AnalysisConfigurationProvided):
        self.session_guid = event.session_guid
        logger.info(f"DatabaseManager a reçu la configuration pour la session {self.session_guid}.")

    def _handle_single_coin_fetched(self, event: SingleCoinFetched):
        if event.coin:
            self.work_queue.put(("_db_save_token", (event.coin, self.session_guid), {}, None))

    def _handle_historical_prices_fetched(self, event: HistoricalPricesFetched):
        if event.prices_df is not None:
            self.work_queue.put(
                (
                    "_db_save_prices",
                    (event.coin_id_symbol, event.prices_df, self.session_guid, event.timeframe),
                    {},
                    None,
                )
            )

    def _handle_rsi_calculated(self, event: RSICalculated):
        if event.rsi is not None:
            self.work_queue.put(
                ("_db_save_rsi", (event.coin_id_symbol, event.rsi, self.session_guid, event.timeframe), {}, None)
            )

    def _handle_correlation_analyzed(self, event: CorrelationAnalyzed):
        result = event.result
        if result:
            task_args = (
                (result["coin_id"], result["coin_symbol"]),
                datetime.now(timezone.utc).isoformat(),
                result["correlation"],
                result["market_cap"],
                result["low_cap_quartile"],
                self.session_guid,
                event.timeframe,
            )
            self.work_queue.put(("_db_save_correlation", task_args, {}, None))

    def _handle_precision_data_fetched(self, event: PrecisionDataFetched):
        if event.precision_data:
            self.work_queue.put(("_db_save_precision_data", (event.precision_data, self.session_guid), {}, None))

    def run(self):
        self.conn = sqlite3.connect(self.db_name, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self._initialize_tables()
        logger.info("Thread DatabaseManager démarré.")
        while self._running:
            try:
                task = self.work_queue.get(timeout=1)
                if task is None:
                    break
                method_name, args, kwargs, _ = task
                try:
                    method = getattr(self, method_name)
                    method(*args, **kwargs)
                except Exception as e:
                    logger.error(f"Erreur d'exécution de la tâche BDD ({method_name}): {e}", exc_info=True)
                finally:
                    self.work_queue.task_done()
            except queue.Empty:
                continue
        self._close()
        logger.info("Thread DatabaseManager arrêté.")

    def stop(self):
        self._running = False
        self.work_queue.put(None)
        self.join()

    def _initialize_tables(self) -> None:
        # Cette méthode reste inchangée
        try:
            self.cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS tokens (
                    coin_id TEXT, coin_symbol TEXT, session_guid TEXT, symbol TEXT, name TEXT, image TEXT,
                    current_price REAL, market_cap INTEGER, market_cap_rank INTEGER,
                    fully_diluted_valuation INTEGER, total_volume INTEGER, high_24h REAL, low_24h REAL,
                    price_change_24h REAL, price_change_percentage_24h REAL, market_cap_change_24h INTEGER,
                    market_cap_change_percentage_24h REAL, circulating_supply REAL, total_supply REAL,
                    max_supply REAL, ath REAL, ath_change_percentage REAL, ath_date TEXT, atl REAL,
                    atl_change_percentage REAL, atl_date TEXT, roi TEXT, last_updated TEXT,
                    PRIMARY KEY (coin_id, session_guid)
                )
            """
            )
            self.cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS prices (
                    coin_id TEXT, coin_symbol TEXT, timestamp TIMESTAMP, session_guid TEXT,
                    open REAL, high REAL, low REAL, close REAL, volume REAL, timeframe TEXT,
                    PRIMARY KEY (coin_id, timestamp, session_guid, timeframe)
                )
            """
            )
            self.cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS rsi (
                    coin_id TEXT, coin_symbol TEXT, timestamp TIMESTAMP, session_guid TEXT, rsi REAL, timeframe TEXT,
                    PRIMARY KEY (coin_id, timestamp, session_guid, timeframe)
                )
            """
            )
            self.cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS correlations (
                    coin_id TEXT, coin_symbol TEXT, run_timestamp TIMESTAMP, session_guid TEXT,
                    correlation REAL, market_cap REAL, low_cap_quartile BOOLEAN, timeframe TEXT,
                    PRIMARY KEY (coin_id, run_timestamp, session_guid, timeframe)
                )
            """
            )
            self.cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS precision_data (
                    symbol TEXT, quote_asset TEXT NOT NULL, base_asset TEXT NOT NULL,
                    status BOOLEAN NOT NULL, base_asset_precision INTEGER NOT NULL, step_size REAL NOT NULL,
                    min_qty REAL NOT NULL, tick_size REAL NOT NULL, min_notional REAL NOT NULL,
                    session_guid TEXT,
                    PRIMARY KEY (symbol, session_guid)
                )
            """
            )
            self.conn.commit()
        except Exception as e:
            logger.error(f"Erreur lors de l'initialisation des tables: {e}", exc_info=True)
            raise

    # ==============================================================================
    # MODIFICATION PRINCIPALE CI-DESSOUS
    # ==============================================================================
    def _db_save_precision_data(self, precision_data: List[Dict], session_guid: str) -> None:
        """Tâche interne optimisée pour enregistrer les données de précision en masse."""
        try:
            # 1. Préparer une liste de tuples, où chaque tuple représente une ligne à insérer.
            data_to_insert = []
            for data in precision_data:
                values = (
                    data.get("symbol"),
                    data.get("quote_asset"),
                    data.get("base_asset"),
                    data.get("status"),
                    data.get("base_asset_precision"),
                    float(data.get("step_size", "0")),
                    float(data.get("min_qty", "0")),
                    float(data.get("tick_size", "0")),
                    float(data.get("min_notional", "0")),
                    session_guid,
                )
                data_to_insert.append(values)

            if not data_to_insert:
                return

            # 2. Utiliser executemany() pour une insertion en masse ultra-rapide.
            # 'INSERT OR IGNORE' évite les erreurs si une clé primaire existe déjà.
            sql = """
                INSERT OR IGNORE INTO precision_data (
                    symbol, quote_asset, base_asset, status, base_asset_precision,
                    step_size, min_qty, tick_size, min_notional, session_guid
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            self.cursor.executemany(sql, data_to_insert)
            self.conn.commit()
            logger.info(f"{len(data_to_insert)} enregistrements de précision insérés en BDD.")
        except Exception as e:
            logger.error(f"Erreur lors de l'enregistrement des données de précision: {e}", exc_info=True)

    def _db_save_token(self, coin: Dict, session_guid: Optional[str]) -> None:
        # Cette méthode reste inchangée
        pass

    def _db_save_prices(
            self, coin_id_symbol: Tuple[str, str], prices_df: pd.DataFrame, session_guid: Optional[str], timeframe: str
    ) -> None:
        # Cette méthode reste inchangée
        pass

    def _db_save_rsi(
            self, coin_id_symbol: Tuple[str, str], rsi_series: pd.Series, session_guid: Optional[str], timeframe: str
    ) -> None:
        # Cette méthode reste inchangée
        pass

    def _db_save_correlation(
            self,
            coin_id_symbol: Tuple[str, str],
            run_timestamp: str,
            correlation: float,
            market_cap: float,
            low_cap_quartile: bool,
            session_guid: Optional[str],
            timeframe: str,
    ) -> None:
        # Cette méthode reste inchangée
        pass

    def _close(self) -> None:
        try:
            if self.conn:
                self.conn.close()
        except Exception as e:
            logger.error(f"Erreur lors de la fermeture de la base: {e}")
