from unittest.mock import ANY, patch

import pandas as pd

# noinspection PyPackageRequirements
import pytest

from crypto_analyzer import CryptoAnalyzer
from tests.conftest import published_events


@pytest.fixture
def analyzer(analysis_config, mocker):
    """Un analyzer dont le ServiceBus est mocké pour ne pas dépendre du réseau."""
    with patch("crypto_analyzer.ServiceBus"):
        analyzer = CryptoAnalyzer(config=analysis_config, session_guid="test-guid")

    analyzer.service_bus = mocker.MagicMock()
    return analyzer


def test_analyzer_initialization(analysis_config):
    """Teste que l'initialisation de l'analyzer est correcte."""
    with patch("crypto_analyzer.ServiceBus") as mock_bus_class:
        analyzer = CryptoAnalyzer(config=analysis_config, session_guid="test-guid")

    # Vérifie que le bus est initialisé avec la bonne URL
    mock_bus_class.assert_called_once_with(url=str(analysis_config.pubsub_url), consumer_name="CryptoAnalyzer")
    assert len(analyzer.analysis_jobs) == len(analysis_config.timeframes)
    assert analyzer.session_guid == "test-guid"
    # Les services enregistrés ne doivent pas être écrasés par l'initialisation.
    assert analyzer.db_manager is not None
    assert len(analyzer.services) == 4


def test_start_analysis_if_ready_starts_processing(analyzer):
    """
    Teste que l'analyse démarre et publie les bonnes requêtes
    quand les données initiales sont prêtes.
    """
    # On simule la réception des données initiales
    analyzer.coins = [
        {"id": "bitcoin", "symbol": "BTC", "market_cap": 1000},
        {"id": "ethereum", "symbol": "eth", "market_cap": None},
        {"id": "no-usdc-pair", "symbol": "xyz", "market_cap": 5},
    ]
    analyzer.precision_data = {
        "BTC/USDC": {"base_asset": "BTC", "quote_asset": "USDC"},
        "ETH/USDC": {"base_asset": "ETH", "quote_asset": "USDC"},
        "XYZ/USDT": {"base_asset": "XYZ", "quote_asset": "USDT"},
    }

    analyzer._start_analysis_if_ready()

    analyzer.service_bus.publish.assert_any_call("FetchHistoricalPricesRequested", ANY, "CryptoAnalyzer")
    assert analyzer._initial_data_loaded.is_set()

    requested = [e.coin_id_symbol for e in published_events(analyzer.service_bus, "FetchHistoricalPricesRequested")]
    assert requested == [("bitcoin", "btc"), ("ethereum", "eth")]
    # Une capitalisation nulle (None) ne doit pas faire échouer le calcul du seuil.
    assert analyzer.market_caps["eth"] == 0
    assert analyzer.low_cap_threshold == 1000

    # Un second appel ne doit pas relancer les jobs.
    analyzer.service_bus.publish.reset_mock()
    analyzer._start_analysis_if_ready()
    analyzer.service_bus.publish.assert_not_called()


def test_start_analysis_stops_cleanly_without_data(analyzer):
    """Des données initiales vides doivent arrêter l'application proprement."""
    analyzer.coins = []
    analyzer.precision_data = {}

    analyzer._start_analysis_if_ready()

    assert analyzer._processing_completed.is_set()
    analyzer.service_bus.publish.assert_not_called()


def test_analyze_correlation_publishes_strong_correlation(analyzer):
    """Une corrélation au-dessus du seuil est publiée, une corrélation faible est ignorée."""
    index = pd.date_range("2025-01-01", periods=30, freq="D", tz="UTC")
    btc_rsi = pd.Series(range(30), index=index, dtype=float)
    analyzer.market_caps = {"eth": 500.0}
    analyzer.low_cap_threshold = 1000.0

    analyzer.analyze_correlation(("ethereum", "eth"), btc_rsi * 2 + 1, btc_rsi, "1d")
    analyzer.analyze_correlation(("flat", "flt"), pd.Series([1.0, -1.0] * 15, index=index), btc_rsi, "1d")

    (event,) = published_events(analyzer.service_bus, "CorrelationAnalyzed")
    assert event.result["coin_id"] == "ethereum"
    assert event.result["correlation"] == pytest.approx(1.0)
    assert event.result["low_cap_quartile"] is True


def test_final_results_published_once_all_jobs_completed(analysis_config):
    """FinalResultsReady n'est publié qu'après la fin de tous les jobs."""
    config = analysis_config.model_copy(update={"timeframes": ["1h", "1d"]})

    with patch("crypto_analyzer.ServiceBus"):
        analyzer = CryptoAnalyzer(config=config, session_guid="test-guid")

    analyzer._handle_analysis_job_completed(type("Evt", (), {"timeframe": "1h"})())
    assert not published_events(analyzer.service_bus, "FinalResultsReady")

    analyzer._handle_analysis_job_completed(type("Evt", (), {"timeframe": "1d"})())
    (event,) = published_events(analyzer.service_bus, "FinalResultsReady")
    assert event.timeframes == ["1h", "1d"]
