from unittest.mock import patch, ANY

from crypto_analyzer import CryptoAnalyzer


# On utilise les fixtures définies dans conftest.py
def test_analyzer_initialization(analysis_config):
    """Teste que l'initialisation de l'analyzer est correcte."""
    # On mock le ServiceBus pour ne pas dépendre du réseau
    with patch('crypto_analyzer.ServiceBus') as mock_bus_class:
        analyzer = CryptoAnalyzer(config=analysis_config, session_guid="test-guid")

        # Vérifie que le bus est initialisé avec la bonne URL
        mock_bus_class.assert_called_once_with(url=str(analysis_config.pubsub_url), consumer_name='CryptoAnalyzer')
        assert len(analyzer.analysis_jobs) == len(analysis_config.timeframes)
        assert analyzer.session_guid == "test-guid"


def test_start_analysis_if_ready_starts_processing(analysis_config, mocker):
    """
    Teste que l'analyse démarre et publie les bonnes requêtes
    quand les données initiales sont prêtes.
    """
    with patch('crypto_analyzer.ServiceBus'):
        # Création de l'instance à tester
        analyzer = CryptoAnalyzer(config=analysis_config, session_guid="test-guid")

        # On mock le service_bus de l'instance pour vérifier les appels
        analyzer.service_bus = mocker.MagicMock()

        # On simule la réception des données initiales
        analyzer.coins = [{"id": "bitcoin", "symbol": "BTC", "market_cap": 1000}]
        analyzer.precision_data = {"BTC/USDC": {"base_asset": "BTC", "quote_asset": "USDC"}}

        # On appelle la méthode à tester
        analyzer._start_analysis_if_ready()

        # On vérifie que la méthode a bien publié les événements pour lancer le fetching
        # noinspection PyUnresolvedReferences
        analyzer.service_bus.publish.assert_any_call("FetchHistoricalPricesRequested", ANY, "CryptoAnalyzer")
        assert analyzer._initial_data_loaded.is_set()
