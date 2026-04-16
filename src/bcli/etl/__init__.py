"""ETL pipeline — extract Business Central data via dlt."""

from bcli.etl._entities import EntityDef, load_entities_from_registry

__all__ = [
    "EntityDef",
    "business_central",
    "load_entities_from_registry",
]


def business_central(**kwargs):
    """Lazy import to avoid requiring dlt at SDK import time."""
    from bcli.etl._source import business_central as _bc

    return _bc(**kwargs)
