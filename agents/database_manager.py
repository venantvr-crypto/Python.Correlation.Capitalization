import sqlite3
import threading
from datetime import UTC, datetime

import pandas as pd

# noinspection PyPackageRequirements
from python_pubsub_client import QueueWorkerThread, ServiceBus

from events import (
    AnalysisConfigurationProvided,
    CoinRef,
    CorrelationAnalyzed,
    HistoricalPricesFetched,
    PrecisionDataFetched,
    RSICalculated,
    SingleCoinFetched,
)
from logger import logger
from serialization import frame_from_json, series_from_json

SCHEMA = """
    CREATE TABLE IF NOT EXISTS tokens (
        coin_id TEXT, coin_symbol TEXT, session_guid TEXT, symbol TEXT, name TEXT, image TEXT,
        current_price REAL, market_cap INTEGER, market_cap_rank INTEGER,
        fully_diluted_valuation INTEGER, total_volume INTEGER, high_24h REAL, low_24h REAL,
        price_change_24h REAL, price_change_percentage_24h REAL, market_cap_change_24h INTEGER,
        market_cap_change_percentage_24h REAL, circulating_supply REAL, total_supply REAL,
        max_supply REAL, ath REAL, ath_change_percentage REAL, ath_date TEXT, atl REAL,
        atl_change_percentage REAL, atl_date TEXT, roi TEXT, last_updated TEXT,
        PRIMARY KEY (coin_id, session_guid)
    );
    CREATE TABLE IF NOT EXISTS prices (
        coin_id TEXT, coin_symbol TEXT, timestamp TIMESTAMP, session_guid TEXT,
        open REAL, high REAL, low REAL, close REAL, volume REAL, timeframe TEXT,
        PRIMARY KEY (coin_id, timestamp, session_guid, timeframe)
    );
    CREATE TABLE IF NOT EXISTS rsi (
        coin_id TEXT, coin_symbol TEXT, timestamp TIMESTAMP, session_guid TEXT, rsi REAL, timeframe TEXT,
        PRIMARY KEY (coin_id, timestamp, session_guid, timeframe)
    );
    CREATE TABLE IF NOT EXISTS correlations (
        coin_id TEXT, coin_symbol TEXT, run_timestamp TIMESTAMP, session_guid TEXT,
        correlation REAL, market_cap REAL, low_cap_quartile BOOLEAN, timeframe TEXT,
        PRIMARY KEY (coin_id, run_timestamp, session_guid, timeframe)
    );
    CREATE TABLE IF NOT EXISTS precision_data (
        symbol TEXT, quote_asset TEXT NOT NULL, base_asset TEXT NOT NULL,
        status BOOLEAN NOT NULL, base_asset_precision INTEGER NOT NULL, step_size REAL NOT NULL,
        min_qty REAL NOT NULL, tick_size REAL NOT NULL, min_notional REAL NOT NULL,
        session_guid TEXT,
        PRIMARY KEY (symbol, session_guid)
    );
"""


def _safe_int(value) -> int | None:

    if value is None or isinstance(value, int):
        return value

    try:
        return int(float(value))
    except (ValueError, TypeError, OverflowError):
        return None


