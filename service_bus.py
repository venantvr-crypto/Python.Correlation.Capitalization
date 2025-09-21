import collections
import threading
import uuid
from dataclasses import asdict, is_dataclass
from typing import Any, Callable, Dict, Optional

# noinspection PyPackageRequirements
from pubsub.pubsub_client import PubSubClient

from events import (
    AnalysisConfigurationProvided,
    AnalysisJobCompleted,
    CalculateRSIRequested,
    CoinProcessingFailed,
    CorrelationAnalyzed,
    DisplayCompleted,
    FetchHistoricalPricesRequested,
    FetchPrecisionDataRequested,
    FetchTopCoinsRequested,
    FinalResultsReady,
    HistoricalPricesFetched,
    PrecisionDataFetched,
    RSICalculated,
    RunAnalysisRequested,
    SingleCoinFetched,
    TopCoinsFetched,
)
from logger import logger

EVENT_SCHEMAS = {
    "AnalysisConfigurationProvided": AnalysisConfigurationProvided,
    "RunAnalysisRequested": RunAnalysisRequested,
    "FetchTopCoinsRequested": FetchTopCoinsRequested,
    "TopCoinsFetched": TopCoinsFetched,
    "SingleCoinFetched": SingleCoinFetched,
    "FetchHistoricalPricesRequested": FetchHistoricalPricesRequested,
    "HistoricalPricesFetched": HistoricalPricesFetched,
    "CalculateRSIRequested": CalculateRSIRequested,
    "RSICalculated": RSICalculated,
    "CorrelationAnalyzed": CorrelationAnalyzed,
    "CoinProcessingFailed": CoinProcessingFailed,
    "FinalResultsReady": FinalResultsReady,
    "DisplayCompleted": DisplayCompleted,
    "FetchPrecisionDataRequested": FetchPrecisionDataRequested,
    "PrecisionDataFetched": PrecisionDataFetched,
    "AnalysisJobCompleted": AnalysisJobCompleted,
}


class ServiceBus(threading.Thread):
    def __init__(self, url: str, consumer_name: str):
        super().__init__()
        self.daemon = True
        self.url = url
        self.consumer_name = consumer_name
        self.client: Optional[PubSubClient] = None
        self._topics = set()
        self._handlers = collections.defaultdict(list)

    def subscribe(self, event_name: str, subscriber: Callable):
        self._topics.add(event_name)
        self._handlers[event_name].append(subscriber)
        logger.debug(f"'{subscriber.__name__}' mis en attente pour l'abonnement à '{event_name}'.")

    def run(self):
        logger.info(f"Le thread ServiceBus démarre. Connexion et abonnement aux topics: {list(self._topics)}")

        self.client = PubSubClient(url=self.url, consumer=self.consumer_name, topics=list(self._topics))

        for event_name, subscribers in self._handlers.items():
            for subscriber in subscribers:
                # MODIFICATION : Correction du bug de "late binding".
                # On passe event_name et subscriber comme arguments à la fonction qui crée le handler.
                # Cela garantit que chaque handler est lié au bon événement.
                def create_handler(e_name, sub):
                    def _internal_handler(message: Dict[str, Any]):
                        # Le serveur envoie des messages de confirmation que nous devons ignorer
                        if isinstance(message, str) and message.startswith("Subscribed to"):
                            return

                        event_class = EVENT_SCHEMAS.get(e_name)
                        validated_payload = message
                        if event_class:
                            try:
                                # Si le message est le payload de la dataclass
                                if not isinstance(message, dict):
                                    logger.warning(
                                        f"Message inattendu pour {e_name}, attendu dict, reçu {type(message)}"
                                    )
                                    return
                                validated_payload = event_class(**message)
                            except TypeError as e:
                                logger.error(f"Erreur validation pour '{e_name}': {e}. Message: {message}")
                                return
                        try:
                            sub(validated_payload)
                        except Exception as e:
                            logger.error(f"Erreur dans l'abonné '{sub.__name__}' pour '{e_name}': {e}", exc_info=True)

                    return _internal_handler

                self.client.register_handler(event_name, create_handler(event_name, subscriber))

        logger.info("Tous les handlers sont enregistrés. Démarrage de l'écoute...")
        try:
            self.client.start()
        except Exception as e:
            logger.error(f"Le client Pub/Sub s'est arrêté avec une erreur : {e}")
        logger.info("ServiceBus arrêté.")

    def publish(self, event_name: str, payload: Any):
        if self.client is None:
            logger.error(f"Impossible de publier '{event_name}': le ServiceBus n'a pas encore démarré.")
            return

        if is_dataclass(payload):
            message = asdict(payload)
        elif isinstance(payload, dict):
            message = payload
        else:
            logger.error(f"Type de payload non supporté : {type(payload)}")
            return

        self.client.publish(
            topic=event_name, message=message, producer=self.client.consumer, message_id=str(uuid.uuid4())
        )

    def stop(self):
        logger.info("Demande d'arrêt pour ServiceBus.")
        pass
