from unittest.mock import ANY

# noinspection PyPackageRequirements
import pytest

from agents.data_fetcher import DataFetcher
# On importe tous les événements nécessaires pour le test
from events import FetchTopCoinsRequested, AnalysisConfigurationProvided, TopCoinsFetched


# Une fixture pour créer une instance de DataFetcher pour chaque test
@pytest.fixture
def data_fetcher(mock_service_bus):
    fetcher = DataFetcher(service_bus=mock_service_bus)
    # On mock les clients externes pour ne pas faire de vrais appels réseau
    fetcher.cg = ANY
    fetcher.binance = ANY
    return fetcher


def test_handle_configuration_provided(data_fetcher, analysis_config):  # On injecte la fixture 'analysis_config'
    """Teste que la configuration est bien prise en compte."""
    # On utilise l'objet config valide au lieu de ANY
    event = AnalysisConfigurationProvided(session_guid="test-guid-123", config=analysis_config)

    data_fetcher._handle_configuration_provided(event)
    assert data_fetcher.session_guid == "test-guid-123"


def test_handle_fetch_top_coins_requested_adds_task(data_fetcher, mocker):
    """Teste que la réception de l'événement de requête ajoute la bonne tâche."""
    # On espionne la méthode add_task pour voir si elle est appelée correctement
    mocker.patch.object(data_fetcher, 'add_task')

    event = FetchTopCoinsRequested(n=50)
    data_fetcher._handle_fetch_top_coins_requested(event)

    # On vérifie que la bonne tâche a été mise en file d'attente avec le bon argument
    # noinspection PyUnresolvedReferences
    data_fetcher.add_task.assert_called_once_with("_fetch_top_coins_task", 50)


def test_fetch_top_coins_task_publishes_result(data_fetcher, mocker):
    """
    Teste que la tâche de fetch publie bien un événement TopCoinsFetched en cas de succès.
    """
    mock_coins = [{"id": "bitcoin"}]
    # On configure le mock du client CoinGecko pour qu'il retourne nos fausses données
    data_fetcher.cg = mocker.MagicMock()
    data_fetcher.cg.get_coins_markets.return_value = mock_coins

    # On exécute la tâche
    data_fetcher._fetch_top_coins_task(n=1)

    # On vérifie que le bon événement Pydantic a été publié
    expected_event = TopCoinsFetched(coins=mock_coins)
    # noinspection PyUnresolvedReferences
    data_fetcher.service_bus.publish.assert_called_once_with("TopCoinsFetched", expected_event, "DataFetcher")
