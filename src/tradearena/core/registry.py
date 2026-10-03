from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PluginRegistry:
    """Small registry for replaceable research components."""

    _items: dict[str, dict[str, Any]] = field(default_factory=dict)

    def register(self, category: str, name: str, plugin: Any) -> None:
        self._items.setdefault(category, {})
        if name in self._items[category]:
            raise ValueError(f"Plugin already registered: {category}/{name}")
        self._items[category][name] = plugin

    def get(self, category: str, name: str) -> Any:
        try:
            return self._items[category][name]
        except KeyError as exc:
            available = ", ".join(sorted(self._items.get(category, {}))) or "<none>"
            raise KeyError(f"Unknown plugin {category}/{name}. Available: {available}") from exc

    def names(self, category: str) -> list[str]:
        return sorted(self._items.get(category, {}))

    def categories(self) -> list[str]:
        return sorted(self._items)
