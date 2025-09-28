import os
import sqlite3
import tempfile
from unittest.mock import MagicMock

# noinspection PyPackageRequirements
import pytest

from database_manager import DatabaseManager
from events import AnalysisConfigurationProvided, SingleCoinFetched


# On utilise la fixture partagée de conftest.py pour la configuration
@pytest.fixture
def db_manager(analysis_config):
    """
    Fixture pytest pour initialiser le DatabaseManager avec une BDD temporaire.
    Le `yield` gère le nettoyage (teardown) après le test.
    """
    # Création d'un fichier de BDD temporaire
    temp_db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = temp_db_file.name
    temp_db_file.close()

    # Initialisation du manager
    mock_bus = MagicMock()
    manager = DatabaseManager(db_name=db_path, service_bus=mock_bus)
    manager.start()

    # On attend 1 seconde maximum. Si ça échoue, le test s'arrête avec une erreur claire.
    # noinspection PyProtectedMember
    initialized_in_time = manager._initialized_event.wait(timeout=1)
    assert initialized_in_time, "Le DatabaseManager n'a pas pu s'initialiser dans le temps imparti."

    config_event = AnalysisConfigurationProvided(session_guid="test-guid", config=analysis_config)
    # noinspection PyProtectedMember
    manager._handle_configuration_provided(config_event)

    yield manager  # Le test s'exécute ici

    # --- Nettoyage après le test ---
    manager.stop()
    os.unlink(db_path)


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
    mocker.patch.object(db_manager, 'add_task')

    coin_data = {"id": "bitcoin", "symbol": "btc"}
    event = SingleCoinFetched(coin=coin_data)

    # On appelle le handler
    db_manager._handle_single_coin_fetched(event)

    # On vérifie que la tâche d'enregistrement a bien été ajoutée
    # noinspection PyUnresolvedReferences
    db_manager.add_task.assert_called_once_with("_db_save_token", coin_data, "test-guid")
