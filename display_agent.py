from typing import Optional

# noinspection PyPackageRequirements
from pubsub import QueueWorkerThread, ServiceBus

from events import AnalysisConfigurationProvided, DisplayCompleted, FinalResultsReady
from logger import logger

# --- Définition des codes de couleur ANSI pour le terminal ---
RESET = "\033[0m"
# Textes sur fonds colorés
BLACK_ON_YELLOW = "\033[30;43m"
WHITE_ON_BLUE = "\033[97;44m"
BLACK_ON_GREEN = "\033[30;42m"
WHITE_ON_GREEN = "\033[97;42m"
WHITE_ON_RED = "\033[97;41m"
# Texte simple
BOLD_WHITE = "\033[1;97m"
CYAN = "\033[96m"


# --- Fin des codes de couleur ---


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
        logger.info(
            f"{BLACK_ON_YELLOW}DisplayAgent{RESET} a reçu la configuration pour la session {BOLD_WHITE}{self.session_guid}{RESET}."
        )

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
            f"\n{WHITE_ON_BLUE}Tokens à faible capitalisation avec forte corrélation RSI avec BTC ({event.weeks} semaines, timeframes: {timeframes_str}) :{RESET}"
        )

        if not results:
            logger.info(f"{WHITE_ON_RED}Aucun résultat à afficher.{RESET}")
            return

        for result in results:
            correlation_color = BLACK_ON_GREEN if result['correlation'] > 0 else WHITE_ON_RED
            logger.info(
                f"Coin: {BOLD_WHITE}{result['coin_id']}/{result['coin_symbol']}{RESET}, "
                f"Timeframe: {result.get('timeframe', 'N/A')}, "
                f"Corrélation RSI: {correlation_color}{result['correlation']:.3f}{RESET}, "
                f"Market Cap: {CYAN}${result['market_cap']:,.0f}{RESET}"
            )
