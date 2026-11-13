import importlib.util
import sys
from unittest.mock import MagicMock

# noinspection PyPackageRequirements
import pytest

from configuration import AnalysisConfig

# `python_pubsub_client` importe ce module (proxys DevTools) dès son chargement. Les tests ne s'en
# servent pas : on le remplace par un mock s'il n'est pas installé, pour ne pas en dépendre.
DEVTOOLS_MODULE = "python_pubsub_devtools_consumers"

if importlib.util.find_spec(DEVTOOLS_MODULE) is None:
    sys.modules[DEVTOOLS_MODULE] = MagicMock()


@pytest.fixture
def mock_service_bus():
    """Fixture pour fournir un mock du ServiceBus."""
    return MagicMock()


@pytest.fixture
def analysis_config():
    """Fixture pour fournir une configuration d'analyse standard."""
    return AnalysisConfig(weeks=4, top_n_coins=10, rsi_period=14)


def published_events(service_bus, event_name: str) -> list:
    """Retourne les payloads publiés sur le bus mocké pour un nom d'événement donné."""
    return [call.args[1] for call in service_bus.publish.call_args_list if call.args[0] == event_name]
