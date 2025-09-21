import os
import sys
import unittest
from unittest.mock import Mock

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from configuration import AnalysisConfig
from events import AnalysisConfigurationProvided
from rsi_calculator import RSICalculator


class TestRSICalculator(unittest.TestCase):

    def setUp(self):
        self.mock_service_bus = Mock()
        self.calculator = RSICalculator(service_bus=self.mock_service_bus)
        config = AnalysisConfig(rsi_period=14)
        config_event = AnalysisConfigurationProvided(session_guid="test-guid", config=config)
        self.calculator._handle_configuration_provided(config_event)

    def tearDown(self):
        if self.calculator.is_alive():
            self.calculator.stop()

    def test_rsi_calculation_basic(self):
        # Utilisation de dtype=float pour la robustesse
        prices = pd.Series([100 + i for i in range(30)], dtype=float)
        self.calculator._calculate_rsi_task(("bitcoin", "btc"), prices, "1h")
        self.mock_service_bus.publish.assert_called()
        call_args = self.mock_service_bus.publish.call_args[0]
        self.assertEqual(call_args[0], "RSICalculated")
        self.assertIsInstance(call_args[1]["rsi"], pd.Series)
