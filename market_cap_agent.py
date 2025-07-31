import queue
import threading
from typing import Dict, Optional

import numpy as np

from logger import logger
from service_bus import ServiceBus


class MarketCapAgent(threading.Thread):
    """
    Agent responsable du calcul du seuil de capitalisation boursière dans son propre thread.
    """

    def __init__(self, service_bus: Optional[ServiceBus] = None):
        super().__init__()
        self.service_bus = service_bus
        self.work_queue = queue.Queue()
        self._running = True

        if self.service_bus:
            # Le MarketCapAgent ne s'abonne plus à TopCoinsFetched, il s'abonne à la requête de calcul
            self.service_bus.subscribe("CalculateMarketCapThresholdRequested",
                                       self._handle_calculate_market_cap_threshold_requested)

    def _handle_calculate_market_cap_threshold_requested(self, payload: Dict):
        """Reçoit l'événement et ajoute la tâche à la file d'attente."""
        self.work_queue.put(payload)

    def run(self):
        """Boucle d'exécution du thread."""
        logger.info("Thread MarketCapAgent démarré.")
        while self._running:
            try:
                payload = self.work_queue.get(timeout=1)
                if payload is None:
                    continue

                self._calculate_market_cap_task(payload)
                self.work_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Erreur d'exécution de la tâche dans MarketCapAgent: {e}")
        logger.info("Thread MarketCapAgent arrêté.")

    def stop(self):
        """Arrête le thread en toute sécurité."""
        self._running = False
        self.work_queue.put(None)
        self.join()

    def _calculate_market_cap_task(self, payload: Dict):
        """Calcule le seuil de capitalisation boursière et publie un événement."""
        coins = payload.get('coins', [])
        session_guid = payload.get('session_guid')

        market_caps: Dict[str, float] = {coin['symbol']: coin['market_cap'] for coin in coins if 'market_cap' in coin}
        market_caps_values = list(market_caps.values())

        low_cap_threshold = np.percentile(market_caps_values, 25) if market_caps_values else float('inf')
        logger.info(f"Seuil de faible capitalisation (25e percentile): ${low_cap_threshold:,.2f}")

        # Le MarketCapAgent publie le seuil calculé, et les market_caps pour que l'orchestrateur les stocke.
        self.service_bus.publish("MarketCapThresholdCalculated", {
            'market_caps': market_caps,
            'low_cap_threshold': low_cap_threshold,
            'session_guid': session_guid
        })
