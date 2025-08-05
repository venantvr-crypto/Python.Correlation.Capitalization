import queue
import threading
from typing import Optional

from events import FinalResultsReady, AnalysisConfigurationProvided
from logger import logger
from service_bus import ServiceBus


class DisplayAgent(threading.Thread):
    """Agent responsable de l'affichage final des résultats dans son propre thread."""

    def __init__(self, service_bus: Optional[ServiceBus] = None):
        super().__init__()
        self.service_bus = service_bus
        self.session_guid: Optional[str] = None
        self.work_queue = queue.Queue()
        self._running = True

        if self.service_bus:
            self.service_bus.subscribe("AnalysisConfigurationProvided", self._handle_configuration_provided)
            self.service_bus.subscribe("FinalResultsReady", self._handle_final_results_ready)

    def _handle_configuration_provided(self, event: AnalysisConfigurationProvided):
        """Stocke la configuration de la session."""
        self.session_guid = event.session_guid
        logger.info(f"DisplayAgent a reçu la configuration pour la session {self.session_guid}.")

    def _handle_final_results_ready(self, event: FinalResultsReady):
        """Reçoit l'événement et ajoute la tâche à la file d'attente."""
        self.work_queue.put(event)

    def run(self):
        """Boucle d'exécution du thread."""
        logger.info("Thread DisplayAgent démarré.")
        while self._running:
            try:
                event = self.work_queue.get(timeout=1)
                if event is None:
                    continue
                self._display_results(event)
                self.work_queue.task_done()
                self.service_bus.publish("DisplayCompleted", {})
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Erreur d'exécution de la tâche dans DisplayAgent: {e}")
        logger.info("Thread DisplayAgent arrêté.")

    def stop(self):
        """Arrête le thread en toute sécurité."""
        self._running = False
        self.work_queue.put(None)
        self.join()

    # noinspection PyMethodMayBeStatic
    def _display_results(self, event: FinalResultsReady):
        """Affiche les résultats finaux."""
        # CORRECTION : Utiliser les résultats et timeframes directement depuis l'événement
        results = event.results
        weeks = event.weeks
        timeframes_str = ", ".join(event.timeframes)

        sorted_results = sorted(results, key=lambda x: (-abs(x.get('correlation', 0)), x.get('market_cap', 0)))

        logger.info(f"Tokens à faible capitalisation avec forte corrélation RSI avec BTC ({weeks} semaines, timeframes: {timeframes_str}) :")

        if not sorted_results:
            logger.info("Aucun résultat à afficher.")
            return

        for result in sorted_results:
            logger.info(
                f"Coin: {result['coin_id']}/{result['coin_symbol']}, "
                f"Correlation RSI: {result['correlation']:.3f}, "
                f"Market Cap: ${result['market_cap']:,.2f}"
            )
