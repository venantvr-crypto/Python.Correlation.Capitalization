import collections
import queue
import threading
from typing import Callable, Any, Dict

from events import RunAnalysisRequested, FetchTopCoinsRequested, TopCoinsFetched, SingleCoinFetched, \
    CalculateMarketCapThresholdRequested, MarketCapThresholdCalculated, FetchHistoricalPricesRequested, \
    HistoricalPricesFetched, CalculateRSIRequested, RSICalculated, CorrelationAnalyzed, \
    CoinProcessingFailed, FinalResultsReady, DisplayCompleted
from logger import logger

EVENT_SCHEMAS = {
    "RunAnalysisRequested": RunAnalysisRequested,
    "FetchTopCoinsRequested": FetchTopCoinsRequested,
    "TopCoinsFetched": TopCoinsFetched,
    "SingleCoinFetched": SingleCoinFetched,
    "CalculateMarketCapThresholdRequested": CalculateMarketCapThresholdRequested,
    "MarketCapThresholdCalculated": MarketCapThresholdCalculated,
    "FetchHistoricalPricesRequested": FetchHistoricalPricesRequested,
    "HistoricalPricesFetched": HistoricalPricesFetched,
    "CalculateRSIRequested": CalculateRSIRequested,
    "RSICalculated": RSICalculated,
    "CorrelationAnalyzed": CorrelationAnalyzed,
    "CoinProcessingFailed": CoinProcessingFailed,
    "FinalResultsReady": FinalResultsReady,
    "DisplayCompleted": DisplayCompleted
}


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
            return

        try:
            event_class = EVENT_SCHEMAS.get(event_name)
            if event_class and isinstance(payload, Dict):
                validated_payload = event_class(**payload)
            else:
                validated_payload = payload
        except Exception as e:
            logger.error(f"Erreur de validation du payload pour l'événement '{event_name}': {e}")
            return

        for subscriber in subscribers:
            try:
                subscriber(validated_payload)
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