def _safe_float(value) -> float | None:

    try:
        return float(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def _safe_str(value) -> str | None:
    return str(value) if value is not None else None


# CoinGecko market fields persisted in the `tokens` table, with their converters.
TOKEN_FIELDS = {
    "symbol": _safe_str,
    "name": _safe_str,
    "image": _safe_str,
    "current_price": _safe_float,
    "market_cap": _safe_int,
    "market_cap_rank": _safe_int,
    "fully_diluted_valuation": _safe_int,
    "total_volume": _safe_int,
    "high_24h": _safe_float,
    "low_24h": _safe_float,
    "price_change_24h": _safe_float,
    "price_change_percentage_24h": _safe_float,
    "market_cap_change_24h": _safe_int,
    "market_cap_change_percentage_24h": _safe_float,
    "circulating_supply": _safe_float,
    "total_supply": _safe_float,
    "max_supply": _safe_float,
    "ath": _safe_float,
    "ath_change_percentage": _safe_float,
    "ath_date": _safe_str,
    "atl": _safe_float,
    "atl_change_percentage": _safe_float,
    "atl_date": _safe_str,
    "roi": _safe_str,
    "last_updated": _safe_str,
}
TOKEN_COLUMNS = ["coin_id", "coin_symbol", "session_guid", *TOKEN_FIELDS]
INSERT_TOKEN_SQL = f"INSERT OR REPLACE INTO tokens ({', '.join(TOKEN_COLUMNS)}) VALUES ({', '.join('?' * len(TOKEN_COLUMNS))})"


class DatabaseManager(QueueWorkerThread):
    """Manages interactions with the SQLite database in its own thread."""

    def __init__(self, db_name: str = "crypto_data.db", service_bus: ServiceBus | None = None):
        super().__init__(service_bus=service_bus, name="DatabaseManager")
        self.db_name = db_name
        self.conn: sqlite3.Connection | None = None
        self.session_guid: str | None = None
        self._initialized_event = threading.Event()

    def setup_event_subscriptions(self) -> None:
        self.service_bus.subscribe("AnalysisConfigurationProvided", self._handle_configuration_provided)
        self.service_bus.subscribe("SingleCoinFetched", self._handle_single_coin_fetched)
        self.service_bus.subscribe("HistoricalPricesFetched", self._handle_historical_prices_fetched)
        self.service_bus.subscribe("RSICalculated", self._handle_rsi_calculated)
        self.service_bus.subscribe("CorrelationAnalyzed", self._handle_correlation_analyzed)
        self.service_bus.subscribe("PrecisionDataFetched", self._handle_precision_data_fetched)

    def _log_error(self, error_msg: str, exc_info: bool = False) -> None:
        logger.error(error_msg, exc_info=exc_info)
        self.log_message(error_msg)

    # --- Event handlers ---

    def _handle_configuration_provided(self, event: AnalysisConfigurationProvided) -> None:
        self.session_guid = event.session_guid
        logger.info(f"DatabaseManager received configuration for session {self.session_guid}.")

    def _handle_single_coin_fetched(self, event: SingleCoinFetched) -> None:

        if event.coin:
            self.add_task("_db_save_token", event.coin, self.session_guid)

    def _handle_historical_prices_fetched(self, event: HistoricalPricesFetched) -> None:

        if not event.prices_df_json:
            return

        try:
            prices_df = frame_from_json(event.prices_df_json)
        except Exception as e:
            logger.error(f"Cannot reconstruct price DataFrame for {event.coin_id_symbol}: {e}")
            return

        if not prices_df.empty:
            self.add_task("_db_save_prices", event.coin_id_symbol, prices_df, self.session_guid, event.timeframe)

    def _handle_rsi_calculated(self, event: RSICalculated) -> None:

        if not event.rsi_series_json:
            return

        try:
            rsi_series = series_from_json(event.rsi_series_json)
        except Exception as e:
            logger.error(f"Cannot reconstruct RSI Series for database for {event.coin_id_symbol}: {e}")
            return

        if not rsi_series.empty:
            self.add_task("_db_save_rsi", event.coin_id_symbol, rsi_series, self.session_guid, event.timeframe)

    def _handle_correlation_analyzed(self, event: CorrelationAnalyzed) -> None:
        result = event.result

        if not result:
            return

        try:
            self.add_task(
                "_db_save_correlation",
                (result["coin_id"], result["coin_symbol"]),
                datetime.now(UTC).isoformat(),
                result["correlation"],
                result["market_cap"],
                result["low_cap_quartile"],
                self.session_guid,
                event.timeframe,
            )
        except KeyError as e:
            logger.critical(f"Malformed correlation result, missing key {e}: {result}")

    def _handle_precision_data_fetched(self, event: PrecisionDataFetched) -> None:

        if event.precision_data:
            self.add_task("_db_save_precision_data", event.precision_data, self.session_guid)

    # --- Thread lifecycle ---

    def run(self) -> None:
        self.conn = sqlite3.connect(self.db_name, check_same_thread=False)

        try:
            self._initialize_tables()
            self._initialized_event.set()
            # Call the parent class run() method to manage the work loop
            super().run()
        finally:
            self._close()

    def _initialize_tables(self) -> None:

        try:
            with self.conn:
                self.conn.executescript(SCHEMA)
        except Exception as e:
            self._log_error(f"Error initializing tables: {e}", exc_info=True)
            raise

    def wait_for_queue_completion(self, timeout: float = 30.0) -> bool:
        """Waits until every queued task has been fully processed (not merely dequeued)."""
        work_queue = self.work_queue

        with work_queue.all_tasks_done:
            completed = work_queue.all_tasks_done.wait_for(lambda: work_queue.unfinished_tasks == 0, timeout=timeout)

        if completed:
            logger.info("DatabaseManager queue is empty. All tasks processed.")
        else:
            logger.warning(f"Timeout waiting for DatabaseManager queue to empty. {work_queue.qsize()} tasks remaining.")

        return completed

    def _close(self) -> None:

        try:
            if self.conn:
                self.conn.close()
                logger.info("Database connection closed.")
        except Exception as e:
            self._log_error(f"Error closing database: {e}")

    # --- Persistence tasks (executed in the worker thread) ---

    def _db_save_precision_data(self, precision_data: list[dict], session_guid: str | None) -> None:

        try:
            rows = [
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
            sql = """
                INSERT OR IGNORE INTO precision_data (
                    symbol, quote_asset, base_asset, status, base_asset_precision,
                    step_size, min_qty, tick_size, min_notional, session_guid
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """

            with self.conn:
                self.conn.executemany(sql, rows)

            logger.info(f"{len(rows)} precision records inserted into database.")
        except Exception as e:
            self._log_error(f"Error saving precision data: {e}", exc_info=True)

    def _db_save_token(self, coin: dict, session_guid: str | None) -> None:
        coin_id = coin.get("id")

        if not coin_id:
            self._log_error("Missing 'id' key in token payload.")
            return

        try:
            values = (
                coin_id,
                (coin.get("symbol") or "").upper(),
                session_guid,
                *(convert(coin.get(field)) for field, convert in TOKEN_FIELDS.items()),
            )

            with self.conn:
                self.conn.execute(INSERT_TOKEN_SQL, values)

            logger.info(f"Token {coin_id} saved with session_guid={session_guid}.")
        except Exception as e:
            self._log_error(f"Error saving token {coin_id}: {e}")

    def _db_save_prices(self, coin_id_symbol: CoinRef, prices_df: pd.DataFrame, session_guid: str | None, timeframe: str) -> None:
        coin_id, coin_symbol = coin_id_symbol

        try:
            rows = [
                (coin_id, coin_symbol, row.Index.isoformat(), session_guid, row.open, row.high, row.low, row.close, row.volume, timeframe)
                for row in prices_df.itertuples()
            ]
            sql = """
                INSERT OR IGNORE INTO prices
                (coin_id, coin_symbol, timestamp, session_guid, open, high, low, close, volume, timeframe)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """

            with self.conn:
                self.conn.executemany(sql, rows)

            logger.info(f"{len(rows)} prices for {coin_id} saved (session={session_guid}).")
        except Exception as e:
            self._log_error(f"Error bulk saving prices for {coin_id}: {e}", exc_info=True)

    def _db_save_rsi(self, coin_id_symbol: CoinRef, rsi_series: pd.Series, session_guid: str | None, timeframe: str) -> None:
        coin_id, coin_symbol = coin_id_symbol

        try:
            rows = [
                (coin_id, coin_symbol, ts.isoformat(), session_guid, rsi_value, timeframe)
                for ts, rsi_value in rsi_series.dropna().items()
            ]
            sql = """
                INSERT OR IGNORE INTO rsi (coin_id, coin_symbol, timestamp, session_guid, rsi, timeframe)
                VALUES (?, ?, ?, ?, ?, ?)
            """

            with self.conn:
                self.conn.executemany(sql, rows)

            logger.info(f"{len(rows)} RSI for {coin_id} saved (session={session_guid}).")
        except Exception as e:
            self._log_error(f"Error bulk saving RSI for {coin_id}: {e}", exc_info=True)

    def _db_save_correlation(
        self,
        coin_id_symbol: CoinRef,
        run_timestamp: str,
        correlation: float,
        market_cap: float,
        low_cap_quartile: bool,
        session_guid: str | None,
        timeframe: str,
    ) -> None:
        coin_id, coin_symbol = coin_id_symbol

        try:
            sql = """
                INSERT INTO correlations (coin_id, coin_symbol, run_timestamp, session_guid, correlation, market_cap, low_cap_quartile, timeframe)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """

            with self.conn:
                self.conn.execute(sql, (coin_id, coin_symbol, run_timestamp, session_guid, correlation, market_cap, low_cap_quartile, timeframe))

            logger.info(f"Correlation for {coin_id} saved with session_guid={session_guid}.")
        except Exception as e:
            self._log_error(f"Error saving correlation for {coin_id}: {e}")
