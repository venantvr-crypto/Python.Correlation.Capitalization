from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from configuration import AnalysisConfig

# (CoinGecko ID, symbol) pair identifying a coin throughout the pipeline.
type CoinRef = tuple[str, str]


class FrozenBaseModel(BaseModel):
    """A base model that is immutable, similar to a frozen dataclass."""

    model_config = ConfigDict(frozen=True)


class AnalysisConfigurationProvided(FrozenBaseModel):
    """Configuration sent to all services at startup."""

    session_guid: str = Field(description="GUID unique pour la session d'analyse.")
    config: AnalysisConfig = Field(description="Objet de configuration complet pour l'analyse.")


class RunAnalysisRequested(FrozenBaseModel):
    """Event to trigger the start of the analysis workflow."""

    payload: bool = Field(default=True, description="Payload factice pour la compatibilité des événements.")


class FetchTopCoinsRequested(FrozenBaseModel):
    """Request to fetch the top N coins from the data source."""

    n: PositiveInt = Field(description="Le nombre de top cryptos à récupérer.")


class TopCoinsFetched(FrozenBaseModel):
    """Event indicating that the list of top coins has been fetched."""

    coins: list[dict] = Field(description="Liste des données brutes des cryptos récupérées.")


class SingleCoinFetched(FrozenBaseModel):
    """Event published for each individual coin fetched."""

    coin: dict = Field(description="Données brutes pour une seule crypto.")


class FetchHistoricalPricesRequested(FrozenBaseModel):
    """Request to fetch historical price data for a specific coin."""

    coin_id_symbol: CoinRef = Field(description="Tuple (ID, Symbole) de la crypto à traiter.")
    weeks: PositiveInt = Field(description="Nombre de semaines de données à récupérer.")
    timeframe: str = Field(description="L'unité de temps pour les données (ex: '1h', '1d').")


class HistoricalPricesFetched(FrozenBaseModel):
    """Event indicating that historical prices for a coin have been fetched."""

    coin_id_symbol: CoinRef = Field(description="Tuple (ID, Symbole) de la crypto traitée.")
    prices_df_json: str | None = Field(default=None, description="Le DataFrame des prix sérialisé en JSON, ou None si échec.")
    timeframe: str = Field(description="L'unité de temps des données.")


class CalculateRSIRequested(FrozenBaseModel):
    """Request to calculate the RSI for a series of prices."""

    coin_id_symbol: CoinRef = Field(description="Tuple (ID, Symbole) de la crypto à traiter.")
    prices_series_json: str | None = Field(default=None, description="La série des prix de clôture sérialisée en JSON.")
    timeframe: str = Field(description="L'unité de temps concernée.")


class RSICalculated(FrozenBaseModel):
    """Event indicating that the RSI for a coin has been calculated."""

    coin_id_symbol: CoinRef = Field(description="Tuple (ID, Symbole) de la crypto traitée.")
    rsi_series_json: str | None = Field(default=None, description="La série RSI calculée et sérialisée en JSON, ou None si échec.")
    timeframe: str = Field(description="L'unité de temps concernée.")


class CorrelationAnalyzed(FrozenBaseModel):
    """Event containing the result of a single correlation analysis."""

    result: dict | None = Field(default=None, description="Dictionnaire contenant le résultat de la corrélation, ou None.")
    timeframe: str = Field(description="L'unité de temps de l'analyse.")


class CoinProcessingFailed(FrozenBaseModel):
    """Event indicating that the processing pipeline failed for a coin."""

    coin_id_symbol: CoinRef = Field(description="Tuple (ID, Symbole) de la crypto qui a échoué.")
    timeframe: str = Field(description="L'unité de temps où l'échec a eu lieu.")


class FinalResultsReady(FrozenBaseModel):
    """Event published when all analyses are complete and results are aggregated."""

    results: list[dict] = Field(description="Liste agrégée de tous les résultats de corrélation.")
    weeks: PositiveInt = Field(description="Rappel du nombre de semaines analysées.")
    timeframes: list[str] = Field(description="Rappel des unités de temps analysées.")


class DisplayCompleted(FrozenBaseModel):
    """Event indicating that the final results have been displayed."""

    payload: bool = Field(default=True, description="Payload factice indiquant la fin de l'affichage.")


class AnalysisJobCompleted(FrozenBaseModel):
    """Event indicating that all coins for a specific timeframe have been processed."""

    timeframe: str = Field(description="L'unité de temps pour laquelle le job est terminé.")


class FetchPrecisionDataRequested(FrozenBaseModel):
    """Request to fetch market precision data from the exchange."""

    payload: bool = Field(default=True, description="Payload factice pour demander les données de précision.")


class PrecisionDataFetched(FrozenBaseModel):
    """Event containing the fetched market precision data."""

    precision_data: list[dict] = Field(description="Liste des données de précision des marchés de l'exchange.")
