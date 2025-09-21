import os
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_fetcher import DataFetcher
from events import AnalysisConfigurationProvided


class TestDataFetcher(unittest.TestCase):

    def setUp(self):
        self.mock_service_bus = Mock()
        self.fetcher = DataFetcher(service_bus=self.mock_service_bus)
        self.fetcher.cg = Mock()
        self.fetcher.binance = Mock()
        self.fetcher.binance.symbols = ["BTC/USDC", "ETH/USDC"]

    def tearDown(self):
        if self.fetcher.is_alive():
            self.fetcher.stop()

    def test_configuration_provided(self):
        event = AnalysisConfigurationProvided(session_guid="test-guid", config=Mock())
        self.fetcher._handle_configuration_provided(event)
        self.assertEqual(self.fetcher.session_guid, "test-guid")

    @patch("data_fetcher.logger")
    def test_fetch_top_coins_task(self, mock_logger):
        mock_coins = [{"id": "bitcoin"}]
        self.fetcher.cg.get_coins_markets.return_value = mock_coins
        self.fetcher._fetch_top_coins_task(1)
        self.mock_service_bus.publish.assert_called_with("TopCoinsFetched", {"coins": mock_coins})
