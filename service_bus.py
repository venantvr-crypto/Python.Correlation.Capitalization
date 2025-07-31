# file: service_bus.py
import queue
import threading
from typing import Callable, Any, Dict, List

from logger import logger


class ServiceBus:
    """Implémente un bus de services pour la communication inter-threads."""

    def __init__(self):
        self._subscribers: Dict[str, List[Callable[[Any], None]]] = {}
        self._event_queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run)
        self._running = False
        self._lock = threading.Lock()

    def start(self):
        """Démarre le thread du bus de services."""
        if not self._running:
            self._running = True
            self._thread.start()
            logger.info("Service bus démarré.")

    def stop(self):
        """Arrête le thread du bus de services."""
        if self._running:
            self._running = False
            self._event_queue.put(None)  # Sentinel value to stop the thread
            self._thread.join()
            logger.info("Service bus arrêté.")

    def publish(self, event_name: str, payload: Any):
        """Publie un événement avec une charge utile."""
        logger.debug(f"Publication de l'événement: {event_name}")
        self._event_queue.put({'event_name': event_name, 'payload': payload})

    def subscribe(self, event_name: str, callback: Callable[[Any], None]):
        """Abonne une fonction à un événement."""
        with self._lock:
            if event_name not in self._subscribers:
                self._subscribers[event_name] = []
            self._subscribers[event_name].append(callback)
            logger.info(f"Fonction abonnée à l'événement: {event_name}")

    def _run(self):
        """Boucle principale pour le traitement des événements."""
        while self._running:
            event = self._event_queue.get()
            if event is None:
                break
            event_name = event['event_name']
            payload = event['payload']
            self._process_event(event_name, payload)
            self._event_queue.task_done()
        logger.info("Boucle du service bus terminée.")

    def _process_event(self, event_name: str, payload: Any):
        """Traite un événement en appelant les abonnés."""
        with self._lock:
            callbacks = self._subscribers.get(event_name, [])
        if not callbacks:
            logger.warning(f"Aucun abonné pour l'événement '{event_name}'.")
        for callback in callbacks:
            try:
                callback(payload)
            except Exception as e:
                logger.error(f"Erreur lors de l'exécution du callback pour '{event_name}': {e}")
