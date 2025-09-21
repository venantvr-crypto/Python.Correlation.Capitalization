import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from configuration import AnalysisConfig
from crypto_analyzer import CryptoAnalyzer
from events import TopCoinsFetched


class TestCryptoAnalyzer(unittest.TestCase):

    def setUp(self):
        """Prépare les données de test avant chaque test."""
        self.config = AnalysisConfig(weeks=4, top_n_coins=100)
        # Le session_guid est maintenant requis par le constructeur
        self.analyzer = CryptoAnalyzer(config=self.config, session_guid="test-guid")

        # Remplacement des services par des MagicMock pour isoler les tests
        self.analyzer.service_bus = MagicMock()
        self.analyzer.db_manager = MagicMock()
        self.analyzer.data_fetcher = MagicMock()
        self.analyzer.rsi_calculator = MagicMock()
        self.analyzer.display_agent = MagicMock()

    def test_handle_top_coins_fetched(self):
        """Test de la gestion de la réception des top coins."""
        coins = [{"id": "bitcoin", "symbol": "btc", "market_cap": 1000}]
        event = TopCoinsFetched(coins=coins)
        self.analyzer._handle_top_coins_fetched(event)
        self.assertEqual(self.analyzer.coins, coins)

    def test_start_analysis_if_ready(self):
        """Test du démarrage de l'analyse quand toutes les données sont prêtes."""
        self.analyzer.coins = [{"id": "bitcoin", "symbol": "BTC", "market_cap": 1000}]
        self.analyzer.precision_data = {"BTC/USDC": {"base_asset": "BTC", "quote_asset": "USDC"}}

        self.analyzer._start_analysis_if_ready()

        self.analyzer.service_bus.publish.assert_called()
