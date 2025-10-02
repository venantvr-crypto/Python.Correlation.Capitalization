from unittest.mock import ANY

import pandas as pd
# noinspection PyPackageRequirements
import pytest

from agents.rsi_calculator import RSICalculator
from events import AnalysisConfigurationProvided, RSICalculated, CalculateRSIRequested


# On utilise la fixture partagée de conftest.py
@pytest.fixture
def rsi_calculator(mock_service_bus, analysis_config):
    """Fixture pour initialiser le RSICalculator et sa configuration."""
    calculator = RSICalculator(service_bus=mock_service_bus)

    # Simuler la réception de la configuration
    config_event = AnalysisConfigurationProvided(session_guid="test-guid", config=analysis_config)
    # noinspection PyProtectedMember
    calculator._handle_configuration_provided(config_event)

    return calculator


def test_handle_calculate_rsi_requested_adds_task(rsi_calculator, mocker):
    """Teste que l'événement de requête pour le calcul RSI ajoute la bonne tâche."""
    mocker.patch.object(rsi_calculator, 'add_task')

    event = CalculateRSIRequested(
        coin_id_symbol=("bitcoin", "btc"),
        prices_series_json='{"name":"close","index":[1,2],"data":[10,20]}',
        timeframe="1d"
    )

    rsi_calculator._handle_calculate_rsi_requested(event)

    # noinspection PyUnresolvedReferences
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
    # noinspection PyUnresolvedReferences
    rsi_calculator.service_bus.publish.assert_called_once()

    # Récupérer les arguments de l'appel à publish
    # noinspection PyUnresolvedReferences
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
