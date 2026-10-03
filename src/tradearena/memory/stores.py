from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class InMemoryResearchMemory:
    """Reference memory store for journals, theses, and failure cases."""

    name: str = "in-memory-research"
    events: list[dict[str, Any]] = field(default_factory=list)
    theses: dict[str, str] = field(default_factory=dict)
    failure_cases: list[dict[str, Any]] = field(default_factory=list)

    def record(self, event_type: str, payload: dict[str, Any]) -> None:
        event = {"type": event_type, "payload": payload}
        self.events.append(event)

        if event_type == "thesis":
            symbol = str(payload.get("symbol", "UNKNOWN"))
            self.theses[symbol] = str(payload.get("text", ""))
        if event_type == "failure":
            self.failure_cases.append(payload)

    def recent(self, event_type: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
        events = self.events
        if event_type is not None:
            events = [event for event in events if event["type"] == event_type]
        return events[-limit:]
