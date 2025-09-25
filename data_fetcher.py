from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import ccxt
import pandas as pd
import requests
# noinspection PyPackageRequirements
from pubsub import QueueWorkerThread, ServiceBus
from pycoingecko import CoinGeckoAPI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from events import (
    AnalysisConfigurationProvided,
    FetchHistoricalPricesRequested,
    FetchPrecisionDataRequested,
    FetchTopCoinsRequested,
    HistoricalPricesFetched,
    PrecisionDataFetched,
    TopCoinsFetched,
)
from logger import logger


class DataFetcher(QueueWorkerThread):
    """Récupère les données de marché avec une gestion robuste des erreurs et des limites d'API."""

    def __init__(self, service_bus: Optional[ServiceBus] = None):
        super().__init__(service_bus=service_bus, name="DataFetcher")
        self.cg = CoinGeckoAPI()

        # Active le rate limiter de CCXT pour respecter les limites de l'API Binance
        self.binance = ccxt.binance(
            {
                "enableRateLimit": True,
                "timeout": 30000,  # 30 secondes en millisecondes
            }
        )

        self.session_guid: Optional[str] = None

    def setup_event_subscriptions(self) -> None:
        self.service_bus.subscribe("AnalysisConfigurationProvided", self._handle_configuration_provided)
        self.service_bus.subscribe("FetchTopCoinsRequested", self._handle_fetch_top_coins_requested)
        self.service_bus.subscribe("FetchHistoricalPricesRequested", self._handle_fetch_historical_prices_requested)
        self.service_bus.subscribe("FetchPrecisionDataRequested", self._handle_fetch_precision_data_requested)

    def _fetch_top_coins_task(self, n: int) -> None:
        """Récupère les N top coins en réessayant chaque page individuellement en cas d'erreur réseau."""
        coins = []
        pages = (n + 99) // 100
        logger.info(f"Début de la récupération de {n} coins sur {pages} pages depuis CoinGecko...")

        @retry(
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=2, min=5, max=30),
            retry=retry_if_exception_type(requests.exceptions.RequestException),
            before_sleep=lambda retry_state: logger.warning(
                f"Erreur réseau avec CoinGecko. Réessai de la page dans {int(retry_state.next_action.sleep)}s... (Tentative {retry_state.attempt_number})"
            ),
        )
        def fetch_one_page(page_num: int) -> List[dict]:
            logger.info(f"Récupération de la page {page_num}/{pages} de CoinGecko...")
            return self.cg.get_coins_markets(vs_currency="usd", per_page=100, page=page_num, timeout=30)

        for page in range(1, pages + 1):
            try:
                new_coins = fetch_one_page(page)
                if not new_coins:
                    logger.warning(f"La page {page} de CoinGecko n'a retourné aucune donnée, arrêt de la collecte.")
                    break
                coins.extend(new_coins)
            except requests.exceptions.RequestException as e:
                logger.error(f"Échec final de la récupération de la page {page} après plusieurs tentatives: {e}")
                logger.warning("Arrêt de la collecte des top coins en raison d'une erreur réseau persistante.")
                break

        logger.info(f"Récupération depuis CoinGecko terminée. {len(coins)} coins trouvés.")
        if self.service_bus:
            self.service_bus.publish("TopCoinsFetched", TopCoinsFetched(coins=coins[:n]), self.__class__.__name__)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=5, max=20),
        retry=retry_if_exception_type(ccxt.NetworkError),
        before_sleep=lambda rs: logger.warning(
            f"Erreur réseau sur Binance pour {rs.args[0][1]}. Réessai dans {int(rs.next_action.sleep)}s..."
        ),
    )
    def _fetch_historical_prices_task(self, coin_id_symbol: Tuple[str, str], weeks: int, timeframe: str) -> None:
        coin_id, coin_symbol = coin_id_symbol
        symbol = f"{coin_symbol.upper()}/USDC"

        logger.info(f"Récupération des prix pour {symbol} sur le timeframe {timeframe}...")

        prices_df = None
        try:
            if not self.binance.markets:
                self.binance.load_markets()

            if symbol in self.binance.markets:
                days = weeks * 7
                since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
                ohlcv = self.binance.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
                if ohlcv:
                    prices_df = pd.DataFrame(
                        ohlcv,
                        columns=["timestamp", "open", "high", "low", "close", "volume"],
                        index=[datetime.fromtimestamp(x[0] / 1000, tz=timezone.utc) for x in ohlcv],
                    )
        except Exception as e:
            logger.error(f"Échec final de la récupération des prix pour {symbol}: {e}")

        if self.service_bus:
            prices_json = prices_df.to_json(orient="split") if prices_df is not None else None
            event_payload = HistoricalPricesFetched(
                coin_id_symbol=coin_id_symbol, prices_df_json=prices_json, timeframe=timeframe
            )
            self.service_bus.publish("HistoricalPricesFetched", event_payload, self.__class__.__name__)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=5, max=20),
        retry=retry_if_exception_type(ccxt.NetworkError),
        before_sleep=lambda rs: logger.warning(
            f"Erreur réseau sur Binance. Réessai dans {int(rs.next_action.sleep)}s..."
        ),
    )
    def _fetch_precision_data_task(self) -> None:
        precision_data = []
        try:
            logger.info("Récupération des données de précision de tous les marchés Binance...")
            markets = self.binance.load_markets()
            for market_info in markets.values():
                if market_info.get("active"):
                    lot_size_filter = next(
                        (
                            f
                            for f in market_info.get("info", {}).get("filters", [])
                            if f.get("filterType") == "LOT_SIZE"
                        ),
                        None,
                    )
                    price_filter = next(
                        (
                            f
                            for f in market_info.get("info", {}).get("filters", [])
                            if f.get("filterType") == "PRICE_FILTER"
                        ),
                        None,
                    )
                    notional_filter = next(
                        (
                            f
                            for f in market_info.get("info", {}).get("filters", [])
                            if f.get("filterType") == "NOTIONAL"
                        ),
                        None,
                    )

                    if lot_size_filter and price_filter and notional_filter:
                        data = {
                            "symbol": market_info.get("symbol"),
                            "quote_asset": market_info.get("quote"),
                            "base_asset": market_info.get("base"),
                            "status": market_info.get("active"),
                            "base_asset_precision": market_info.get("info", {}).get("baseAssetPrecision"),
                            "step_size": lot_size_filter.get("stepSize"),
                            "min_qty": lot_size_filter.get("minQty"),
                            "tick_size": price_filter.get("tickSize"),
                            "min_notional": notional_filter.get("minNotional"),
                        }
                        precision_data.append(data)
            logger.info(f"{len(precision_data)} marchés actifs trouvés sur Binance.")
        except Exception as e:
            logger.error(f"Échec de la récupération des données de précision de Binance: {e}")

        if self.service_bus:
            self.service_bus.publish("PrecisionDataFetched", PrecisionDataFetched(precision_data=precision_data), self.__class__.__name__)

    def _handle_configuration_provided(self, event: AnalysisConfigurationProvided):
        self.session_guid = event.session_guid
        logger.info(f"DataFetcher a reçu la configuration pour la session {self.session_guid}.")

    def _handle_fetch_top_coins_requested(self, event: FetchTopCoinsRequested):
        self.add_task("_fetch_top_coins_task", event.n)

    def _handle_fetch_historical_prices_requested(self, event: FetchHistoricalPricesRequested):
        self.add_task("_fetch_historical_prices_task", event.coin_id_symbol, event.weeks, event.timeframe)

    def _handle_fetch_precision_data_requested(self, _event: FetchPrecisionDataRequested):
        self.add_task("_fetch_precision_data_task")
