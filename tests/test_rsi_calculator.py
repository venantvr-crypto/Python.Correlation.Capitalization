import os
import sys
import unittest
from unittest.mock import Mock

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from rsi_calculator import RSICalculator
from events import AnalysisConfigurationProvided, CalculateRSIRequested
from configuration import AnalysisConfig


class TestRSICalculator(unittest.TestCase):

    def setUp(self):
        """Prépare les données de test avant chaque test."""
        self.mock_service_bus = Mock()
        self.calculator = RSICalculator(service_bus=self.mock_service_bus)

        # Configuration par défaut
        self.config = AnalysisConfig(rsi_period=14)
        config_event = AnalysisConfigurationProvided(
            session_guid="test-guid",
            config=self.config
        )
        self.calculator._handle_configuration_provided(config_event)

    def tearDown(self):
        """Nettoie après chaque test."""
        if hasattr(self.calculator, '_running') and self.calculator._running and self.calculator.is_alive():
            self.calculator.stop()

    def test_configuration_provided(self):
        """Test que la configuration est correctement stockée."""
        self.assertEqual(self.calculator.session_guid, "test-guid")
        self.assertEqual(self.calculator.periods, 14)

    def test_rsi_calculation_basic(self):
        """Test du calcul RSI avec des données simples."""
        # Créer une série de prix avec une tendance claire
        prices = pd.Series([
            100, 102, 101, 103, 105, 104, 106, 108, 107, 109,
            111, 110, 112, 114, 113, 115, 117, 116, 118, 120
        ])

        coin_id_symbol = ('bitcoin', 'btc')
        timeframe = '1h'

        # Appeler directement la méthode de calcul
        self.calculator._calculate_rsi_task(coin_id_symbol, prices, timeframe)

        # Vérifier que le résultat a été publié
        self.mock_service_bus.publish.assert_called()
        call_args = self.mock_service_bus.publish.call_args

        self.assertEqual(call_args[0][0], "RSICalculated")
        result = call_args[0][1]
        self.assertEqual(result['coin_id_symbol'], coin_id_symbol)
        self.assertEqual(result['timeframe'], timeframe)
        self.assertIsNotNone(result['rsi'])
        self.assertIsInstance(result['rsi'], pd.Series)

        # Vérifier que le RSI a les bonnes propriétés
        rsi = result['rsi']
        self.assertTrue((rsi >= 0).all())
        self.assertTrue((rsi <= 100).all())
        # Les premières valeurs doivent être supprimées (NaN)
        self.assertGreater(len(prices), len(rsi))

    def test_rsi_calculation_insufficient_data(self):
        """Test avec des données insuffisantes."""
        prices = pd.Series([100, 102, 101])  # Seulement 3 prix

        coin_id_symbol = ('bitcoin', 'btc')
        timeframe = '1h'

        self.calculator._calculate_rsi_task(coin_id_symbol, prices, timeframe)

        # Vérifier que None est publié pour le RSI
        call_args = self.mock_service_bus.publish.call_args
        result = call_args[0][1]
        self.assertIsNone(result['rsi'])

    def test_rsi_calculation_with_zeros(self):
        """Test du calcul RSI avec des valeurs nulles dans les pertes."""
        # Créer une série avec seulement des gains (pas de pertes)
        prices = pd.Series([100 + i for i in range(20)])

        coin_id_symbol = ('ethereum', 'eth')
        timeframe = '1d'

        self.calculator._calculate_rsi_task(coin_id_symbol, prices, timeframe)

        call_args = self.mock_service_bus.publish.call_args
        result = call_args[0][1]
        rsi = result['rsi']

        # Avec seulement des gains, le RSI devrait tendre vers 100
        self.assertTrue((rsi.dropna() > 70).all())

    def test_rsi_calculation_volatile_data(self):
        """Test avec des données très volatiles."""
        # Créer une série oscillante
        prices = pd.Series([100 + 10 * ((-1) ** i) for i in range(30)])

        coin_id_symbol = ('dogecoin', 'doge')
        timeframe = '4h'

        self.calculator._calculate_rsi_task(coin_id_symbol, prices, timeframe)

        call_args = self.mock_service_bus.publish.call_args
        result = call_args[0][1]
        rsi = result['rsi']

        # Avec des oscillations régulières, le RSI devrait être proche de 50
        mean_rsi = rsi.dropna().mean()
        self.assertGreater(mean_rsi, 30)
        self.assertLess(mean_rsi, 70)

    def test_handle_calculate_rsi_requested(self):
        """Test de la mise en file d'attente des demandes de calcul."""
        prices = pd.Series([100 + i for i in range(20)])
        event = CalculateRSIRequested(
            coin_id_symbol=('bitcoin', 'btc'),
            prices_series=prices,
            timeframe='1h'
        )

        self.calculator._handle_calculate_rsi_requested(event)

        # Vérifier que la tâche est dans la file d'attente
        self.assertFalse(self.calculator.work_queue.empty())
        task = self.calculator.work_queue.get()
        self.assertEqual(task[0], '_calculate_rsi_task')
        self.assertEqual(task[1][0], ('bitcoin', 'btc'))

    def test_rsi_without_configuration(self):
        """Test que le calcul échoue sans configuration."""
        calculator = RSICalculator(service_bus=self.mock_service_bus)
        prices = pd.Series([100 + i for i in range(20)])

        calculator._calculate_rsi_task(('bitcoin', 'btc'), prices, '1h')

        # Vérifier qu'une erreur est publiée
        call_args = self.mock_service_bus.publish.call_args
        result = call_args[0][1]
        self.assertIsNone(result['rsi'])

    def test_dropna_removes_initial_nan(self):
        """Test que dropna() supprime correctement les NaN initiaux."""
        prices = pd.Series([100 + i for i in range(30)])

        self.calculator._calculate_rsi_task(('bitcoin', 'btc'), prices, '1h')

        call_args = self.mock_service_bus.publish.call_args
        result = call_args[0][1]
        rsi = result['rsi']

        # Vérifier qu'il n'y a pas de NaN dans le résultat
        self.assertFalse(rsi.isna().any())
        # Vérifier que la longueur est réduite
        self.assertLess(len(rsi), len(prices))


if __name__ == '__main__':
    unittest.main()
