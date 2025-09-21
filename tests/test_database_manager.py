import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database_manager import DatabaseManager
from events import AnalysisConfigurationProvided


class TestDatabaseManager(unittest.TestCase):

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()

        self.mock_service_bus = Mock()
        self.db_manager = DatabaseManager(db_name=self.db_path, service_bus=self.mock_service_bus)
        self.db_manager.start()

        config_event = AnalysisConfigurationProvided(session_guid="test-guid", config=Mock())
        self.db_manager._handle_configuration_provided(config_event)

    def tearDown(self):
        if self.db_manager.is_alive():
            self.db_manager.stop()
        try:
            os.unlink(self.db_path)
        except Exception:  # Correction de l'exception trop large
            pass

    def test_tables_creation(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()
        self.assertIn("tokens", tables)
        self.assertIn("prices", tables)
