import threading
from typing import Optional, Tuple, List, Dict

import pandas as pd

from data_fetcher import DataFetcher
from database_manager import DatabaseManager
from display_agent import DisplayAgent
from events import RunAnalysisRequested, TopCoinsFetched, MarketCapThresholdCalculated, HistoricalPricesFetched, \
    RSICalculated, CorrelationAnalyzed, CoinProcessingFailed, DisplayCompleted, PrecisionDataFetched
from logger import logger
from market_cap_agent import MarketCapAgent
from rsi_calculator import RSICalculator
from service_bus import ServiceBus


class CryptoAnalyzer:
    """Orchestre l'analyse des corrélations RSI pour le scalping, piloté par les événements."""

    def __init__(self, weeks: int = 50, correlation_threshold: float = 0.7,
                 rsi_period: int = 14, session_guid: Optional[str] = None, min_correlation_samples: int = 30,
                 quote_currencies: List[str] = None):
        self.weeks = weeks
        self.correlation_threshold = correlation_threshold
        self.rsi_period = rsi_period
        self.min_correlation_samples = min_correlation_samples
        self.quote_currencies = quote_currencies if quote_currencies is not None else ['USDT', 'USDC', 'BNB']
        self.session_guid = session_guid
        self.market_caps: Dict[str, float] = {}
        self.low_cap_threshold: float = float('inf')
        self.btc_rsi_by_quote: Dict[str, pd.Series] = {}  # RSI de BTC pour chaque devise de quote
        self.rsi_results: Dict[Tuple[str, str, str], pd.Series] = {}  # (coin_id, symbol, quote_currency) -> RSI series
        self.results: List[Dict] = []
        self._pairs_to_process: List[Tuple[str, str, str]] = []  # (coin_id, symbol, quote_currency)
        self.coins: List[Dict] = []
        self.precision_data: Dict[str, Dict] = {}

        # Attributs de synchronisation et de comptage
        self._processing_counter = 0
        self._counter_lock = threading.Lock()
        self._all_processing_completed = threading.Event()
        self._precision_data_loaded = threading.Event()
        self._top_coins_loaded = threading.Event()
        self._rsi_processing_completed = threading.Event()

        # Initialisation des composants
        self.service_bus = ServiceBus()
        self.db_manager = DatabaseManager(service_bus=self.service_bus)
        self.data_fetcher = DataFetcher(session_guid=self.session_guid,
                                        service_bus=self.service_bus,
                                        quote_currencies=self.quote_currencies)
        self.rsi_calculator = RSICalculator(periods=self.rsi_period, service_bus=self.service_bus)
        self.display_agent = DisplayAgent(service_bus=self.service_bus)
        self.market_cap_agent = MarketCapAgent(service_bus=self.service_bus)

        self._setup_event_subscriptions()

    def _setup_event_subscriptions(self):
        """Définit les abonnements aux événements du bus de services."""
        self.service_bus.subscribe("RunAnalysisRequested", self._handle_run_analysis_requested)
        self.service_bus.subscribe("TopCoinsFetched", self._handle_top_coins_fetched)
        self.service_bus.subscribe("MarketCapThresholdCalculated", self._handle_market_cap_threshold_calculated)
        self.service_bus.subscribe("HistoricalPricesFetched", self._handle_historical_prices_fetched)
        self.service_bus.subscribe("RSICalculated", self._handle_rsi_calculated)
        self.service_bus.subscribe("CorrelationAnalyzed", self._handle_correlation_analyzed)
        self.service_bus.subscribe("CoinProcessingFailed", self._handle_coin_processing_failed)
        self.service_bus.subscribe("DisplayCompleted", self._handle_display_completed)
        self.service_bus.subscribe("PrecisionDataFetched", self._handle_precision_data_fetched)

    def _handle_run_analysis_requested(self, event: RunAnalysisRequested):
        """Lance les premières requêtes de données."""
        logger.info("Démarrage de l'analyse, demande des données de précision et de la liste des top coins.")
        self.service_bus.publish("FetchPrecisionDataRequested", {'session_guid': event.session_guid})
        self.service_bus.publish("FetchTopCoinsRequested", {'n': 5000, 'session_guid': event.session_guid})

    def _handle_precision_data_fetched(self, event: PrecisionDataFetched):
        """Reçoit TOUTES les paires, les stocke et signale que les données sont prêtes."""
        logger.info(f"Données de précision reçues pour {len(event.precision_data)} paires.")
        self.precision_data = {item['symbol']: item for item in event.precision_data}
        self._precision_data_loaded.set()
        self._start_analysis_if_ready()

    def _handle_top_coins_fetched(self, event: TopCoinsFetched):
        """Reçoit les top coins et signale que les données sont prêtes."""
        self.coins = event.coins
        self._top_coins_loaded.set()
        self._start_analysis_if_ready()

    def _start_analysis_if_ready(self):
        """Vérifie si toutes les données initiales sont là, puis filtre et lance l'analyse."""
        if not (self._precision_data_loaded.is_set() and self._top_coins_loaded.is_set()):
            return

        logger.info("Données de précision et top coins reçus. Démarrage du filtrage des paires.")

        # Créer une liste de paires valides à partir des données de précision
        self._pairs_to_process = [
            (coin.get('id', ''), coin.get('symbol', '').upper(), market['quote_asset'])
            for market in self.precision_data.values()
            for coin in self.coins
            if coin.get('symbol', '').upper() == market['base_asset']
               and market['quote_asset'] in self.quote_currencies
        ]

        logger.info(f"Nombre de paires à traiter : {len(self._pairs_to_process)}")

        for coin in self.coins:
            self.service_bus.publish("SingleCoinFetched", {'coin': coin, 'session_guid': self.session_guid})

        self.service_bus.publish("CalculateMarketCapThresholdRequested", {
            'coins': self.coins,
            'session_guid': self.session_guid
        })

    def _handle_market_cap_threshold_calculated(self, event: MarketCapThresholdCalculated):
        """Stocke les données de capitalisation et lance la récupération des prix pour BTC et autres paires."""
        self.market_caps = event.market_caps
        self.low_cap_threshold = event.low_cap_threshold

        logger.info("Demande des données pour Bitcoin pour chaque devise de quote...")
        for quote in self.quote_currencies:
            self.service_bus.publish("FetchHistoricalPricesRequested", {
                'coin_id_symbol': ('bitcoin', 'BTC'),
                'weeks': self.weeks,
                'session_guid': event.session_guid,
                'quote_currencies_override': [quote]
            })

        # Initialiser le compteur pour toutes les paires
        with self._counter_lock:
            self._processing_counter = len(self._pairs_to_process)
            logger.info(f"Initialisation du compteur de traitement à {self._processing_counter} paires.")

        # Lancer la récupération des prix pour toutes les paires (y compris BTC)
        for coin_id, symbol, quote in self._pairs_to_process:
            self.service_bus.publish("FetchHistoricalPricesRequested", {
                'coin_id_symbol': (coin_id, symbol),
                'weeks': self.weeks,
                'session_guid': event.session_guid,
                'quote_currencies_override': [quote]
            })

    def _handle_historical_prices_fetched(self, event: HistoricalPricesFetched):
        """Lance le calcul du RSI pour les prix reçus."""
        coin_id_symbol = event.coin_id_symbol
        prices_df = event.prices_df
        session_guid = event.session_guid
        quote_currency = event.quote_currency
        coin_symbol = coin_id_symbol[1]

        if prices_df is None or prices_df.empty:
            logger.warning(f"Données de prix manquantes pour {coin_symbol}/{quote_currency}. Le coin ne sera pas analysé.")
            self.service_bus.publish("CoinProcessingFailed", {
                'coin_id_symbol': coin_id_symbol,
                'quote_currency': quote_currency
            })
            return

        self.service_bus.publish("CalculateRSIRequested", {
            'coin_id_symbol': coin_id_symbol,
            'prices_series': prices_df['close'],
            'session_guid': session_guid,
            'quote_currency': quote_currency
        })

    def _handle_rsi_calculated(self, event: RSICalculated):
        """Stocke le RSI et vérifie si tous les RSI sont calculés avant de lancer l'analyse de corrélation."""
        coin_id_symbol = event.coin_id_symbol
        rsi_series = event.rsi
        session_guid = event.session_guid
        quote_currency = event.quote_currency
        coin_symbol = coin_id_symbol[1]

        if rsi_series is None or rsi_series.empty:
            logger.warning(f"Série RSI manquante pour {coin_symbol}/{quote_currency}.")
            self.service_bus.publish("CoinProcessingFailed", {
                'coin_id_symbol': coin_id_symbol,
                'quote_currency': quote_currency
            })
            return

        # Stocker le RSI dans rsi_results
        self.rsi_results[(coin_id_symbol[0], coin_symbol, quote_currency)] = rsi_series
        logger.info(f"RSI stocké pour {coin_symbol}/{quote_currency}.")

        if coin_symbol.lower() == 'btc':
            logger.info(f"RSI de référence pour BTC/{quote_currency} calculé et stocké.")
            self.btc_rsi_by_quote[quote_currency] = rsi_series

        # Décrémenter le compteur
        self._decrement_processing_counter()

    def _decrement_processing_counter(self):
        """Décrémente le compteur de paires à traiter et lance l'analyse de corrélation si terminée."""
        with self._counter_lock:
            self._processing_counter -= 1
            logger.debug(f"Compteur de traitement : {self._processing_counter}")
            if self._processing_counter <= 0:
                logger.info("Tous les calculs RSI sont terminés. Démarrage de l'analyse de corrélation.")
                self._rsi_processing_completed.set()
                self._start_correlation_analysis()

    def _start_correlation_analysis(self):
        """Analyse les corrélations pour toutes les paires avec RSI valide."""
        logger.info("Début de l'analyse de corrélation pour les paires avec RSI valide.")
        for (coin_id, coin_symbol, quote_currency), rsi_series in self.rsi_results.items():
            if coin_symbol.lower() != 'btc':  # Exclure BTC lui-même
                self._analyze_correlation((coin_id, coin_symbol), rsi_series, self.session_guid, quote_currency)

        # Après avoir lancé toutes les corrélations, signaler la fin si aucune corrélation n'est en cours
        with self._counter_lock:
            if not self.results:
                logger.info("Aucune corrélation valide trouvée. Publication de FinalResultsReady.")
                self.service_bus.publish("FinalResultsReady", {
                    'results': self.results,
                    'weeks': self.weeks,
                    'session_guid': self.session_guid,
                    'db_manager': self.db_manager
                })

    def _analyze_correlation(self, coin_id_symbol: Tuple[str, str], coin_rsi: pd.Series, session_guid: str, quote_currency: str):
        """Analyse la corrélation entre le RSI d'un coin et celui de BTC pour la même devise de quote."""
        btc_rsi = self.btc_rsi_by_quote.get(quote_currency)
        if btc_rsi is None:
            logger.warning(f"RSI de BTC/{quote_currency} non disponible pour l'analyse. Ignoré.")
            self.service_bus.publish("CoinProcessingFailed", {
                'coin_id_symbol': coin_id_symbol,
                'quote_currency': quote_currency
            })
            return

        common_index_rsi = btc_rsi.index.intersection(coin_rsi.index)
        if len(common_index_rsi) < self.min_correlation_samples:
            logger.warning(f"Index commun RSI insuffisant pour {coin_id_symbol[1]}/{quote_currency} ({len(common_index_rsi)} échantillons).")
            self.service_bus.publish("CoinProcessingFailed", {
                'coin_id_symbol': coin_id_symbol,
                'quote_currency': quote_currency
            })
            return

        aligned_btc_rsi = btc_rsi.loc[common_index_rsi]
        aligned_coin_rsi = coin_rsi.loc[common_index_rsi]
        correlation = aligned_coin_rsi.corr(aligned_btc_rsi)

        market_cap = self.market_caps.get(coin_id_symbol[1], 0)
        low_cap_quartile = market_cap <= self.low_cap_threshold

        if pd.isna(correlation) or abs(correlation) < self.correlation_threshold or not low_cap_quartile:
            logger.info(f"Corrélation pour {coin_id_symbol[1]}/{quote_currency} non pertinente (corrélation={correlation}, market_cap={market_cap}, low_cap={low_cap_quartile}).")
            self.service_bus.publish("CoinProcessingFailed", {
                'coin_id_symbol': coin_id_symbol,
                'quote_currency': quote_currency
            })
            return

        result = {
            'coin_id': coin_id_symbol[0],
            'coin_symbol': coin_id_symbol[1],
            'quote_currency': quote_currency,
            'correlation': correlation,
            'market_cap': market_cap,
            'low_cap_quartile': low_cap_quartile
        }
        logger.info(f"Corrélation calculée pour {coin_id_symbol[1]}/{quote_currency}: {correlation:.3f}")
        self.service_bus.publish("CorrelationAnalyzed", {'result': result, 'session_guid': session_guid})

    def _handle_correlation_analyzed(self, event: CorrelationAnalyzed):
        """Ajoute un résultat d'analyse réussi et décrémente le compteur."""
        if event.result:
            self.results.append(event.result)
        self._decrement_processing_counter()

    def _handle_coin_processing_failed(self, event: CoinProcessingFailed):
        """Gère l'échec du traitement d'un coin en décrémentant le compteur."""
        logger.debug(f"Échec du traitement pour {event.coin_id_symbol[1]}/{event.quote_currency}")
        self._decrement_processing_counter()

    def _handle_display_completed(self, event: DisplayCompleted):
        """Signale que l'ensemble du processus est terminé."""
        self._all_processing_completed.set()
        logger.info("L'analyse, l'affichage et l'arrêt des services sont terminés.")

    def run(self) -> None:
        """Démarre tous les services, lance l'analyse et attend la fin."""
        services = [
            self.service_bus, self.db_manager, self.data_fetcher,
            self.rsi_calculator, self.display_agent, self.market_cap_agent
        ]

        for service in services:
            service.start()

        self.service_bus.publish("RunAnalysisRequested", {'session_guid': self.session_guid})
        self._all_processing_completed.wait()

        for service in reversed(services):
            service.stop()
