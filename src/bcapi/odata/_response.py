"""OData response wrapper."""

from __future__ import annotations

from typing import Any


class ODataResponse:
    """Wraps a BC OData API response."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    @property
    def value(self) -> list[dict[str, Any]]:
        """The records (from the 'value' array)."""
        return self._data.get("value", [])

    @property
    def count(self) -> int | None:
        """Total record count if $count was requested."""
        return self._data.get("@odata.count")

    @property
    def next_link(self) -> str | None:
        """URL for the next page, if any."""
        return self._data.get("@odata.nextLink")

    @property
    def context(self) -> str | None:
        """OData context URL."""
        return self._data.get("@odata.context")

    @property
    def raw(self) -> dict[str, Any]:
        """The raw response dict."""
        return self._data

    def __len__(self) -> int:
        return len(self.value)

    def __iter__(self):
        return iter(self.value)

    def __getitem__(self, index):
        return self.value[index]
