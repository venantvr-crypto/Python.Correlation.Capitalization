import queue
import threading
import time
from abc import ABC, abstractmethod
from typing import Optional, Any, List

from logger import logger
from service_bus import ServiceBus


class QueueWorkerThread(threading.Thread, ABC):
    """
    Classe de base abstraite pour un thread de travail qui consomme des tâches
    depuis une file d'attente (pattern Producer-Consumer).
    """

    def __init__(self, service_bus: Optional[ServiceBus] = None, name: Optional[str] = None):
        """
        Initialise le thread de travail.
        :param service_bus: Le bus de services pour la communication par événements.
        :param name: Le nom du thread, utile pour les logs.
        """
        super().__init__(name=name or self.__class__.__name__)
        self.service_bus = service_bus
        self.work_queue = queue.Queue()
        self._running = True

        if self.service_bus:
            self.setup_event_subscriptions()

    @abstractmethod
    def setup_event_subscriptions(self) -> None:
        """
        Méthode abstraite. Les classes enfants DOIVENT l'implémenter
        pour s'abonner aux événements du ServiceBus.
        """
        pass

    def add_task(self, method_name: str, *args: Any, **kwargs: Any) -> None:
        """Méthode utilitaire pour ajouter une tâche à la file d'attente."""
        self.work_queue.put((method_name, args, kwargs))

    def run(self) -> None:
        """
        La boucle de travail principale. Gérée par la classe mère.
        Elle récupère les tâches et appelle la méthode correspondante.
        """
        logger.info(f"Thread '{self.name}' démarré.")
        while self._running:
            try:
                task = self.work_queue.get(timeout=1)
                if task is None:  # Le signal d'arrêt
                    break

                method_name, args, kwargs = task
                try:
                    method = getattr(self, method_name)
                    method(*args, **kwargs)
                except Exception as e:
                    logger.error(
                        f"Erreur d'exécution de la tâche '{method_name}' dans '{self.name}': {e}", exc_info=True
                    )
                finally:
                    # Crucial pour que queue.join() fonctionne !
                    self.work_queue.task_done()
            except queue.Empty:
                continue
        logger.info(f"La boucle de travail de '{self.name}' est terminée.")

    def stop(self) -> None:
        """
        Arrête le thread PROPREMENT :
        1. Attend que la file de travail soit vide.
        2. Envoie le signal d'arrêt à la boucle 'run'.
        3. Attend la terminaison effective du thread.
        """
        logger.info(f"Demande d'arrêt pour '{self.name}'. Attente de la fin des tâches en file...")

        # ÉTAPE 1 : Attend que la file soit complètement vide.
        self.work_queue.join()

        # ÉTAPE 2 : Arrête la boucle 'while' du thread.
        self._running = False
        self.work_queue.put(None)  # Débloque le .get() qui pourrait être en attente.

        # ÉTAPE 3 : Attend la terminaison du thread lui-même.
        super().join()
        logger.info(f"Thread '{self.name}' a terminé toutes ses tâches et est arrêté.")


class OrchestratorBase(ABC):
    """
    Classe de base pour un orchestrateur qui gère le cycle de vie
    d'une application basée sur des services (threads).
    """

    def __init__(self, service_bus: ServiceBus):
        self.service_bus = service_bus
        self.services: List[QueueWorkerThread] = []
        self._processing_completed = threading.Event()

        # Le contrat : l'enfant doit fournir les services et les abonnements
        self.register_services()
        self.setup_event_subscriptions()

        # La classe mère s'abonne à l'événement de fin universel
        self.service_bus.subscribe("AllProcessingCompleted", self._on_all_processing_completed)

    @abstractmethod
    def register_services(self) -> None:
        """Les classes enfants doivent implémenter cette méthode pour créer et
        ajouter leurs services à la liste self.services."""
        pass

    @abstractmethod
    def setup_event_subscriptions(self) -> None:
        """Les classes enfants doivent s'abonner ici à leurs événements spécifiques."""
        pass

    @abstractmethod
    def start_workflow(self) -> None:
        """Le point d'entrée pour lancer la logique métier de l'orchestrateur."""
        pass

    def _start_services(self) -> None:
        """Démarre tous les services enregistrés, y compris le ServiceBus."""
        logger.info(f"Démarrage de {len(self.services)} services et du ServiceBus...")
        all_services_to_manage = self.services + [self.service_bus]
        for service in all_services_to_manage:
            if not service.is_alive():
                service.start()
        time.sleep(1)  # Laisse le temps aux threads de démarrer

    def _stop_services(self) -> None:
        """Arrête tous les services enregistrés, y compris le ServiceBus."""
        logger.info("Arrêt de tous les services...")
        all_services_to_manage = self.services + [self.service_bus]
        for service in reversed(all_services_to_manage):
            if service.is_alive():
                service.stop()
        logger.info("Tous les services ont été arrêtés proprement.")

    def _on_all_processing_completed(self, _event: Any) -> None:
        """Handler pour le signal de fin, qui débloque la méthode 'run'."""
        logger.info("Signal de fin d'analyse reçu. Déclenchement de l'arrêt.")
        self._processing_completed.set()

    def run(self) -> None:
        """Point d'entrée principal qui exécute le cycle de vie complet."""
        self._start_services()
        self.start_workflow()
        self._processing_completed.wait()  # Bloque jusqu'à la réception du signal de fin
        self._stop_services()
        logger.info("Exécution de l'orchestrateur terminée.")
