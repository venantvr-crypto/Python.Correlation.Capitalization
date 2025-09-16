import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from database_manager import DatabaseManager
from events import AnalysisConfigurationProvided, SingleCoinFetched, HistoricalPricesFetched, RSICalculated, CorrelationAnalyzed


class TestDatabaseManager(unittest.TestCase):

    def setUp(self):
        """Prépare les données de test avant chaque test."""
        # Créer une base de données temporaire
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()

        self.mock_service_bus = Mock()
        self.db_manager = DatabaseManager(db_name=self.db_path, service_bus=self.mock_service_bus)

        # Démarrer le thread et initialiser les tables
        self.db_manager.start()

        # Attendre un peu pour que les tables soient créées
        import time
        time.sleep(0.1)

        # Configuration par défaut
        config_event = AnalysisConfigurationProvided(
            session_guid="test-guid",
            config=Mock()
        )
        self.db_manager._handle_configuration_provided(config_event)

    def tearDown(self):
        """Nettoie après chaque test."""
        if hasattr(self.db_manager, '_running') and self.db_manager._running:
            self.db_manager.stop()

        # Supprimer la base de données temporaire
        try:
            os.unlink(self.db_path)
        except:
            pass

    def test_tables_creation(self):
        """Test que toutes les tables sont créées correctement."""
        # Se connecter directement à la base pour vérifier
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Vérifier l'existence des tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]

        expected_tables = ['tokens', 'prices', 'rsi', 'correlations', 'precision_data']
        for table in expected_tables:
            self.assertIn(table, tables)

        conn.close()

    def test_save_token(self):
        """Test de l'enregistrement d'un token."""
        coin_data = {
            'id': 'bitcoin',
            'symbol': 'btc',
            'name': 'Bitcoin',
            'image': 'http://image.url',
            'current_price': 50000.0,
            'market_cap': 1000000000,
            'market_cap_rank': 1,
            'fully_diluted_valuation': 1050000000,
            'total_volume': 30000000,
            'high_24h': 51000.0,
            'low_24h': 49000.0,
            'price_change_24h': 500.0,
            'price_change_percentage_24h': 1.0,
            'market_cap_change_24h': 10000000,
            'market_cap_change_percentage_24h': 1.0,
            'circulating_supply': 19000000.0,
            'total_supply': 21000000.0,
            'max_supply': 21000000.0,
            'ath': 69000.0,
            'ath_change_percentage': -27.5,
            'ath_date': '2021-11-10',
            'atl': 67.81,
            'atl_change_percentage': 73000.0,
            'atl_date': '2013-07-06',
            'roi': None,
            'last_updated': '2024-01-15T12:00:00Z'
        }

        # Enregistrer le token
        self.db_manager._db_save_token(coin_data, 'test-guid')

        # Attendre que la tâche soit traitée
        self.db_manager.work_queue.join()

        # Vérifier en base
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tokens WHERE coin_id = ?", ('bitcoin',))
        result = cursor.fetchone()

        self.assertIsNotNone(result)
        # Vérifier quelques champs
        self.assertEqual(result[0], 'bitcoin')  # coin_id
        self.assertEqual(result[1], 'BTC')  # coin_symbol
        self.assertEqual(result[2], 'test-guid')  # session_guid

        conn.close()

    def test_save_prices(self):
        """Test de l'enregistrement des prix."""
        prices_data = pd.DataFrame({
            'timestamp': [1609459200000, 1609462800000],
            'open': [100.0, 102.0],
            'high': [105.0, 106.0],
            'low': [99.0, 101.0],
            'close': [102.0, 104.0],
            'volume': [1000.0, 1100.0]
        })

        coin_id_symbol = ('bitcoin', 'btc')
        self.db_manager._db_save_prices(coin_id_symbol, prices_data, 'test-guid', '1h')

        # Attendre que la tâche soit traitée
        self.db_manager.work_queue.join()

        # Vérifier en base
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM prices WHERE coin_id = ?", ('bitcoin',))
        count = cursor.fetchone()[0]

        self.assertEqual(count, 2)
        conn.close()

    def test_save_rsi(self):
        """Test de l'enregistrement des valeurs RSI."""
        rsi_data = pd.Series(
            [50.0, 55.0, 60.0],
            index=pd.date_range(start='2024-01-01', periods=3, freq='h', tz=timezone.utc)
        )

        coin_id_symbol = ('bitcoin', 'btc')
        self.db_manager._db_save_rsi(coin_id_symbol, rsi_data, 'test-guid', '1h')

        # Attendre que la tâche soit traitée
        self.db_manager.work_queue.join()

        # Vérifier en base
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM rsi WHERE coin_id = ?", ('bitcoin',))
        count = cursor.fetchone()[0]

        self.assertEqual(count, 3)

        # Vérifier les valeurs
        cursor.execute("SELECT rsi FROM rsi WHERE coin_id = ? ORDER BY timestamp", ('bitcoin',))
        values = [row[0] for row in cursor.fetchall()]
        self.assertEqual(values, [50.0, 55.0, 60.0])

        conn.close()

    def test_save_correlation(self):
        """Test de l'enregistrement d'une corrélation."""
        self.db_manager._db_save_correlation(
            coin_id_symbol=('ethereum', 'eth'),
            run_timestamp='2024-01-15T12:00:00Z',
            correlation=0.85,
            market_cap=200000000.0,
            low_cap_quartile=False,
            session_guid='test-guid',
            timeframe='1d'
        )

        # Attendre que la tâche soit traitée
        self.db_manager.work_queue.join()

        # Vérifier en base
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM correlations WHERE coin_id = ?", ('ethereum',))
        result = cursor.fetchone()

        self.assertIsNotNone(result)
        self.assertEqual(result[0], 'ethereum')
        self.assertEqual(result[1], 'eth')
        self.assertEqual(result[4], 0.85)  # correlation
        self.assertEqual(result[5], 200000000.0)  # market_cap

        conn.close()

    def test_save_precision_data(self):
        """Test de l'enregistrement des données de précision."""
        precision_data = [
            {
                'symbol': 'BTC/USDC',
                'quote_asset': 'USDC',
                'base_asset': 'BTC',
                'status': True,
                'base_asset_precision': 8,
                'step_size': '0.00001',
                'min_qty': '0.0001',
                'tick_size': '0.01',
                'min_notional': '10'
            }
        ]

        self.db_manager._db_save_precision_data(precision_data, 'test-guid')

        # Attendre que la tâche soit traitée
        self.db_manager.work_queue.join()

        # Vérifier en base
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM precision_data WHERE symbol = ?", ('BTC/USDC',))
        result = cursor.fetchone()

        self.assertIsNotNone(result)
        self.assertEqual(result[0], 'BTC/USDC')
        self.assertEqual(result[1], 'USDC')
        self.assertEqual(result[2], 'BTC')

        conn.close()

    def test_get_prices(self):
        """Test de récupération des prix."""
        # D'abord enregistrer des prix
        prices_data = pd.DataFrame({
            'timestamp': [1609459200000, 1609462800000],
            'open': [100.0, 102.0],
            'high': [105.0, 106.0],
            'low': [99.0, 101.0],
            'close': [102.0, 104.0],
            'volume': [1000.0, 1100.0]
        })

        coin_id_symbol = ('bitcoin', 'btc')
        self.db_manager._db_save_prices(coin_id_symbol, prices_data, 'test-guid', '1h')
        self.db_manager.work_queue.join()

        # Récupérer les prix
        start_date = datetime(2020, 1, 1, tzinfo=timezone.utc)
        result = self.db_manager.get_prices(coin_id_symbol, start_date, 'test-guid', '1h')

        self.assertIsNotNone(result)
        self.assertIsInstance(result, pd.DataFrame)
        self.assertEqual(len(result), 2)
        self.assertIn('close', result.columns)

    def test_get_correlations(self):
        """Test de récupération des corrélations."""
        # D'abord enregistrer une corrélation
        self.db_manager._db_save_correlation(
            coin_id_symbol=('ethereum', 'eth'),
            run_timestamp='2024-01-15T12:00:00Z',
            correlation=0.85,
            market_cap=200000000.0,
            low_cap_quartile=False,
            session_guid='test-guid',
            timeframe='1d'
        )
        self.db_manager.work_queue.join()

        # Récupérer les corrélations
        result = self.db_manager.get_correlations('test-guid', '1d')

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], 'ethereum')
        self.assertEqual(result[0][3], 0.85)

    def test_handle_events(self):
        """Test de la gestion des événements."""
        # Test SingleCoinFetched
        event = SingleCoinFetched(coin={'id': 'bitcoin', 'symbol': 'btc'})
        self.db_manager._handle_single_coin_fetched(event)
        self.assertEqual(self.db_manager.work_queue.qsize(), 1)

        # Test HistoricalPricesFetched
        prices_df = pd.DataFrame({'timestamp': [1609459200000], 'close': [100.0]})
        event = HistoricalPricesFetched(
            coin_id_symbol=('bitcoin', 'btc'),
            prices_df=prices_df,
            timeframe='1h'
        )
        self.db_manager._handle_historical_prices_fetched(event)
        self.assertEqual(self.db_manager.work_queue.qsize(), 2)

        # Test RSICalculated
        rsi_series = pd.Series([50.0], index=[datetime.now(timezone.utc)])
        event = RSICalculated(
            coin_id_symbol=('bitcoin', 'btc'),
            rsi=rsi_series,
            timeframe='1h'
        )
        self.db_manager._handle_rsi_calculated(event)
        self.assertEqual(self.db_manager.work_queue.qsize(), 3)

        # Test CorrelationAnalyzed
        event = CorrelationAnalyzed(
            result={
                'coin_id': 'ethereum',
                'coin_symbol': 'eth',
                'correlation': 0.85,
                'market_cap': 200000000.0,
                'low_cap_quartile': False
            },
            timeframe='1d'
        )
        self.db_manager._handle_correlation_analyzed(event)
        self.assertEqual(self.db_manager.work_queue.qsize(), 4)

    def test_thread_stop(self):
        """Test de l'arrêt correct du thread."""
        self.assertTrue(self.db_manager._running)
        self.db_manager.stop()
        self.assertFalse(self.db_manager._running)
        self.assertFalse(self.db_manager.is_alive())


if __name__ == '__main__':
    unittest.main()
