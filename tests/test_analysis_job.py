from unittest.mock import MagicMock

import pandas as pd

from analysis_job import AnalysisJob
from tests.conftest import published_events


def make_job() -> AnalysisJob:
    job = AnalysisJob("1d", MagicMock())
    job.analyzer.rsi_results = {}
    job.set_coins_to_process([("bitcoin", "btc"), ("ethereum", "eth"), ("solana", "sol")])
    return job


def test_btc_is_excluded_from_coins_to_process():
    assert make_job().coins_to_process == [("ethereum", "eth"), ("solana", "sol")]


def test_job_completes_once_every_coin_is_accounted_for():
    """Le job se termine après BTC + chaque crypto, et ignore les doublons."""
    job = make_job()
    job.btc_rsi = pd.Series([50.0])
    job.analyzer.rsi_results = {("ethereum", "eth", "1d"): pd.Series([40.0])}

    job.decrement_counter(("bitcoin", "btc"))
    job.decrement_counter(("ethereum", "eth"))
    job.decrement_counter(("ethereum", "eth"))  # doublon : ne doit pas compter
    assert not published_events(job.analyzer.service_bus, "AnalysisJobCompleted")

    job.decrement_counter(("solana", "sol"))

    job.analyzer.analyze_correlation.assert_called_once()
    (event,) = published_events(job.analyzer.service_bus, "AnalysisJobCompleted")
    assert event.timeframe == "1d"


def test_job_completion_is_published_even_without_btc_rsi():
    """Sans RSI BTC, aucune corrélation n'est calculée mais le job doit se terminer."""
    job = make_job()

    for coin in [("bitcoin", "btc"), ("ethereum", "eth"), ("solana", "sol")]:
        job.decrement_counter(coin)

    job.analyzer.analyze_correlation.assert_not_called()
    assert len(published_events(job.analyzer.service_bus, "AnalysisJobCompleted")) == 1
