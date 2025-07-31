import queue
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Union, Optional, List, Tuple, Dict

import pandas as pd

from events import SingleCoinFetched, HistoricalPricesFetched, RSICalculated, CorrelationAnalyzed
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
        self.service_bus.subscribe("SingleCoinFetched", self._handle_single_coin_fetched)
        self.service_bus.subscribe("HistoricalPricesFetched", self._handle_historical_prices_fetched)
        self.service_bus.subscribe("RSICalculated", self._handle_rsi_calculated)
        self.service_bus.subscribe("CorrelationAnalyzed", self._handle_correlation_analyzed)

    def _handle_single_coin_fetched(self, event: SingleCoinFetched):
        # Accès direct aux attributs de l'objet dataclass
        coin = event.coin
        session_guid = event.session_guid
        if coin:
            self.save_token(coin, session_guid)

    def _handle_historical_prices_fetched(self, event: HistoricalPricesFetched):
        # Accès direct aux attributs de l'objet dataclass
        coin_id_symbol = event.coin_id_symbol
        prices_df = event.prices_df
        session_guid = event.session_guid
        if prices_df is not None:
            self.save_prices(coin_id_symbol, prices_df, session_guid)

    def _handle_rsi_calculated(self, event: RSICalculated):
        # Accès direct aux attributs de l'objet dataclass
        coin_id_symbol = event.coin_id_symbol
        rsi_series = event.rsi
        session_guid = event.session_guid
        if rsi_series is not None:
            self.save_rsi(coin_id_symbol, rsi_series, session_guid)

    def _handle_correlation_analyzed(self, event: CorrelationAnalyzed):
        # Accès direct aux attributs de l'objet dataclass
        result = event.result
        session_guid = event.session_guid
        if result:
            self.save_correlation(
                (result['coin_id'], result['coin_symbol']),
                datetime.now(timezone.utc).isoformat(),
                result['correlation'],
                result['market_cap'],
                result['low_cap_quartile'],
                session_guid
            )

    def run(self):
        """Boucle d'exécution du thread."""
        self.conn = sqlite3.connect(self.db_name, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self._initialize_tables()
        logger.info("Thread DatabaseManager démarré.")
        while self._running:
            try:
                # Utilisation d'un timeout pour permettre au thread de s'arrêter
                task = self.work_queue.get(timeout=1)
                if task is None:
                    # Signal pour arrêter le thread
                    self._running = False
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
                # Le thread peut continuer à tourner même si la queue est vide
                continue
        self.close()
        logger.info("Thread DatabaseManager arrêté.")

    def stop(self):
        """Arrête le thread en toute sécurité."""
        self.work_queue.put(None)
        self.join()

    def _initialize_tables(self) -> None:
        """Crée les tables nécessaires si elles n'existent pas."""
        try:
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS tokens (
                    coin_id TEXT,
                    coin_symbol TEXT,
                    session_guid TEXT,
                    symbol TEXT,
                    name TEXT,
                    image TEXT,
                    current_price REAL,
                    market_cap INTEGER,
                    market_cap_rank INTEGER,
                    fully_diluted_valuation INTEGER,
                    total_volume INTEGER,
                    high_24h REAL,
                    low_24h REAL,
                    price_change_24h REAL,
                    price_change_percentage_24h REAL,
                    market_cap_change_24h INTEGER,
                    market_cap_change_percentage_24h REAL,
                    circulating_supply REAL,
                    total_supply REAL,
                    max_supply REAL,
                    ath REAL,
                    ath_change_percentage REAL,
                    ath_date TEXT,
                    atl REAL,
                    atl_change_percentage REAL,
                    atl_date TEXT,
                    roi TEXT,
                    last_updated TEXT,
                    PRIMARY KEY (coin_id, session_guid)
                )
            ''')
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS prices (
                    coin_id TEXT,
                    coin_symbol TEXT,
                    timestamp TIMESTAMP,
                    session_guid TEXT,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    PRIMARY KEY (coin_id, timestamp, session_guid)
                )
            ''')
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS rsi (
                    coin_id TEXT,
                    coin_symbol TEXT,
                    timestamp TIMESTAMP,
                    session_guid TEXT,
                    rsi REAL,
                    PRIMARY KEY (coin_id, timestamp, session_guid)
                )
            ''')
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS correlations (
                    coin_id TEXT,
                    coin_symbol TEXT,
                    run_timestamp TIMESTAMP,
                    session_guid TEXT,
                    correlation REAL,
                    market_cap REAL,
                    low_cap_quartile BOOLEAN,
                    PRIMARY KEY (coin_id, run_timestamp, session_guid)
                )
            ''')
            self.conn.commit()
        except Exception as e:
            logger.error(f"Erreur lors de l'initialisation des tables: {e}")
            raise

    # Méthodes publiques qui envoient des tâches à la file d'attente
    def save_token(self, coin: Dict, session_guid: Optional[str]) -> None:
        self.work_queue.put(('_save_token_task', (coin, session_guid), {}, None))

    def save_prices(self, coin_id_symbol: Tuple[str, str], prices_df: pd.DataFrame,
                    session_guid: Optional[str]) -> None:
        self.work_queue.put(('_save_prices_task', (coin_id_symbol, prices_df, session_guid), {}, None))

    def save_rsi(self, coin_id_symbol: Tuple[str, str], rsi_series: pd.Series, session_guid: Optional[str]) -> None:
        self.work_queue.put(('_save_rsi_task', (coin_id_symbol, rsi_series, session_guid), {}, None))

    def save_correlation(self, coin_id_symbol: Tuple[str, str], run_timestamp: str, correlation: float,
                         market_cap: float, low_cap_quartile: bool, session_guid: Optional[str]) -> None:
        self.work_queue.put(('_save_correlation_task', (coin_id_symbol, run_timestamp, correlation, market_cap,
                                                        low_cap_quartile, session_guid), {}, None))

    def get_prices(self, coin_id_symbol: Tuple[str, str], start_date: datetime, session_guid: Optional[str] = None) -> (
            Optional)[pd.DataFrame]:
        """Récupère les prix OHLC depuis la base de manière threadsafe."""
        result_queue = queue.Queue()
        self.work_queue.put(('_get_prices_task', (coin_id_symbol, start_date, session_guid), {}, result_queue))
        result = result_queue.get()
        if isinstance(result, Exception):
            raise result
        return result

    def get_correlations(self, session_guid: Optional[str]) -> List[Tuple]:
        """Récupère les dernières corrélations de manière threadsafe."""
        result_queue = queue.Queue()
        self.work_queue.put(('_get_correlations_task', (session_guid,), {}, result_queue))
        result = result_queue.get()
        if isinstance(result, Exception):
            raise result
        return result

    def _save_token_task(self, coin: Dict, session_guid: Optional[str]) -> None:
        coin_id = coin.get('id')
        try:
            if not coin_id:
                logger.error("Clé 'id' manquante dans le payload du token.")
                return

            def safe_int(value):
                if value is None:
                    return None
                try:
                    return int(float(value)) if isinstance(value, (float, str)) else value
                except (ValueError, TypeError):
                    logger.warning(f"Conversion en int échouée pour {value}, retour à None.")
                    return None

            def safe_float(value):
                if value is None:
                    return None
                try:
                    return float(value) if isinstance(value, (int, str)) else value
                except (ValueError, TypeError):
                    logger.warning(f"Conversion en float échouée pour {value}, retour à None.")
                    return None

            def safe_str(value):
                if value is None:
                    return None
                return str(value)

            values = (
                coin_id,
                session_guid,
                safe_str(coin.get('symbol')),
                safe_str(coin.get('name')),
                safe_str(coin.get('image')),
                safe_float(coin.get('current_price')),
                safe_int(coin.get('market_cap')),
                safe_int(coin.get('market_cap_rank')),
                safe_int(coin.get('fully_diluted_valuation')),
                safe_int(coin.get('total_volume')),
                safe_float(coin.get('high_24h')),
                safe_float(coin.get('low_24h')),
                safe_float(coin.get('price_change_24h')),
                safe_float(coin.get('price_change_percentage_24h')),
                safe_int(coin.get('market_cap_change_24h')),
                safe_float(coin.get('market_cap_change_percentage_24h')),
                safe_float(coin.get('circulating_supply')),
                safe_float(coin.get('total_supply')),
                safe_float(coin.get('max_supply')),
                safe_float(coin.get('ath')),
                safe_float(coin.get('ath_change_percentage')),
                safe_str(coin.get('ath_date')),
                safe_float(coin.get('atl')),
                safe_float(coin.get('atl_change_percentage')),
                safe_str(coin.get('atl_date')),
                safe_str(coin.get('roi')),
                safe_str(coin.get('last_updated'))
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

    def _save_prices_task(self, coin_id_symbol: Tuple[str, str], prices_df: pd.DataFrame,
                          session_guid: Optional[str]) -> None:
        """Tâche interne pour enregistrer les prix OHLC en utilisant une conversion de timestamp robuste."""
        coin_id, coin_symbol = coin_id_symbol
        try:
            if prices_df.empty:
                logger.warning(f"DataFrame vide pour {coin_id}, aucune donnée enregistrée.")
                return
            for timestamp, row in prices_df.iterrows():
                # dt = timestamp.to_pydatetime()
                # timestamp = timestamps[i]
                timestamp = row['timestamp']
                # Convertir en secondes et créer un objet datetime
                dt = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
                # Formater en ISO 8601
                # iso_string = dt.isoformat()
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

    def _save_rsi_task(self, coin_id_symbol: Tuple[str, str], rsi_series: pd.Series,
                       session_guid: Optional[str]) -> None:
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
        except Exception as e:
            logger.error(f"Erreur lors de l'enregistrement du RSI pour {coin_id}: {e}")

    def _save_correlation_task(self, coin_id_symbol: Tuple[str, str], run_timestamp: str, correlation: float,
                               market_cap: float, low_cap_quartile: bool, session_guid: Optional[str]) -> None:
        """Tâche interne pour enregistrer une corrélation."""
        coin_id, coin_symbol = coin_id_symbol
        try:
            self.cursor.execute('''
                INSERT INTO correlations (coin_id, coin_symbol, run_timestamp, session_guid, correlation, market_cap, low_cap_quartile)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (coin_id, coin_symbol, run_timestamp, session_guid, correlation, market_cap, low_cap_quartile))
            self.conn.commit()
        except Exception as e:
            logger.error(f"Erreur lors de l'enregistrement de la corrélation pour {coin_id}: {e}")

    def _get_prices_task(self, coin_id_symbol: Tuple[str, str], start_date: datetime, session_guid: Optional[str]) -> (
            Optional)[pd.DataFrame]:
        """Tâche interne pour récupérer les prix OHLC depuis la base."""
        coin_id, coin_symbol = coin_id_symbol
        try:
            self.cursor.execute('''
                SELECT timestamp, open, high, low, close FROM prices
                WHERE coin_id = ? AND timestamp >= ? AND session_guid = ?
                ORDER BY timestamp
            ''', (coin_id, start_date.isoformat(), session_guid,))
            rows = self.cursor.fetchall()
            if not rows:
                return None
            df = pd.DataFrame(
                rows,
                columns=['timestamp', 'open', 'high', 'low', 'close'],
                index=[datetime.fromisoformat(row[0]).astimezone(timezone.utc) for row in rows]
            )
            return df
        except Exception as e:
            logger.error(f"Erreur lors de la récupération des prix pour {coin_id}: {e}")
            return None

    def _get_correlations_task(self, session_guid: Optional[str]) -> List[Tuple]:
        """Tâche interne pour récupérer les dernières corrélations."""
        try:
            self.cursor.execute('''
                SELECT coin_id, coin_symbol, run_timestamp, correlation, market_cap, low_cap_quartile
                FROM correlations
                WHERE session_guid = ?
                ORDER BY run_timestamp DESC
            ''', (session_guid,))
            return self.cursor.fetchall()
        except Exception as e:
            logger.error(f"Erreur lors de la récupération des corrélations: {e}")
            return []

    @staticmethod
    def _ensure_datetime(timestamp: Union[int, float, str, datetime, pd.Timestamp]) -> Optional[datetime]:
        """Convertit un timestamp en objet datetime UTC."""
        try:
            if isinstance(timestamp, datetime):
                if timestamp.tzinfo is None:
                    return timestamp.replace(tzinfo=timezone.utc)
                return timestamp.astimezone(timezone.utc)
            if isinstance(timestamp, str):
                return datetime.fromisoformat(timestamp.replace('Z', '+00:00')).astimezone(timezone.utc)
            if isinstance(timestamp, (int, float)):
                return datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
            return pd.Timestamp(timestamp).to_pydatetime().replace(tzinfo=timezone.utc)
        except Exception as e:
            logger.error(f"Erreur de conversion de timestamp: {timestamp}, {e}")
            return None

    def close(self) -> None:
        """Ferme la connexion à la base."""
        try:
            if self.conn:
                self.conn.close()
        except Exception as e:
            logger.error(f"Erreur lors de la fermeture de la base: {e}")
