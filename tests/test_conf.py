from unittest.mock import MagicMock

# noinspection PyPackageRequirements
import pytest

from configuration import AnalysisConfig


@pytest.fixture
def mock_service_bus():
    """Fixture pour fournir un mock du ServiceBus."""
    return MagicMock()


@pytest.fixture
def analysis_config():
    """Fixture pour fournir une configuration d'analyse standard."""
    # noinspection PyArgumentList
    return AnalysisConfig(weeks=4, top_n_coins=10, rsi_period=14)
