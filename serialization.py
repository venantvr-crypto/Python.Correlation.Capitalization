"""JSON (de)serialization helpers for pandas objects carried in event payloads."""

from io import StringIO

import pandas as pd


def _with_utc_index[T: (pd.DataFrame, pd.Series)](obj: T) -> T:
    obj.index = pd.to_datetime(obj.index, unit="ms", utc=True)
    return obj


def frame_to_json(frame: pd.DataFrame | pd.Series) -> str:
    return frame.to_json(orient="split")


def frame_from_json(payload: str) -> pd.DataFrame:
    """Rebuilds a DataFrame (UTC datetime index) serialized with `frame_to_json`."""
    return _with_utc_index(pd.read_json(StringIO(payload), orient="split"))


def series_from_json(payload: str) -> pd.Series:
    """Rebuilds a Series (UTC datetime index) serialized with `frame_to_json`."""
    return _with_utc_index(pd.read_json(StringIO(payload), orient="split", typ="series"))
