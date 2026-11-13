from configuration import AnalysisConfig
from main import parse_args


def test_default_arguments_build_a_valid_configuration():
    config = AnalysisConfig(**vars(parse_args([])))

    assert config.timeframes == ["1h", "1d"]
    assert config.top_n_coins == 1000


def test_arguments_override_configuration():
    config = AnalysisConfig(**vars(parse_args(["--weeks", "2", "--top-n-coins", "10", "--timeframes", "1h"])))

    assert (config.weeks, config.top_n_coins, config.timeframes) == (2, 10, ["1h"])
