from typing import Optional

# noinspection PyPackageRequirements
from pubsub import QueueWorkerThread, ServiceBus

from events import AnalysisConfigurationProvided, DisplayCompleted, FinalResultsReady
from logger import logger


class DisplayAgent(QueueWorkerThread):
    """Agent responsable de l'affichage final des résultats dans son propre thread."""

    def __init__(self, service_bus: Optional[ServiceBus] = None):
        super().__init__(service_bus=service_bus, name="DisplayAgent")
        self.session_guid: Optional[str] = None

    def setup_event_subscriptions(self) -> None:
        self.service_bus.subscribe("AnalysisConfigurationProvided", self._handle_configuration_provided)
        self.service_bus.subscribe("FinalResultsReady", self._handle_final_results_ready)

    def _handle_configuration_provided(self, event: AnalysisConfigurationProvided):
        self.session_guid = event.session_guid
        logger.info(f"DisplayAgent a reçu la configuration pour la session {self.session_guid}.")

    def _handle_final_results_ready(self, event: FinalResultsReady):
        self.add_task("_display_results_and_publish", event)

    def _display_results_and_publish(self, event: FinalResultsReady):
        """Méthode qui affiche les résultats et publie l'événement de fin."""
        self._display_results(event)
        self.service_bus.publish("DisplayCompleted", DisplayCompleted(), self.__class__.__name__)

    @staticmethod
    def _display_results(event: FinalResultsReady):
        results = sorted(event.results, key=lambda x: (-abs(x.get("correlation", 0)), x.get("market_cap", 0)))
        timeframes_str = ", ".join(event.timeframes)

        logger.info(
            f"Tokens à faible capitalisation avec forte corrélation RSI avec BTC ({event.weeks} semaines, timeframes: {timeframes_str}) :"
        )

        if not results:
            logger.info("Aucun résultat à afficher.")
            return

        for result in results:
            logger.info(
                f"Coin: {result['coin_id']}/{result['coin_symbol']}, "
                f"Correlation RSI: {result['correlation']:.3f}, "
                f"Market Cap: ${result['market_cap']:,.2f}"
            )
