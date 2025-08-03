import queue
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional, List, Tuple, Dict

import pandas as pd

from events import SingleCoinFetched, HistoricalPricesFetched, RSICalculated, CorrelationAnalyzed, PrecisionDataFetched
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
        self.work_queue = queue.Queue()
        self._running = True

        if self.service_bus:
            self._setup_event_subscriptions()

    def _setup_event_subscriptions(self):
        """Abonne les gestionnaires d'événements au bus de services."""
        self.service_bus.subscribe("SingleCoinFetched", self._handle_single_coin_fetched)
        self.service_bus.subscribe("HistoricalPricesFetched", self._handle_historical_prices_fetched)
        self.service_bus.subscribe("RSICalculated", self._handle_rsi_calculated)
        self.service_bus.subscribe("CorrelationAnalyzed", self._handle_correlation_analyzed)
        self.service_bus.subscribe("PrecisionDataFetched", self._handle_precision_data_fetched)

    def _handle_single_coin_fetched(self, event: SingleCoinFetched):
        """Met en file d'attente la tâche d'enregistrement d'un token."""
        if event.coin:
            self.work_queue.put(('_db_save_token', (event.coin, event.session_guid), {}, None))

    def _handle_historical_prices_fetched(self, event: HistoricalPricesFetched):
        """Met en file d'attente la tâche d'enregistrement des prix."""
        if event.prices_df is not None:
            self.work_queue.put(
                ('_db_save_prices', (event.coin_id_symbol, event.prices_df, event.session_guid), {}, None))

    def _handle_rsi_calculated(self, event: RSICalculated):
        """Met en file d'attente la tâche d'enregistrement du RSI."""
        if event.rsi is not None:
            self.work_queue.put(('_db_save_rsi', (event.coin_id_symbol, event.rsi, event.session_guid), {}, None))

    def _handle_correlation_analyzed(self, event: CorrelationAnalyzed):
        """Met en file d'attente la tâche d'enregistrement d'une corrélation."""
        result = event.result
        if result:
            task_args = (
                (result['coin_id'], result['coin_symbol']),
                datetime.now(timezone.utc).isoformat(),
                result['correlation'],
                result['market_cap'],
                result['low_cap_quartile'],
                event.session_guid
            )
            self.work_queue.put(('_db_save_correlation', task_args, {}, None))

    def _handle_precision_data_fetched(self, event: PrecisionDataFetched):
        """Met en file d'attente la tâche d'enregistrement des données de précision."""
        if event.precision_data:
            self.work_queue.put(('_db_save_precision_data', (event.precision_data, event.session_guid), {}, None))

    def run(self):
        """Boucle d'exécution du thread qui traite les tâches de la file d'attente."""
        self.conn = sqlite3.connect(self.db_name, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self._initialize_tables()
        logger.info("Thread DatabaseManager démarré.")
        while self._running:
            try:
                task = self.work_queue.get(timeout=1)
                if task is None:
                    break

                method_name, args, kwargs, result_queue = task
                try:
                    method = getattr(self, method_name)
                    result = method(*args, **kwargs)
                    if result_queue:
                        result_queue.put(result)
                except Exception as e:
                    logger.error(f"Erreur d'exécution de la tâche en base de données ({method_name}): {e}")
                    if result_queue:
                        result_queue.put(e)
                finally:
                    self.work_queue.task_done()
            except queue.Empty:
                continue
        self._close()
        logger.info("Thread DatabaseManager arrêté.")

    def stop(self):
        """Arrête le thread en toute sécurité."""
        self.work_queue.put(None)
        self.join()

    def _initialize_tables(self) -> None:
        """Crée les tables de la base de données si elles n'existent pas."""
        try:
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS tokens (
                    coin_id TEXT, session_guid TEXT, symbol TEXT, name TEXT, image TEXT,
                    current_price REAL, market_cap INTEGER, market_cap_rank INTEGER,
                    fully_diluted_valuation INTEGER, total_volume INTEGER, high_24h REAL, low_24h REAL,
                    price_change_24h REAL, price_change_percentage_24h REAL, market_cap_change_24h INTEGER,
                    market_cap_change_percentage_24h REAL, circulating_supply REAL, total_supply REAL,
                    max_supply REAL, ath REAL, ath_change_percentage REAL, ath_date TEXT, atl REAL,
                    atl_change_percentage REAL, atl_date TEXT, roi TEXT, last_updated TEXT,
                    PRIMARY KEY (coin_id, session_guid)
                )
            ''')
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS prices (
                    coin_id TEXT, coin_symbol TEXT, timestamp TIMESTAMP, session_guid TEXT,
                    open REAL, high REAL, low REAL, close REAL,
                    PRIMARY KEY (coin_id, timestamp, session_guid)
                )
            ''')
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS rsi (
                    coin_id TEXT, coin_symbol TEXT, timestamp TIMESTAMP, session_guid TEXT, rsi REAL,
                    PRIMARY KEY (coin_id, timestamp, session_guid)
                )
            ''')
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS correlations (
                    coin_id TEXT, coin_symbol TEXT, run_timestamp TIMESTAMP, session_guid TEXT,
                    correlation REAL, market_cap REAL, low_cap_quartile BOOLEAN,
                    PRIMARY KEY (coin_id, run_timestamp, session_guid)
                )
            ''')
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS precision_data (
                    symbol TEXT, quote_asset TEXT NOT NULL, base_asset TEXT NOT NULL,
                    status TEXT NOT NULL, base_asset_precision INTEGER NOT NULL, step_size REAL NOT NULL,
                    min_qty REAL NOT NULL, tick_size REAL NOT NULL, min_notional REAL NOT NULL,
                    session_guid TEXT,
                    PRIMARY KEY (symbol, quote_asset, base_asset, session_guid)
                )
            ''')
            self.conn.commit()
        except Exception as e:
            logger.error(f"Erreur lors de l'initialisation des tables: {e}")
            raise

    # --- Méthodes publiques pour la récupération de données ---
    def get_prices(self, coin_id_symbol: Tuple[str, str], start_date: datetime, session_guid: Optional[str] = None) -> \
            Optional[pd.DataFrame]:
        """Récupère les prix OHLC depuis la base de manière threadsafe."""
        result_queue = queue.Queue()
        self.work_queue.put(('_db_get_prices', (coin_id_symbol, start_date, session_guid), {}, result_queue))
        result = result_queue.get()
        if isinstance(result, Exception):
            raise result
        return result

    def get_correlations(self, session_guid: Optional[str]) -> List[Tuple]:
        """Récupère les dernières corrélations de manière threadsafe."""
        result_queue = queue.Queue()
        self.work_queue.put(('_db_get_correlations', (session_guid,), {}, result_queue))
        result = result_queue.get()
        if isinstance(result, Exception):
            raise result
        return result

    # --- Tâches de base de données exécutées par le thread ---
    def _db_save_token(self, coin: Dict, session_guid: Optional[str]) -> None:
        """Tâche interne pour enregistrer les informations d'un token."""
        coin_id = coin.get('id')
        try:
            if not coin_id:
                logger.error("Clé 'id' manquante dans le payload du token.")
                return

            def safe_int(value):
                if value is None: return None
                try:
                    return int(float(value)) if isinstance(value, (float, str)) else value
                except (ValueError, TypeError):
                    return None

            def safe_float(value):
                if value is None: return None
                try:
                    return float(value) if isinstance(value, (int, str)) else value
                except (ValueError, TypeError):
                    return None

            def safe_str(value):
                return str(value) if value is not None else None

            values = (
                coin_id,
                session_guid, safe_str(coin.get('symbol')), safe_str(coin.get('name')),
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
                INSERT INTO tokens (
                    coin_id, session_guid, symbol, name, image, current_price, market_cap, market_cap_rank,
                    fully_diluted_valuation, total_volume, high_24h, low_24h, price_change_24h,
                    price_change_percentage_24h, market_cap_change_24h, market_cap_change_percentage_24h,
                    circulating_supply, total_supply, max_supply, ath, ath_change_percentage,
                    ath_date, atl, atl_change_percentage, atl_date, roi, last_updated
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', values)
            self.conn.commit()
            logger.info(f"Token {coin_id} enregistré avec session_guid={session_guid}.")
        except Exception as e:
            logger.error(f"Erreur lors de l'enregistrement du token {coin_id}: {e}")

    def _db_save_prices(self, coin_id_symbol: Tuple[str, str], prices_df: pd.DataFrame,
                        session_guid: Optional[str]) -> None:
        """Tâche interne pour enregistrer les prix OHLC."""
        coin_id, coin_symbol = coin_id_symbol
        try:
            if prices_df.empty:
                logger.warning(f"DataFrame vide pour {coin_id}, aucune donnée enregistrée.")
                return
            for _, row in prices_df.iterrows():
                timestamp = row['timestamp']
                dt = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
                iso_string = dt.isoformat()
                self.cursor.execute('''
                    INSERT INTO prices (coin_id, coin_symbol, timestamp, session_guid, open, high, low, close)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (coin_id, coin_symbol, iso_string, session_guid, row['open'], row['high'], row['low'],
                      row['close']))
            self.conn.commit()
            logger.info(f"Prix pour {coin_id} enregistrés avec session_guid={session_guid}.")
        except Exception as e:
            logger.error(f"Erreur lors de l'enregistrement des prix pour {coin_id}: {e}")

    def _db_save_rsi(self, coin_id_symbol: Tuple[str, str], rsi_series: pd.Series, session_guid: Optional[str]) -> None:
        """Tâche interne pour enregistrer les valeurs RSI."""
        coin_id, coin_symbol = coin_id_symbol
        try:
            if rsi_series.empty:
                logger.warning(f"Série RSI vide pour {coin_id}, aucune donnée enregistrée.")
                return
            for timestamp, rsi_value in rsi_series.items():
                if not pd.isna(rsi_value):
                    # noinspection PyUnresolvedReferences
                    dt = timestamp.to_pydatetime().isoformat()
                    self.cursor.execute('''
                        INSERT INTO rsi (coin_id, coin_symbol, timestamp, session_guid, rsi)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (coin_id, coin_symbol, dt, session_guid, rsi_value))
            self.conn.commit()
            logger.info(f"RSI pour {coin_id} enregistré avec session_guid={session_guid}.")
        except Exception as e:
            logger.error(f"Erreur lors de l'enregistrement du RSI pour {coin_id}: {e}")

    def _db_save_correlation(self, coin_id_symbol: Tuple[str, str], run_timestamp: str, correlation: float,
                             market_cap: float, low_cap_quartile: bool, session_guid: Optional[str]) -> None:
        """Tâche interne pour enregistrer une corrélation."""
        coin_id, coin_symbol = coin_id_symbol
        try:
            self.cursor.execute('''
                INSERT INTO correlations (coin_id, coin_symbol, run_timestamp, session_guid, correlation, market_cap, low_cap_quartile)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (coin_id, coin_symbol, run_timestamp, session_guid, correlation, market_cap, low_cap_quartile))
            self.conn.commit()
            logger.info(f"Corrélation pour {coin_id} enregistrée avec session_guid={session_guid}.")
        except Exception as e:
            logger.error(f"Erreur lors de l'enregistrement de la corrélation pour {coin_id}: {e}")

    def _db_save_precision_data(self, precision_data: List[Dict], session_guid: str) -> None:
        """Tâche interne pour enregistrer les données de précision des marchés."""
        try:
            for data in precision_data:
                values = (
                    data.get('symbol'), data.get('quote_asset'), data.get('base_asset'),
                    data.get('status'), data.get('base_asset_precision'),
                    float(data.get('step_size', '0')), float(data.get('min_qty', '0')),
                    float(data.get('tick_size', '0')), float(data.get('min_notional', '0')),
                    session_guid
                )
                self.cursor.execute('''
                    INSERT INTO precision_data (
                        symbol, quote_asset, base_asset, status, base_asset_precision,
                        step_size, min_qty, tick_size, min_notional, session_guid
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', values)
            self.conn.commit()
            logger.info(f"{len(precision_data)} enregistrements de précision insérés avec session_guid={session_guid}.")
        except Exception as e:
            logger.error(f"Erreur lors de l'enregistrement des données de précision: {e}")

    def _db_get_prices(self, coin_id_symbol: Tuple[str, str], start_date: datetime, session_guid: Optional[str]) -> \
            Optional[pd.DataFrame]:
        """Tâche interne pour récupérer les prix OHLC."""
        coin_id, _ = coin_id_symbol
        try:
            self.cursor.execute('''
                SELECT timestamp, open, high, low, close FROM prices
                WHERE coin_id = ? AND timestamp >= ? AND session_guid = ? ORDER BY timestamp
            ''', (coin_id, start_date.isoformat(), session_guid,))
            rows = self.cursor.fetchall()
            if not rows:
                return None
            df = pd.DataFrame(rows, columns=['timestamp', 'open', 'high', 'low', 'close'],
                              index=[datetime.fromisoformat(row[0]).astimezone(timezone.utc) for row in rows])
            return df
        except Exception as e:
            logger.error(f"Erreur lors de la récupération des prix pour {coin_id}: {e}")
            return None

    def _db_get_correlations(self, session_guid: Optional[str]) -> List[Tuple]:
        """Tâche interne pour récupérer les dernières corrélations."""
        try:
            self.cursor.execute('''
                SELECT coin_id, coin_symbol, run_timestamp, correlation, market_cap, low_cap_quartile
                FROM correlations WHERE session_guid = ? ORDER BY run_timestamp DESC
            ''', (session_guid,))
            return self.cursor.fetchall()
        except Exception as e:
            logger.error(f"Erreur lors de la récupération des corrélations: {e}")
            return []

    def _close(self) -> None:
        """Ferme la connexion à la base de données."""
        try:
            if self.conn:
                self.conn.close()
        except Exception as e:
            logger.error(f"Erreur lors de la fermeture de la base: {e}")
