from unittest.mock import ANY

import pandas as pd

# noinspection PyPackageRequirements
import pytest

from agents.rsi_calculator import RSICalculator, compute_rsi
from events import AnalysisConfigurationProvided, CalculateRSIRequested, RSICalculated
from serialization import series_from_json
from tests.conftest import published_events


# On utilise la fixture partagée de conftest.py
@pytest.fixture
def rsi_calculator(mock_service_bus, analysis_config):
    """Fixture pour initialiser le RSICalculator et sa configuration."""
    calculator = RSICalculator(service_bus=mock_service_bus)

    # Simuler la réception de la configuration
    config_event = AnalysisConfigurationProvided(session_guid="test-guid", config=analysis_config)
    calculator._handle_configuration_provided(config_event)

    return calculator


def test_handle_calculate_rsi_requested_adds_task(rsi_calculator, mocker):
    """Teste que l'événement de requête pour le calcul RSI ajoute la bonne tâche."""
    mocker.patch.object(rsi_calculator, "add_task")

    event = CalculateRSIRequested(
        coin_id_symbol=("bitcoin", "btc"),
        prices_series_json='{"name":"close","index":[1,2],"data":[10,20]}',
        timeframe="1d"
    )

    rsi_calculator._handle_calculate_rsi_requested(event)

    rsi_calculator.add_task.assert_called_once_with("_calculate_rsi_task", ("bitcoin", "btc"), ANY, "1d")


def test_calculate_rsi_task_publishes_event(rsi_calculator):
    """
    Teste que la tâche de calcul RSI publie bien un événement RSICalculated
    avec une chaîne JSON valide.
    """
    # Une série de prix linéaire ne produit que des NaN pour le RSI.
    # Il faut des hausses et des baisses pour tester le calcul.
    prices_data = [
        110, 112, 115, 114, 113, 111, 109, 110, 111, 113, 116, 118, 120,
        121, 120, 118, 117, 119, 122, 123, 125, 124, 122, 120, 119, 120
    ]
    prices = pd.Series(prices_data, dtype=float)

    # Exécuter la tâche
    rsi_calculator._calculate_rsi_task(("bitcoin", "btc"), prices, "1h")

    # Vérifier que service_bus.publish a été appelé une fois
    rsi_calculator.service_bus.publish.assert_called_once()

    # Récupérer les arguments de l'appel à publish
    call_args = rsi_calculator.service_bus.publish.call_args.args

    event_name = call_args[0]
    payload = call_args[1]

    # Vérifier le nom de l'événement et le type du payload
    assert event_name == "RSICalculated"
    assert isinstance(payload, RSICalculated)

    # Vérifier le contenu du payload en utilisant la notation objet
    assert payload.coin_id_symbol == ("bitcoin", "btc")
    assert payload.timeframe == "1h"
    # Vérifier que le résultat est bien une chaîne de caractères (JSON) et non None
    assert isinstance(payload.rsi_series_json, str)
    assert payload.rsi_series_json != "null"

    # 26 prix et une période de 14 donnent 13 valeurs de RSI, toutes entre 0 et 100.
    rsi_series = series_from_json(payload.rsi_series_json)
    assert len(rsi_series) == 13
    assert rsi_series.between(0, 100).all()


def test_compute_rsi_known_values():
    """Vérifie le RSI sur des cas dont le résultat est connu."""
    rising = pd.Series(range(1, 21), dtype=float)
    assert (compute_rsi(rising, 14) == 100).all()

    falling = pd.Series(range(20, 0, -1), dtype=float)
    assert (compute_rsi(falling, 14) == 0).all()

    # Gains et pertes identiques : RSI de 50.
    alternating = pd.Series([10.0, 11.0] * 10)
    assert compute_rsi(alternating, 2).iloc[-1] == pytest.approx(50.0)


def test_compute_rsi_requires_enough_data():
    assert compute_rsi(pd.Series([1.0, 2.0, None, 3.0]), 14).empty
    assert len(compute_rsi(pd.Series(range(15), dtype=float), 14)) == 2


@pytest.mark.parametrize("prices", [None, pd.Series([1.0, 2.0, 3.0])])
def test_calculate_rsi_task_publishes_failure_without_enough_data(rsi_calculator, prices):
    """Sans RSI calculable, un échec est publié pour que le compteur du job soit décrémenté."""
    rsi_calculator._calculate_rsi_task(("ethereum", "eth"), prices, "1d")

    assert not published_events(rsi_calculator.service_bus, "RSICalculated")
    (event,) = published_events(rsi_calculator.service_bus, "CoinProcessingFailed")
    assert event.coin_id_symbol == ("ethereum", "eth")
