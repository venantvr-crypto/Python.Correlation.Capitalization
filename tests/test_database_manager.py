import sqlite3
from unittest.mock import MagicMock

import pandas as pd

# noinspection PyPackageRequirements
import pytest

from agents.database_manager import DatabaseManager
from events import AnalysisConfigurationProvided, CorrelationAnalyzed, HistoricalPricesFetched, SingleCoinFetched
from serialization import frame_to_json


# On utilise la fixture partagée de conftest.py pour la configuration
@pytest.fixture
def db_manager(analysis_config, tmp_path):
    """
    Fixture pytest pour initialiser le DatabaseManager avec une BDD temporaire.
    Le `yield` gère le nettoyage (teardown) après le test.
    """
    # Initialisation du manager sur une BDD temporaire, supprimée par pytest
    manager = DatabaseManager(db_name=str(tmp_path / "test.db"), service_bus=MagicMock())
    manager.start()

    # On attend 1 seconde maximum. Si ça échoue, le test s'arrête avec une erreur claire.
    initialized_in_time = manager._initialized_event.wait(timeout=1)
    assert initialized_in_time, "Le DatabaseManager n'a pas pu s'initialiser dans le temps imparti."

    config_event = AnalysisConfigurationProvided(session_guid="test-guid", config=analysis_config)
    manager._handle_configuration_provided(config_event)

    yield manager  # Le test s'exécute ici

    # --- Nettoyage après le test ---
    manager.stop()


def fetch_all(db_manager, sql: str) -> list[tuple]:
    """Attend la fin des tâches en file, puis interroge directement la BDD."""
    assert db_manager.wait_for_queue_completion(timeout=5)

    with sqlite3.connect(db_manager.db_name) as conn:
        return conn.execute(sql).fetchall()


def test_tables_are_created(db_manager):
    """
    Vérifie que le DatabaseManager crée bien les tables nécessaires au démarrage.
    """
    # La fixture a déjà attendu que l'initialisation soit terminée,
    # on peut donc vérifier immédiatement.

    # Connexion directe à la BDD pour vérifier l'état
    conn = sqlite3.connect(db_manager.db_name)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    conn.close()

    expected_tables = {"tokens", "prices", "rsi", "correlations", "precision_data"}
    assert expected_tables.issubset(tables)


def test_handle_single_coin_fetched_adds_task(db_manager, mocker):
    """
    Vérifie que l'événement SingleCoinFetched ajoute bien une tâche à la file d'attente.
    """
    # On espionne la méthode add_task
    mocker.patch.object(db_manager, "add_task")

    coin_data = {"id": "bitcoin", "symbol": "btc"}
    event = SingleCoinFetched(coin=coin_data)

    # On appelle le handler
    db_manager._handle_single_coin_fetched(event)

    # On vérifie que la tâche d'enregistrement a bien été ajoutée
    db_manager.add_task.assert_called_once_with("_db_save_token", coin_data, "test-guid")


def test_token_is_persisted(db_manager):
    """Un token reçu est enregistré avec des valeurs converties aux bons types."""
    coin = {"id": "ethereum", "symbol": "eth", "name": "Ethereum", "current_price": "3000.5", "market_cap": 3.6e11, "roi": None}
    db_manager._handle_single_coin_fetched(SingleCoinFetched(coin=coin))

    rows = fetch_all(db_manager, "SELECT coin_id, coin_symbol, session_guid, symbol, current_price, market_cap, roi FROM tokens")
    assert rows == [("ethereum", "ETH", "test-guid", "eth", 3000.5, 360_000_000_000, None)]


def test_prices_are_persisted_once(db_manager):
    """Les prix sont enregistrés en masse, sans doublon si l'événement est rejoué."""
    index = pd.date_range("2025-01-01", periods=3, freq="D", tz="UTC", name="timestamp")
    prices_df = pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": [1.5, 2.5, 3.5], "volume": 100.0}, index=index)
    event = HistoricalPricesFetched(coin_id_symbol=("ethereum", "eth"), prices_df_json=frame_to_json(prices_df), timeframe="1d")

    db_manager._handle_historical_prices_fetched(event)
    db_manager._handle_historical_prices_fetched(event)

    rows = fetch_all(db_manager, "SELECT coin_id, timestamp, close, timeframe FROM prices ORDER BY timestamp")
    assert rows == [
        ("ethereum", "2025-01-01T00:00:00+00:00", 1.5, "1d"),
        ("ethereum", "2025-01-02T00:00:00+00:00", 2.5, "1d"),
        ("ethereum", "2025-01-03T00:00:00+00:00", 3.5, "1d"),
    ]


def test_correlation_is_persisted(db_manager):
    result = {"coin_id": "ethereum", "coin_symbol": "eth", "correlation": 0.9, "market_cap": 500.0, "low_cap_quartile": True, "timeframe": "1d"}
    db_manager._handle_correlation_analyzed(CorrelationAnalyzed(result=result, timeframe="1d"))

    rows = fetch_all(db_manager, "SELECT coin_id, session_guid, correlation, low_cap_quartile, timeframe FROM correlations")
    assert rows == [("ethereum", "test-guid", 0.9, 1, "1d")]
