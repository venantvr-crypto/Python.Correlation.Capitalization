import collections
import queue
import threading
from typing import Callable, Any, Dict

from logger import logger


class ServiceBus(threading.Thread):
    """
    Un bus de services centralisé et thread-safe qui traite les événements de manière séquentielle.
    """

    def __init__(self):
        super().__init__()
        self._subscribers: Dict[str, list[Callable]] = collections.defaultdict(list)
        self._event_queue = queue.Queue()
        self._running = True
        logger.info("Service bus créé.")

    def run(self):
        """Boucle principale du bus de services pour traiter les événements de manière séquentielle."""
        logger.info("Service bus démarré.")
        while self._running:
            try:
                event_name, payload = self._event_queue.get(timeout=1)
                self._dispatch_event(event_name, payload)
                self._event_queue.task_done()
            except queue.Empty:
                continue
        logger.info("Service bus arrêté.")

    def _dispatch_event(self, event_name: str, payload: Any):
        """Dispatch les événements aux souscripteurs."""
        subscribers = self._subscribers[event_name]
        if not subscribers:
            logger.warning(f"Aucun abonné pour l'événement '{event_name}'.")
        for subscriber in subscribers:
            try:
                # Le ServiceBus ne fait plus qu'appeler une méthode qui met la tâche dans une file d'attente.
                # Le travail est ensuite effectué par le thread de la classe abonnée.
                subscriber(payload)
            except Exception as e:
                logger.error(f"Erreur d'exécution de l'abonné pour '{event_name}': {e}")

    def subscribe(self, event_name: str, subscriber: Callable):
        """Abonne une fonction ou une méthode à un événement."""
        self._subscribers[event_name].append(subscriber)
        logger.debug(f"Abonné '{subscriber.__name__}' à l'événement '{event_name}'.")

    def publish(self, event_name: str, payload: Any):
        """Publie un événement dans la file d'attente du bus."""
        self._event_queue.put((event_name, payload))

    def stop(self):
        """Arrête le thread du bus de services."""
        self._running = False
        self.join()
