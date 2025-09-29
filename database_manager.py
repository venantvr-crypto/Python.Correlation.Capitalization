import sqlite3
import threading
from datetime import datetime, timezone
from io import StringIO
from typing import Dict, List, Optional, Tuple

import pandas as pd
# noinspection PyPackageRequirements
from pubsub import QueueWorkerThread, ServiceBus

from events import (
    AnalysisConfigurationProvided,
    CorrelationAnalyzed,
    HistoricalPricesFetched,
    PrecisionDataFetched,
    RSICalculated,
    SingleCoinFetched,
)
from logger import logger


class DatabaseManager(QueueWorkerThread):
    """Manages interactions with the SQLite database in its own thread."""

    def __init__(self, db_name: str = "crypto_data.db", service_bus: Optional[ServiceBus] = None):
        super().__init__(service_bus=service_bus, name="DatabaseManager")
        self.db_name = db_name
        self.conn = None
        self.cursor = None
        self.session_guid: Optional[str] = None
        self._initialized_event = threading.Event()  # -- NEW: Synchronization event

    def setup_event_subscriptions(self) -> None:
        self.service_bus.subscribe("AnalysisConfigurationProvided", self._handle_configuration_provided)
        self.service_bus.subscribe("SingleCoinFetched", self._handle_single_coin_fetched)
        self.service_bus.subscribe("HistoricalPricesFetched", self._handle_historical_prices_fetched)
        self.service_bus.subscribe("RSICalculated", self._handle_rsi_calculated)
        self.service_bus.subscribe("CorrelationAnalyzed", self._handle_correlation_analyzed)
        self.service_bus.subscribe("PrecisionDataFetched", self._handle_precision_data_fetched)

    def _handle_configuration_provided(self, event: AnalysisConfigurationProvided):
        self.session_guid = event.session_guid
        logger.info(f"DatabaseManager received configuration for session {self.session_guid}.")

    def _handle_single_coin_fetched(self, event: SingleCoinFetched):
        if event.coin:
            self.add_task("_db_save_token", event.coin, self.session_guid)

    def _handle_historical_prices_fetched(self, event: HistoricalPricesFetched):
        if not event.prices_df_json:
            return
        try:
            prices_df = pd.read_json(StringIO(event.prices_df_json), orient="split")
            prices_df.index = pd.to_datetime(prices_df.index, unit="ms", utc=True)
        except Exception as e:
            logger.error(
                f"Cannot reconstruct price DataFrame for {event.coin_id_symbol}: {e}"
            )
            return
        if prices_df is not None and not prices_df.empty:
            self.add_task("_db_save_prices", event.coin_id_symbol, prices_df, self.session_guid, event.timeframe)

    def _handle_rsi_calculated(self, event: RSICalculated):
        if not event.rsi_series_json:
            return
        try:
            rsi_series = pd.read_json(StringIO(event.rsi_series_json), orient="split", typ="series")
            rsi_series.index = pd.to_datetime(rsi_series.index, unit="ms", utc=True)
        except Exception as e:
            logger.error(f"Cannot reconstruct RSI Series for database for {event.coin_id_symbol}: {e}")
            return
        if rsi_series is not None and not rsi_series.empty:
            self.add_task("_db_save_rsi", event.coin_id_symbol, rsi_series, self.session_guid, event.timeframe)

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
            self.add_task("_db_save_correlation", *task_args)

    def _handle_precision_data_fetched(self, event: PrecisionDataFetched):
        if event.precision_data:
            self.add_task("_db_save_precision_data", event.precision_data, self.session_guid)

    def run(self):
        self.conn = sqlite3.connect(self.db_name, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self._initialize_tables()
        self._initialized_event.set()

        # Call the parent class run() method to manage the work loop
        super().run()

        self._close()

    def _initialize_tables(self) -> None:
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
            logger.error(f"Error initializing tables: {e}", exc_info=True)
            raise

    def _db_save_precision_data(self, precision_data: List[Dict], session_guid: str) -> None:
        try:
            data_to_insert = [
                (
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
                for data in precision_data
            ]
            if not data_to_insert:
                return
            sql = """
                INSERT OR IGNORE INTO precision_data (
                    symbol, quote_asset, base_asset, status, base_asset_precision,
                    step_size, min_qty, tick_size, min_notional, session_guid
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            self.cursor.executemany(sql, data_to_insert)
            self.conn.commit()
            logger.info(f"{len(data_to_insert)} precision records inserted into database.")
        except Exception as e:
            logger.error(f"Error saving precision data: {e}", exc_info=True)

    def _db_save_token(self, coin: Dict, session_guid: Optional[str]) -> None:
        coin_id = coin.get('id')
        try:
            if not coin_id:
                logger.error("Missing 'id' key in token payload.")
                return

            def safe_int(value):
                if value is None:
                    return None
                try:
                    return int(float(value)) if isinstance(value, (float, str)) else value
                except (ValueError, TypeError):
                    return None

            def safe_float(value):
                if value is None:
                    return None
                try:
                    return float(value) if isinstance(value, (int, str)) else value
                except (ValueError, TypeError):
                    return None

            def safe_str(value):
                return str(value) if value is not None else None

            values = (
                coin_id, coin.get('symbol', '').upper(), session_guid, safe_str(coin.get('symbol')), safe_str(coin.get('name')),
                safe_str(coin.get('image')), safe_float(coin.get('current_price')),
                safe_int(coin.get('market_cap')), safe_int(coin.get('market_cap_rank')),
                safe_int(coin.get('fully_diluted_valuation')), safe_int(coin.get('total_volume')),
                safe_float(coin.get('high_24h')), safe_float(coin.get('low_24h')),
                safe_float(coin.get('price_change_24h')), safe_float(coin.get('price_change_percentage_24h')),
                safe_int(coin.get('market_cap_change_24h')), safe_float(coin.get('market_cap_change_percentage_24h')),
                safe_float(coin.get('circulating_supply')), safe_float(coin.get('total_supply')),
                safe_float(coin.get('max_supply')), safe_float(coin.get('ath')),
                safe_float(coin.get('ath_change_percentage')), safe_str(coin.get('ath_date')),
                safe_float(coin.get('atl')), safe_float(coin.get('atl_change_percentage')),
                safe_str(coin.get('atl_date')), safe_str(coin.get('roi')), safe_str(coin.get('last_updated'))
            )
            self.cursor.execute('''
                INSERT OR REPLACE INTO tokens (
                    coin_id, coin_symbol, session_guid, symbol, name, image, current_price, market_cap, market_cap_rank,
                    fully_diluted_valuation, total_volume, high_24h, low_24h, price_change_24h,
                    price_change_percentage_24h, market_cap_change_24h, market_cap_change_percentage_24h,
                    circulating_supply, total_supply, max_supply, ath, ath_change_percentage,
                    ath_date, atl, atl_change_percentage, atl_date, roi, last_updated
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', values)
            self.conn.commit()
            logger.info(f"Token {coin_id} saved with session_guid={session_guid}.")
        except Exception as e:
            logger.error(f"Error saving token {coin_id}: {e}")

    def _db_save_prices(self, coin_id_symbol: Tuple[str, str], prices_df: pd.DataFrame, session_guid: Optional[str], timeframe: str) -> None:
        coin_id, coin_symbol = coin_id_symbol
        try:
            if prices_df.empty:
                return
            data_to_insert = [
                (coin_id, coin_symbol, ts.isoformat(), session_guid, row.open, row.high, row.low, row.close, row.volume, timeframe)
                for ts, row in prices_df.iterrows()
            ]
            if not data_to_insert:
                return
            sql = """
                INSERT OR IGNORE INTO prices
                (coin_id, coin_symbol, timestamp, session_guid, open, high, low, close, volume, timeframe)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            self.cursor.executemany(sql, data_to_insert)
            self.conn.commit()
            logger.info(f"{len(data_to_insert)} prices for {coin_id} saved (session={session_guid}).")
        except Exception as e:
            logger.error(f"Error bulk saving prices for {coin_id}: {e}", exc_info=True)

    def _db_save_rsi(self, coin_id_symbol: Tuple[str, str], rsi_series: pd.Series, session_guid: Optional[str], timeframe: str) -> None:
        coin_id, coin_symbol = coin_id_symbol
        try:
            if rsi_series.empty:
                return
            data_to_insert = [
                (coin_id, coin_symbol, ts.to_pydatetime().isoformat(), session_guid, rsi_value, timeframe)
                for ts, rsi_value in rsi_series.items() if not pd.isna(rsi_value)
            ]
            if not data_to_insert:
                return
            sql = """
                INSERT OR IGNORE INTO rsi (coin_id, coin_symbol, timestamp, session_guid, rsi, timeframe)
                VALUES (?, ?, ?, ?, ?, ?)
            """
            self.cursor.executemany(sql, data_to_insert)
            self.conn.commit()
            logger.info(f"{len(data_to_insert)} RSI for {coin_id} saved (session={session_guid}).")
        except Exception as e:
            logger.error(f"Error bulk saving RSI for {coin_id}: {e}", exc_info=True)

    def _db_save_correlation(self, coin_id_symbol: Tuple[str, str], run_timestamp: str, correlation: float, market_cap: float, low_cap_quartile: bool,
                             session_guid: Optional[str], timeframe: str) -> None:
        coin_id, coin_symbol = coin_id_symbol
        try:
            self.cursor.execute('''
                INSERT INTO correlations (coin_id, coin_symbol, run_timestamp, session_guid, correlation, market_cap, low_cap_quartile, timeframe)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (coin_id, coin_symbol, run_timestamp, session_guid, correlation, market_cap, low_cap_quartile, timeframe))
            self.conn.commit()
            logger.info(f"Correlation for {coin_id} saved with session_guid={session_guid}.")
        except Exception as e:
            logger.error(f"Error saving correlation for {coin_id}: {e}")

    def _close(self) -> None:
        try:
            if self.conn:
                self.conn.close()
        except Exception as e:
            logger.error(f"Error closing database: {e}")
