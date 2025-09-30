from typing import Optional

# noinspection PyPackageRequirements
from pubsub import QueueWorkerThread, ServiceBus

from events import AnalysisConfigurationProvided, DisplayCompleted, FinalResultsReady
from logger import logger

# --- Definition of ANSI color codes for the terminal ---
RESET = "\033[0m"
# Text on colored backgrounds
BLACK_ON_YELLOW = "\033[30;43m"
WHITE_ON_BLUE = "\033[97;44m"
BLACK_ON_GREEN = "\033[30;42m"
WHITE_ON_GREEN = "\033[97;42m"
WHITE_ON_RED = "\033[97;41m"
# Simple text
BOLD_WHITE = "\033[1;97m"
CYAN = "\033[96m"


# --- End of color codes ---


class DisplayAgent(QueueWorkerThread):
    """Agent responsible for displaying final results in its own thread."""

    def __init__(self, service_bus: Optional[ServiceBus] = None):
        super().__init__(service_bus=service_bus, name="DisplayAgent")
        self.session_guid: Optional[str] = None

    def setup_event_subscriptions(self) -> None:
        self.service_bus.subscribe("AnalysisConfigurationProvided", self._handle_configuration_provided)
        self.service_bus.subscribe("FinalResultsReady", self._handle_final_results_ready)

    def _handle_configuration_provided(self, event: AnalysisConfigurationProvided):
        try:
            self.session_guid = event.session_guid
            logger.info(
                f"{BLACK_ON_YELLOW}DisplayAgent{RESET} received configuration for session {BOLD_WHITE}{self.session_guid}{RESET}."
            )
        except Exception as e:
            error_msg = f"Error handling configuration provided: {e}"
            logger.critical(error_msg, exc_info=True)
            self.log_message(error_msg)

    def _handle_final_results_ready(self, event: FinalResultsReady):
        try:
            self.add_task("_display_results_and_publish", event)
        except Exception as e:
            error_msg = f"Error handling final results ready: {e}"
            logger.critical(error_msg, exc_info=True)
            self.log_message(error_msg)

    def _display_results_and_publish(self, event: FinalResultsReady):
        """Method that displays results and publishes the completion event."""
        self._display_results(event)
        self.service_bus.publish("DisplayCompleted", DisplayCompleted(), self.__class__.__name__)

    @staticmethod
    def _display_results(event: FinalResultsReady):
        results = sorted(event.results, key=lambda x: (-abs(x.get("correlation", 0)), x.get("market_cap", 0)))
        timeframes_str = ", ".join(event.timeframes)

        logger.info(
            f"\n{WHITE_ON_BLUE}Low capitalization tokens with strong RSI correlation with BTC ({event.weeks} weeks, timeframes: {timeframes_str}):{RESET}"
        )

        if not results:
            logger.info(f"{WHITE_ON_RED}No results to display.{RESET}")
            return

        for result in results:
            correlation_color = BLACK_ON_GREEN if result['correlation'] > 0 else WHITE_ON_RED
            logger.info(
                f"Coin: {BOLD_WHITE}{result['coin_id']}/{result['coin_symbol']}{RESET}, "
                f"Timeframe: {result.get('timeframe', 'N/A')}, "
                f"RSI Correlation: {correlation_color}{result['correlation']:.3f}{RESET}, "
                f"Market Cap: {CYAN}${result['market_cap']:,.0f}{RESET}"
            )
