"""
Wires up the SellerDataSource adapter based on config. This is THE swap point
for connecting to a real marketplace backend instead of the demo CSVs — change
`data_source_kind` and the relevant connection settings, and every route/service
that depends on `SellerDataSource` picks it up with zero code changes elsewhere.
"""
from __future__ import annotations

from functools import lru_cache

from core.config import get_settings
from core.database import engine
from services.data_source import (
    CSVSellerDataSource, RESTSellerDataSource, SQLSellerDataSource, SellerDataSource,
)


@lru_cache
def _build_source() -> SellerDataSource:
    settings = get_settings()
    kind = getattr(settings, "data_source_kind", "sql")

    if kind == "sql":
        return SQLSellerDataSource(engine)
    if kind == "rest":
        # settings.seller_api_key is a pydantic SecretStr — passing it
        # straight through would bake the literal string "**********" into
        # the Authorization header (SecretStr.__str__ redacts), silently
        # breaking every outbound request. Must unwrap with
        # get_secret_value() to get the real key.
        api_key = settings.seller_api_key
        return RESTSellerDataSource(
            base_url=getattr(settings, "seller_api_base_url", "") or "",
            api_key=api_key.get_secret_value() if api_key is not None else "",
        )
    if kind == "csv":
        return CSVSellerDataSource(
            csv_path=getattr(settings, "seller_csv_path", "data/sellers.csv"),
            default_tenant_id="demo",
        )
    raise ValueError(f"Unknown data_source_kind: {kind}")


def get_seller_source() -> SellerDataSource:
    return _build_source()
