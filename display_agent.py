import queue
import threading
from typing import Optional

from events import FinalResultsReady
from logger import logger
from service_bus import ServiceBus


class DisplayAgent(threading.Thread):
    """Agent responsable de l'affichage final des résultats dans son propre thread."""

    def __init__(self, service_bus: Optional[ServiceBus] = None):
        super().__init__()
        self.service_bus = service_bus
        self.work_queue = queue.Queue()
        self._running = True

        if self.service_bus:
            self.service_bus.subscribe("FinalResultsReady", self._handle_final_results_ready)

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
        # Accès direct aux attributs de l'objet dataclass
        results = event.results
        weeks = event.weeks
        session_guid = event.session_guid
        db_manager = event.db_manager

        results = sorted(results, key=lambda x: (-abs(x['correlation']), x['market_cap']))
        logger.info(f"\nTokens à faible capitalisation avec forte corrélation RSI avec BTC ({weeks} semaines) :")
        for result in results:
            logger.info(
                f"Coin: {result['coin_id']}/{result['coin_symbol']}, Correlation RSI: {result['correlation']:.3f}, "
                f"Market Cap: ${result['market_cap']:,.2f}")

        logger.info("\nRésumé de l'historique des corrélations :")
        try:
            if db_manager and session_guid:
                correlations = db_manager.get_correlations(session_guid=session_guid)
                for row in correlations:
                    logger.info(
                        f"Run: {row[2]}, Coin: {row[0]}/{row[1]}, Correlation: {row[3]:.3f}, Market Cap: ${row[4]:,.2f}, "
                        f"Low Cap Quartile: {row[5]}")
        except Exception as e:
            logger.error(f"Erreur lors de l'affichage de l'historique des corrélations : {e}")
