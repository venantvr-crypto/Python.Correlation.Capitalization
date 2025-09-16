import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from data_fetcher import DataFetcher
from events import AnalysisConfigurationProvided, FetchTopCoinsRequested, FetchHistoricalPricesRequested


class TestDataFetcher(unittest.TestCase):

    def setUp(self):
        """Prépare les données de test avant chaque test."""
        self.mock_service_bus = Mock()
        self.fetcher = DataFetcher(service_bus=self.mock_service_bus)

        # Mock les APIs externes
        self.fetcher.cg = Mock()
        self.fetcher.binance = Mock()
        self.fetcher.binance.symbols = ['BTC/USDC', 'ETH/USDC', 'DOGE/USDC']

    def tearDown(self):
        """Nettoie après chaque test."""
        if hasattr(self.fetcher, '_running') and self.fetcher._running and self.fetcher.is_alive():
            self.fetcher.stop()

    def test_configuration_provided(self):
        """Test que la configuration est correctement stockée."""
        event = AnalysisConfigurationProvided(
            session_guid="test-guid",
            config=Mock()
        )
        self.fetcher._handle_configuration_provided(event)
        self.assertEqual(self.fetcher.session_guid, "test-guid")

    def test_fetch_top_coins(self):
        """Test de récupération des top coins."""
        # Préparer les données mock
        mock_coins = [
            {'id': f'coin{i}', 'symbol': f'c{i}', 'market_cap': 1000000 - i * 1000}
            for i in range(150)
        ]
        self.fetcher.cg.get_coins_markets.side_effect = [
            mock_coins[:100],  # Page 1
            mock_coins[100:]  # Page 2
        ]

        # Appeler la méthode
        self.fetcher._fetch_top_coins_task(150)

        # Vérifier que l'API a été appelée correctement
        self.assertEqual(self.fetcher.cg.get_coins_markets.call_count, 2)

        # Vérifier que le résultat a été publié
        self.mock_service_bus.publish.assert_called_with(
            "TopCoinsFetched",
            {'coins': mock_coins[:150]}
        )

    def test_fetch_historical_prices_valid_symbol(self):
        """Test de récupération des prix historiques pour un symbole valide."""
        coin_id_symbol = ('bitcoin', 'btc')
        weeks = 4
        timeframe = '1h'

        # Mock les données OHLCV
        mock_ohlcv = [
            [1609459200000, 100, 105, 99, 102, 1000],  # timestamp, o, h, l, c, v
            [1609462800000, 102, 106, 101, 104, 1100],
        ]
        self.fetcher.binance.fetch_ohlcv.return_value = mock_ohlcv

        # Appeler la méthode
        self.fetcher._fetch_historical_prices_task(coin_id_symbol, weeks, timeframe)

        # Vérifier que l'API Binance a été appelée
        self.fetcher.binance.fetch_ohlcv.assert_called_once()
        call_args = self.fetcher.binance.fetch_ohlcv.call_args
        self.assertEqual(call_args[0][0], 'BTC/USDC')
        self.assertEqual(call_args[0][1], timeframe)

        # Vérifier que le résultat a été publié
        self.mock_service_bus.publish.assert_called()
        published_data = self.mock_service_bus.publish.call_args[0][1]
        self.assertEqual(published_data['coin_id_symbol'], coin_id_symbol)
        self.assertEqual(published_data['timeframe'], timeframe)
        self.assertIsInstance(published_data['prices_df'], pd.DataFrame)

    def test_fetch_historical_prices_invalid_symbol(self):
        """Test avec un symbole invalide."""
        coin_id_symbol = ('fakecoin', 'fake')
        weeks = 4
        timeframe = '1h'

        # Symbole non dans la liste
        self.fetcher.binance.symbols = ['BTC/USDC', 'ETH/USDC']

        # Appeler la méthode
        self.fetcher._fetch_historical_prices_task(coin_id_symbol, weeks, timeframe)

        # Vérifier que None est publié pour les prix
        self.mock_service_bus.publish.assert_called_with(
            "HistoricalPricesFetched",
            {'coin_id_symbol': coin_id_symbol, 'prices_df': None, 'timeframe': timeframe}
        )

    def test_timeframe_limit_calculation(self):
        """Test du calcul correct de la limite selon le timeframe."""
        test_cases = [
            ('1h', 60, 672),  # 4 semaines * 7 jours * 24 heures = 672
            ('1d', 1440, 28),  # 4 semaines * 7 jours = 28
            ('4h', 240, 168),  # 4 semaines * 7 jours * 6 = 168
            ('15m', 15, 1000),  # Limité à 1000 (max Binance)
        ]

        for timeframe, expected_minutes, expected_limit in test_cases:
            with self.subTest(timeframe=timeframe):
                coin_id_symbol = ('bitcoin', 'btc')
                weeks = 4

                self.fetcher.binance.fetch_ohlcv.return_value = []
                self.fetcher._fetch_historical_prices_task(coin_id_symbol, weeks, timeframe)

                # Vérifier la limite passée à l'API
                call_args = self.fetcher.binance.fetch_ohlcv.call_args
                actual_limit = call_args[1]['limit']
                self.assertEqual(actual_limit, expected_limit)

    def test_fetch_precision_data(self):
        """Test de récupération des données de précision."""
        # Mock les données de marché
        mock_markets = {
            'BTC/USDC': {
                'symbol': 'BTC/USDC',
                'base': 'BTC',
                'quote': 'USDC',
                'active': True,
                'info': {
                    'baseAssetPrecision': 8,
                    'filters': [
                        {'filterType': 'LOT_SIZE', 'stepSize': '0.00001', 'minQty': '0.0001'},
                        {'filterType': 'PRICE_FILTER', 'tickSize': '0.01'},
                        {'filterType': 'NOTIONAL', 'minNotional': '10'},
                    ]
                }
            },
            'ETH/USDT': {
                'symbol': 'ETH/USDT',
                'base': 'ETH',
                'quote': 'USDT',
                'active': False,  # Inactif, ne devrait pas être inclus
                'info': {
                    'baseAssetPrecision': 8,
                    'filters': []
                }
            }
        }
        self.fetcher.binance.load_markets.return_value = mock_markets

        # Appeler la méthode
        self.fetcher._fetch_precision_data_task()

        # Vérifier que les données ont été publiées
        self.mock_service_bus.publish.assert_called()
        published_data = self.mock_service_bus.publish.call_args[0][1]['precision_data']

        # Vérifier qu'on n'a que les marchés actifs avec tous les filtres
        self.assertEqual(len(published_data), 1)
        self.assertEqual(published_data[0]['symbol'], 'BTC/USDC')
        self.assertEqual(published_data[0]['base_asset'], 'BTC')

    def test_handle_fetch_requests(self):
        """Test de la mise en file d'attente des demandes."""
        # Test FetchTopCoinsRequested
        event1 = FetchTopCoinsRequested(n=100)
        self.fetcher._handle_fetch_top_coins_requested(event1)

        # Test FetchHistoricalPricesRequested
        event2 = FetchHistoricalPricesRequested(
            coin_id_symbol=('bitcoin', 'btc'),
            weeks=4,
            timeframe='1h'
        )
        self.fetcher._handle_fetch_historical_prices_requested(event2)

        # Vérifier que les tâches sont dans la file d'attente
        self.assertEqual(self.fetcher.work_queue.qsize(), 2)

        task1 = self.fetcher.work_queue.get()
        self.assertEqual(task1[0], '_fetch_top_coins_task')

        task2 = self.fetcher.work_queue.get()
        self.assertEqual(task2[0], '_fetch_historical_prices_task')

    @patch('data_fetcher.logger')
    def test_error_handling_in_fetch_top_coins(self, mock_logger):
        """Test de la gestion d'erreur lors de la récupération des coins."""
        # Simuler une erreur API
        self.fetcher.cg.get_coins_markets.side_effect = Exception("API Error")

        # Appeler la méthode
        self.fetcher._fetch_top_coins_task(100)

        # Vérifier que l'erreur est loggée
        mock_logger.error.assert_called()

        # Vérifier qu'un résultat vide est publié
        self.mock_service_bus.publish.assert_called_with(
            "TopCoinsFetched",
            {'coins': []}
        )

    def test_dataframe_index_is_datetime(self):
        """Test que l'index du DataFrame est bien en datetime."""
        coin_id_symbol = ('bitcoin', 'btc')
        mock_ohlcv = [
            [1609459200000, 100, 105, 99, 102, 1000],
        ]
        self.fetcher.binance.fetch_ohlcv.return_value = mock_ohlcv

        self.fetcher._fetch_historical_prices_task(coin_id_symbol, 1, '1h')

        published_data = self.mock_service_bus.publish.call_args[0][1]
        df = published_data['prices_df']

        # Vérifier que l'index est bien un datetime
        self.assertIsInstance(df.index[0], datetime)
        self.assertEqual(df.index[0].tzinfo, timezone.utc)


if __name__ == '__main__':
    unittest.main()
