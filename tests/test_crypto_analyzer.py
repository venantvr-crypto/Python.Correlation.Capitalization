import os
import sys
import unittest
from datetime import timezone
from unittest.mock import Mock

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from crypto_analyzer import CryptoAnalyzer
from configuration import AnalysisConfig
from events import TopCoinsFetched, PrecisionDataFetched, RSICalculated


class TestCryptoAnalyzer(unittest.TestCase):

    def setUp(self):
        """Prépare les données de test avant chaque test."""
        self.config = AnalysisConfig(
            weeks=4,
            top_n_coins=100,
            correlation_threshold=0.7,
            rsi_period=14,
            timeframes=['1h', '1d'],
            low_cap_percentile=25.0
        )

        self.analyzer = CryptoAnalyzer(config=self.config)

        # Mock les services
        self.analyzer.service_bus = Mock()
        self.analyzer.db_manager = Mock()
        self.analyzer.data_fetcher = Mock()
        self.analyzer.rsi_calculator = Mock()
        self.analyzer.display_agent = Mock()

    def test_initialization(self):
        """Test de l'initialisation correcte de l'analyseur."""
        self.assertEqual(self.analyzer.config, self.config)
        self.assertEqual(len(self.analyzer.analysis_jobs), 2)
        self.assertIn('1h', self.analyzer.analysis_jobs)
        self.assertIn('1d', self.analyzer.analysis_jobs)
        self.assertEqual(self.analyzer._job_completion_counter, 2)

    def test_handle_top_coins_fetched(self):
        """Test de la gestion de la réception des top coins."""
        coins = [
            {'id': 'bitcoin', 'symbol': 'btc', 'market_cap': 1000000000},
            {'id': 'ethereum', 'symbol': 'eth', 'market_cap': 500000000},
            {'id': 'dogecoin', 'symbol': 'doge', 'market_cap': 10000000}
        ]

        event = TopCoinsFetched(coins=coins)
        self.analyzer._handle_top_coins_fetched(event)

        self.assertEqual(self.analyzer.coins, coins)

    def test_handle_precision_data_fetched(self):
        """Test de la gestion de la réception des données de précision."""
        precision_data = [
            {'symbol': 'BTC/USDC', 'base_asset': 'BTC', 'quote_asset': 'USDC'},
            {'symbol': 'ETH/USDC', 'base_asset': 'ETH', 'quote_asset': 'USDC'},
            {'symbol': 'DOGE/USDC', 'base_asset': 'DOGE', 'quote_asset': 'USDC'}
        ]

        event = PrecisionDataFetched(precision_data=precision_data)
        self.analyzer._handle_precision_data_fetched(event)

        self.assertEqual(len(self.analyzer.precision_data), 3)
        self.assertIn('BTC/USDC', self.analyzer.precision_data)

    def test_start_analysis_if_ready(self):
        """Test du démarrage de l'analyse quand toutes les données sont prêtes."""
        # Préparer les données
        self.analyzer.coins = [
            {'id': 'bitcoin', 'symbol': 'BTC', 'market_cap': 1000000000},
            {'id': 'ethereum', 'symbol': 'ETH', 'market_cap': 500000000},
            {'id': 'dogecoin', 'symbol': 'DOGE', 'market_cap': 10000000}
        ]

        self.analyzer.precision_data = {
            'BTC/USDC': {'symbol': 'BTC/USDC', 'base_asset': 'BTC', 'quote_asset': 'USDC'},
            'ETH/USDC': {'symbol': 'ETH/USDC', 'base_asset': 'ETH', 'quote_asset': 'USDC'}
        }

        # Appeler la méthode
        self.analyzer._start_analysis_if_ready()

        # Vérifier le filtrage des coins
        self.assertEqual(len(self.analyzer.coins), 2)  # Seulement BTC et ETH ont une paire USDC

        # Vérifier le calcul du seuil de capitalisation
        self.assertGreater(self.analyzer.low_cap_threshold, 0)

        # Vérifier que les jobs ont été configurés
        for job in self.analyzer.analysis_jobs.values():
            self.assertGreater(len(job.coins_to_process), 0)

        # Vérifier que les requêtes de prix ont été publiées
        self.analyzer.service_bus.publish.assert_called()

    def test_analyze_correlation_significant(self):
        """Test de l'analyse de corrélation avec une corrélation significative."""
        # Créer des données RSI corrélées
        dates = pd.date_range(start='2024-01-01', periods=30, freq='h', tz=timezone.utc)
        btc_rsi = pd.Series([50 + i for i in range(30)], index=dates)
        coin_rsi = pd.Series([48 + i * 1.1 for i in range(30)], index=dates)  # Corrélation forte

        self.analyzer.market_caps = {'eth': 500000000}
        self.analyzer.low_cap_threshold = 600000000  # ETH est sous le seuil

        # Analyser la corrélation
        self.analyzer.analyze_correlation(
            coin_id_symbol=('ethereum', 'eth'),
            coin_rsi=coin_rsi,
            btc_rsi=btc_rsi,
            timeframe='1h'
        )

        # Vérifier qu'un résultat a été publié
        self.analyzer.service_bus.publish.assert_called_with(
            "CorrelationAnalyzed",
            unittest.mock.ANY
        )

        # Vérifier le contenu du résultat
        call_args = self.analyzer.service_bus.publish.call_args
        result = call_args[0][1]['result']
        self.assertEqual(result['coin_id'], 'ethereum')
        self.assertEqual(result['coin_symbol'], 'eth')
        self.assertGreater(result['correlation'], 0.7)
        self.assertTrue(result['low_cap_quartile'])

    def test_analyze_correlation_not_significant(self):
        """Test avec une corrélation non significative."""
        # Créer des données RSI non corrélées
        dates = pd.date_range(start='2024-01-01', periods=30, freq='h', tz=timezone.utc)
        btc_rsi = pd.Series([50 + i for i in range(30)], index=dates)
        coin_rsi = pd.Series([50 + np.random.randn() * 5 for _ in range(30)], index=dates)

        self.analyzer.market_caps = {'eth': 500000000}
        self.analyzer.low_cap_threshold = 600000000

        # Réinitialiser le mock
        self.analyzer.service_bus.publish.reset_mock()

        # Analyser la corrélation
        self.analyzer.analyze_correlation(
            coin_id_symbol=('ethereum', 'eth'),
            coin_rsi=coin_rsi,
            btc_rsi=btc_rsi,
            timeframe='1h'
        )

        # Vérifier qu'aucun résultat n'a été publié (corrélation trop faible)
        # Le publish pourrait être appelé avec result=None ou pas du tout
        if self.analyzer.service_bus.publish.called:
            call_args = self.analyzer.service_bus.publish.call_args
            if call_args[0][0] == "CorrelationAnalyzed":
                result = call_args[0][1].get('result')
                # Si un résultat est publié, vérifier qu'il respecte le seuil
                if result:
                    self.assertGreaterEqual(abs(result['correlation']), 0.7)

    def test_handle_rsi_calculated(self):
        """Test de la gestion du calcul RSI."""
        # Créer des données RSI
        dates = pd.date_range(start='2024-01-01', periods=30, freq='h', tz=timezone.utc)
        rsi_data = pd.Series([50 + i for i in range(30)], index=dates)

        event = RSICalculated(
            coin_id_symbol=('bitcoin', 'btc'),
            rsi=rsi_data,
            timeframe='1h'
        )

        # Configurer le job
        job = self.analyzer.analysis_jobs['1h']
        job.decrement_counter = Mock()

        # Traiter l'événement
        self.analyzer._handle_rsi_calculated(event)

        # Vérifier que le RSI est stocké
        key = ('bitcoin', 'btc', '1h')
        self.assertIn(key, self.analyzer.rsi_results)
        self.assertEqual(len(self.analyzer.rsi_results[key]), 30)

        # Vérifier que le compteur du job est décrémenté
        job.decrement_counter.assert_called_once()

    def test_handle_rsi_calculated_for_btc(self):
        """Test de la gestion spéciale du RSI de BTC."""
        dates = pd.date_range(start='2024-01-01', periods=30, freq='h', tz=timezone.utc)
        rsi_data = pd.Series([50 + i for i in range(30)], index=dates)

        event = RSICalculated(
            coin_id_symbol=('bitcoin', 'btc'),
            rsi=rsi_data,
            timeframe='1h'
        )

        job = self.analyzer.analysis_jobs['1h']
        job.decrement_counter = Mock()

        self.analyzer._handle_rsi_calculated(event)

        # Vérifier que le RSI de BTC est stocké dans le job
        self.assertIsNotNone(job.btc_rsi)
        pd.testing.assert_series_equal(job.btc_rsi, rsi_data)

    def test_handle_analysis_job_completed(self):
        """Test de la gestion de la complétion d'un job."""
        from events import AnalysisJobCompleted

        # Simuler la complétion du premier job
        event = AnalysisJobCompleted(timeframe='1h')
        self.analyzer._handle_analysis_job_completed(event)

        # Le compteur devrait être à 1
        self.assertEqual(self.analyzer._job_completion_counter, 1)

        # Simuler la complétion du second job
        event = AnalysisJobCompleted(timeframe='1d')
        self.analyzer._handle_analysis_job_completed(event)

        # Le compteur devrait être à 0
        self.assertEqual(self.analyzer._job_completion_counter, 0)

        # Vérifier que FinalResultsReady a été publié
        self.analyzer.service_bus.publish.assert_called_with(
            "FinalResultsReady",
            unittest.mock.ANY
        )

    def test_insufficient_data_for_correlation(self):
        """Test avec des données insuffisantes pour la corrélation."""
        # Créer des données RSI avec peu de points
        dates = pd.date_range(start='2024-01-01', periods=5, freq='h', tz=timezone.utc)
        btc_rsi = pd.Series([50, 51, 52, 53, 54], index=dates)
        coin_rsi = pd.Series([48, 49, 50, 51, 52], index=dates)

        self.analyzer.market_caps = {'eth': 500000000}
        self.analyzer.low_cap_threshold = 600000000

        # Réinitialiser le mock
        self.analyzer.service_bus.publish.reset_mock()

        # Analyser la corrélation
        self.analyzer.analyze_correlation(
            coin_id_symbol=('ethereum', 'eth'),
            coin_rsi=coin_rsi,
            btc_rsi=btc_rsi,
            timeframe='1h'
        )

        # Vérifier qu'aucun résultat n'a été publié (données insuffisantes)
        if self.analyzer.service_bus.publish.called:
            call_args = self.analyzer.service_bus.publish.call_args
            if call_args[0][0] == "CorrelationAnalyzed":
                # Si publié, vérifier qu'il n'y a pas de résultat
                result = call_args[0][1].get('result')
                self.assertIsNone(result)

    def test_market_cap_threshold_calculation(self):
        """Test du calcul du seuil de capitalisation."""
        self.analyzer.coins = [
            {'id': f'coin{i}', 'symbol': f'C{i}', 'market_cap': (i + 1) * 1000000}
            for i in range(100)
        ]

        self.analyzer.precision_data = {
            f'C{i}/USDC': {'base_asset': f'C{i}', 'quote_asset': 'USDC'}
            for i in range(100)
        }

        self.analyzer._start_analysis_if_ready()

        # Le 25e percentile de 1M à 100M devrait être 25.75M
        expected_threshold = pd.Series([i * 1000000 for i in range(1, 101)]).quantile(0.25)
        self.assertAlmostEqual(self.analyzer.low_cap_threshold, expected_threshold, delta=1000000)


if __name__ == '__main__':
    unittest.main()
