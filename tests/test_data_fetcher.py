from unittest.mock import ANY

import ccxt
import pandas as pd

# noinspection PyPackageRequirements
import pytest

from agents.data_fetcher import OHLCV_PAGE_SIZE, DataFetcher

# On importe tous les événements nécessaires pour le test
from events import AnalysisConfigurationProvided, FetchTopCoinsRequested, TopCoinsFetched
from serialization import frame_from_json
from tests.conftest import published_events


# Une fixture pour créer une instance de DataFetcher pour chaque test
@pytest.fixture
def data_fetcher(mock_service_bus):
    fetcher = DataFetcher(service_bus=mock_service_bus)
    # On mock les clients externes pour ne pas faire de vrais appels réseau
    fetcher.cg = ANY
    fetcher.binance = ANY
    return fetcher


def test_handle_configuration_provided(data_fetcher, analysis_config):  # On injecte la fixture 'analysis_config'
    """Teste que la configuration est bien prise en compte."""
    # On utilise l'objet config valide au lieu de ANY
    event = AnalysisConfigurationProvided(session_guid="test-guid-123", config=analysis_config)

    data_fetcher._handle_configuration_provided(event)
    assert data_fetcher.session_guid == "test-guid-123"


def test_handle_fetch_top_coins_requested_adds_task(data_fetcher, mocker):
    """Teste que la réception de l'événement de requête ajoute la bonne tâche."""
    # On espionne la méthode add_task pour voir si elle est appelée correctement
    mocker.patch.object(data_fetcher, "add_task")

    event = FetchTopCoinsRequested(n=50)
    data_fetcher._handle_fetch_top_coins_requested(event)

    # On vérifie que la bonne tâche a été mise en file d'attente avec le bon argument
    data_fetcher.add_task.assert_called_once_with("_fetch_top_coins_task", 50)


def test_fetch_top_coins_task_publishes_result(data_fetcher, mocker):
    """
    Teste que la tâche de fetch publie bien un événement TopCoinsFetched en cas de succès.
    """
    mock_coins = [{"id": "bitcoin"}]
    # On configure le mock du client CoinGecko pour qu'il retourne nos fausses données
    data_fetcher.cg = mocker.MagicMock()
    data_fetcher.cg.get_coins_markets.return_value = mock_coins

    # On exécute la tâche
    data_fetcher._fetch_top_coins_task(n=1)

    # On vérifie que le bon événement Pydantic a été publié
    expected_event = TopCoinsFetched(coins=mock_coins)
    data_fetcher.service_bus.publish.assert_called_once_with("TopCoinsFetched", expected_event, "DataFetcher")


def candle(i: int) -> list:
    """Une bougie horaire factice, horodatée en millisecondes."""
    return [1_700_000_000_000 + i * 3_600_000, 1.0, 2.0, 0.5, 1.5, 100.0]


def test_fetch_historical_prices_paginates(data_fetcher, mocker):
    """L'historique complet est récupéré, au-delà de la limite d'une requête Binance."""
    candles = [candle(i) for i in range(OHLCV_PAGE_SIZE + 5)]
    data_fetcher.binance = mocker.MagicMock()
    data_fetcher.binance.markets = {"ETH/USDC": {}}
    data_fetcher.binance.fetch_ohlcv.side_effect = [candles[:OHLCV_PAGE_SIZE], candles[OHLCV_PAGE_SIZE:]]

    data_fetcher._fetch_historical_prices_task(("ethereum", "eth"), 50, "1h")

    assert data_fetcher.binance.fetch_ohlcv.call_count == 2
    # La seconde page reprend juste après la dernière bougie reçue.
    assert data_fetcher.binance.fetch_ohlcv.call_args.kwargs["since"] == candles[OHLCV_PAGE_SIZE - 1][0] + 1

    (event,) = published_events(data_fetcher.service_bus, "HistoricalPricesFetched")
    prices_df = frame_from_json(event.prices_df_json)
    assert len(prices_df) == OHLCV_PAGE_SIZE + 5
    assert prices_df.index[0] == pd.Timestamp(candles[0][0], unit="ms", tz="UTC")
    assert list(prices_df.columns) == ["open", "high", "low", "close", "volume"]


def test_fetch_historical_prices_unknown_market_publishes_empty_result(data_fetcher, mocker):
    """Une paire absente de Binance publie un résultat vide, sans appel réseau."""
    data_fetcher.binance = mocker.MagicMock()
    data_fetcher.binance.markets = {"BTC/USDC": {}}

    data_fetcher._fetch_historical_prices_task(("unknown", "xyz"), 4, "1d")

    data_fetcher.binance.fetch_ohlcv.assert_not_called()
    (event,) = published_events(data_fetcher.service_bus, "HistoricalPricesFetched")
    assert event.prices_df_json is None


def test_fetch_historical_prices_failure_publishes_coin_failure(data_fetcher, mocker):
    """Une erreur de l'exchange publie CoinProcessingFailed pour ne pas bloquer le job."""
    data_fetcher.binance = mocker.MagicMock()
    data_fetcher.binance.markets = {"ETH/USDC": {}}
    data_fetcher.binance.fetch_ohlcv.side_effect = ccxt.BadSymbol("boom")

    data_fetcher._fetch_historical_prices_task(("ethereum", "eth"), 4, "1d")

    (event,) = published_events(data_fetcher.service_bus, "CoinProcessingFailed")
    assert event.coin_id_symbol == ("ethereum", "eth")
    assert event.timeframe == "1d"


def test_fetch_precision_data_keeps_active_markets_with_all_filters(data_fetcher, mocker):
    filters = [
        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
        {"filterType": "NOTIONAL", "minNotional": "5"},
    ]
    complete = {"symbol": "ETH/USDC", "quote": "USDC", "base": "ETH", "active": True, "info": {"baseAssetPrecision": 8, "filters": filters}}
    data_fetcher.binance = mocker.MagicMock()
    data_fetcher.binance.load_markets.return_value = {
        "ETH/USDC": complete,
        "OLD/USDC": {**complete, "symbol": "OLD/USDC", "active": False},
        "BAD/USDC": {**complete, "symbol": "BAD/USDC", "info": {"filters": filters[:1]}},
    }

    data_fetcher._fetch_precision_data_task()

    (event,) = published_events(data_fetcher.service_bus, "PrecisionDataFetched")
    assert [m["symbol"] for m in event.precision_data] == ["ETH/USDC"]
    assert event.precision_data[0]["min_notional"] == "5"


def test_fetch_precision_data_failure_publishes_empty_list(data_fetcher, mocker):
    """En cas d'échec, une liste vide est publiée pour que l'orchestrateur s'arrête proprement."""
    data_fetcher.binance = mocker.MagicMock()
    data_fetcher.binance.load_markets.side_effect = ccxt.ExchangeError("boom")

    data_fetcher._fetch_precision_data_task()

    (event,) = published_events(data_fetcher.service_bus, "PrecisionDataFetched")
    assert event.precision_data == []
