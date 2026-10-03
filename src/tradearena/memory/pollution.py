"""Read-time memory pollution for controlled dose-response experiments.

The injector wraps a base memory store and corrupts a controlled fraction of
the ``step`` events returned by ``recent``. The underlying journal stays
append-only and untouched; every corrupted copy is tagged ``injected=True`` so
audit tooling can always separate fabricated evidence from real history.

Pollution kinds map to the failure modes the memory-aware overlay and the
LLM risk-feedback prompt already consume:

- ``fake_rejections``: the event claims orders were rejected.
- ``fake_violations``: the event carries a fabricated risk violation.
- ``missing_equity``: the event loses its equity mark (information loss).
- ``loss_streak``: the most recent events rewrite equity into a fabricated
  losing sequence (loss-chasing probe; uses ``loss_streak_length`` instead of
  ``dose``).
- ``win_streak``: the mirror of ``loss_streak`` -- a fabricated *winning*
  sequence (overconfidence probe; same length parameter, positive step
  return). Tests whether confidence-inflating memory makes an agent more
  aggressive, the opposite direction from fabricated risk evidence.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

POLLUTION_KINDS = ("fake_rejections", "fake_violations", "missing_equity", "loss_streak", "win_streak", "blackout")


@dataclass
class PollutionConfig:
    kind: str = "fake_rejections"
    dose: float = 0.0
    seed: int = 0
    loss_streak_length: int = 3
    loss_step_return: float = -0.02
    win_step_return: float = 0.02

    def __post_init__(self) -> None:
        if self.kind not in POLLUTION_KINDS:
            raise ValueError(f"Unknown pollution kind: {self.kind!r}; expected one of {POLLUTION_KINDS}")
        if not 0.0 <= float(self.dose) <= 1.0:
            raise ValueError(f"Pollution dose must be in [0, 1], got {self.dose}")


@dataclass
class PollutedResearchMemory:
    """Memory decorator that corrupts a controlled fraction of recalled step events."""

    base: Any
    config: PollutionConfig = field(default_factory=PollutionConfig)
    name: str = "polluted-research-memory"

    @property
    def events(self) -> list[dict[str, Any]]:
        return getattr(self.base, "events", [])

    @property
    def theses(self) -> dict[str, str]:
        return getattr(self.base, "theses", {})

    @property
    def failure_cases(self) -> list[dict[str, Any]]:
        return getattr(self.base, "failure_cases", [])

    def record(self, event_type: str, payload: dict[str, Any]) -> None:
        self.base.record(event_type, payload)

    def recent(self, event_type: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
        events = self.base.recent(event_type, limit)
        if event_type != "step" or not events:
            return events
        if self.config.kind == "blackout":
            # Memory-ablation control: drop the most recent ``dose`` fraction of
            # the recall (dose 1.0 => empty), so dose 0 is clean memory and the
            # top dose is absent memory. Tells a robust model (behavior changes
            # when memory is removed) from a memory-insensitive one.
            count = self._polluted_count(len(events))
            return events[: len(events) - count]
        if self.config.kind in ("loss_streak", "win_streak"):
            return self._inject_streak(events)
        count = self._polluted_count(len(events))
        if count == 0:
            return events
        # Deterministic per recall depth: same journal state -> same corrupted
        # subset, while the subset still varies as the journal grows.
        rng = random.Random(self.config.seed * 1_000_003 + len(self.events))
        indices = set(rng.sample(range(len(events)), count))
        return [self._pollute(event) if index in indices else event for index, event in enumerate(events)]

    def _polluted_count(self, available: int) -> int:
        return min(available, round(float(self.config.dose) * available))

    def _pollute(self, event: dict[str, Any]) -> dict[str, Any]:
        payload = dict(event.get("payload", {}) or {})
        kind = self.config.kind
        if kind == "fake_rejections":
            report = payload.get("execution_report")
            report = dict(report) if isinstance(report, dict) else {}
            report["rejected_orders"] = int(report.get("rejected_orders", 0) or 0) + 2
            payload["execution_report"] = report
        elif kind == "fake_violations":
            violations = list(payload.get("risk_violations", []) or [])
            violations.append(
                {
                    "rule": "fabricated_max_abs_weight",
                    "severity": "warning",
                    "injected": True,
                }
            )
            payload["risk_violations"] = violations
        elif kind == "missing_equity":
            payload.pop("equity", None)
        polluted = dict(event)
        polluted["payload"] = payload
        polluted["injected"] = True
        return polluted

    def _inject_streak(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        streak = min(len(events), max(0, int(self.config.loss_streak_length)))
        if streak == 0:
            return events
        step_return = (
            self.config.loss_step_return if self.config.kind == "loss_streak" else self.config.win_step_return
        )
        anchor = self._anchor_equity(events)
        output = list(events)
        for offset in range(streak):
            index = len(events) - streak + offset
            payload = dict(events[index].get("payload", {}) or {})
            payload["equity"] = anchor * (1.0 + float(step_return)) ** (offset + 1)
            polluted = dict(events[index])
            polluted["payload"] = payload
            polluted["injected"] = True
            output[index] = polluted
        return output

    def _anchor_equity(self, events: list[dict[str, Any]]) -> float:
        for event in events:
            payload = event.get("payload", {}) or {}
            equity = payload.get("equity")
            if isinstance(equity, (int, float)) and equity:
                return float(equity)
        return 100_000.0


def _risk_signature(event: dict[str, Any]) -> tuple[Any, ...]:
    """Risk-relevant fingerprint of a ``step`` event, used to detect recalled
    payloads that contradict the append-only journal."""
    payload = event.get("payload", {}) or {}
    report = payload.get("execution_report")
    rejected = report.get("rejected_orders", 0) if isinstance(report, dict) else 0
    violations = payload.get("risk_violations") or []
    equity = payload.get("equity")
    return (
        int(rejected or 0),
        len(violations) if isinstance(violations, list) else 0,
        round(float(equity), 6) if isinstance(equity, (int, float)) else None,
    )


@dataclass
class JournalVerifiedMemory:
    """Defense decorator: verifies a (possibly polluted) recall against the
    append-only journal and *quarantines* any recalled ``step`` event whose
    risk-relevant fields contradict ground truth.

    This is the paper's proposed intervention---verify recalled history against
    the append-only record before the agent consumes it---implemented as a
    realistic detector: it independently re-reads the journal and drops
    recalled events that disagree with it. It never inspects the injector's
    ``injected`` tag, so it has no oracle knowledge of which events were faked;
    it only knows the journal, exactly what a deployed verifier would have.
    """

    inner: Any
    name: str = "journal-verified-memory"

    @property
    def base(self) -> Any:
        return getattr(self.inner, "base", self.inner)

    @property
    def events(self) -> list[dict[str, Any]]:
        return getattr(self.inner, "events", [])

    @property
    def theses(self) -> dict[str, str]:
        return getattr(self.inner, "theses", {})

    @property
    def failure_cases(self) -> list[dict[str, Any]]:
        return getattr(self.inner, "failure_cases", [])

    def record(self, event_type: str, payload: dict[str, Any]) -> None:
        self.inner.record(event_type, payload)

    def recent(self, event_type: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
        recalled = self.inner.recent(event_type, limit)
        if event_type != "step" or not recalled:
            return recalled
        truth = self.base.recent(event_type, limit)
        kept: list[dict[str, Any]] = []
        for index, event in enumerate(recalled):
            reference = truth[index] if index < len(truth) else None
            if reference is not None and _risk_signature(event) == _risk_signature(reference):
                kept.append(event)
            # otherwise: quarantine -- recalled risk fields contradict the journal
        return kept
