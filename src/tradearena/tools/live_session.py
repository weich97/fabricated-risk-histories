"""Weekly human-gated live-readiness session orchestrator.

This module chains the existing broker control-plane building blocks
(``broker_export``, ``broker_capability``, ``operator_runbook``,
``live_readiness``) into one resumable weekly session:

    propose -> review -> approve | reject -> execute -> reconcile

Design constraints:

- Zero network and zero LLM calls. Market input is either a deterministic
  synthetic snapshot or an operator-supplied local prices file; execution
  engines are the dry-run adapter and a mock paper-sandbox client.
- Human approval is asynchronous. Every step persists state to disk and each
  CLI invocation reloads it, so the session can pause between propose and
  approve and survive interpreter restarts.
- Approvals are hash-bound to the reviewed broker handoff artifact. The
  execute step re-validates the approval binding and expiry against the
  on-disk handoff file and refuses to run when either check fails.
- The ``llm`` decision source is a declared interface only. Selecting it
  raises immediately without performing any provider call.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from tradearena.core.domain import Order, OrderType, Side
from tradearena.tools.approval_signing import sign_approval_artifact
from tradearena.tools.broker_capability import validate_broker_adapter_capability
from tradearena.tools.broker_export import (
    AlpacaPaperExportAdapter,
    AlpacaPaperOrder,
    BrokerAdapterContractError,
    BrokerAdapterMode,
    BrokerApproval,
    BrokerOrderStatus,
    BrokerResponse,
    BrokerSafetyConfig,
    DryRunBrokerAdapter,
    broker_handoff_artifact_hash,
    build_broker_approval_artifact,
    validate_broker_approval_artifact,
    validate_broker_approval_artifact_file,
    validate_broker_approval_request_binding,
    validate_broker_handoff_artifact_file,
    validate_broker_response_artifact_file,
    write_broker_response_artifact,
)
from tradearena.tools.live_readiness import validate_live_readiness_preflight_bundle_file
from tradearena.tools.operator_runbook import validate_operator_runbook_artifact

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SESSIONS_ROOT = ROOT / "outputs" / "live_sessions"
FORWARD_WINDOW_COMMITMENT_PATH = ROOT / "docs" / "results" / "forward_window_commitment_2026q3.json"

STATE_FILENAME = "session_state.json"
SNAPSHOT_FILENAME = "market_snapshot.json"
RISK_REPORT_FILENAME = "risk_gate_report.json"
CAPABILITY_FILENAME = "capability_manifest.json"
APPROVAL_FILENAME = "broker_approval_artifact.json"
REJECTION_FILENAME = "rejection_record.json"
RESPONSE_FILENAME = "broker_response_artifact.json"
RUNBOOK_FILENAME = "operator_runbook_artifact.json"
BUNDLE_FILENAME = "preflight_bundle.json"
PREFLIGHT_SUMMARY_FILENAME = "preflight_summary.json"
SESSION_SUMMARY_FILENAME = "session_summary.json"
JOURNAL_FILENAME = "journal.jsonl"

STATUS_PROPOSED = "proposed"
STATUS_NO_TRADE = "no_trade"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_EXECUTED = "executed"
STATUS_RECONCILED = "reconciled"

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ISO_TIMESTAMP_WITH_TZ_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_DEFAULT_SYMBOLS = ("BTC-USD", "BTC=F", "GSPC")
_JOURNAL_SCHEMA = "trellm_live_session_journal_entry_v0.1"
_STATE_SCHEMA = "trellm_live_session_state_v0.1"

SAFETY_NOTE = (
    "Weekly human-gated paper session. No broker API call is made, no credentials are read, "
    "and no live submission path exists in this session; all artifacts are local and redacted."
)


class LiveSessionError(ValueError):
    """Raised when a live session step cannot proceed safely."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LiveSessionConfig:
    """Inputs for one weekly human-gated session."""

    session_id: str
    root: Path = DEFAULT_SESSIONS_ROOT
    symbols: tuple[str, ...] = _DEFAULT_SYMBOLS
    decision_source: str = "rule-based"
    engine: str = "dry_run"
    seed: int = 7
    per_symbol_notional: float = 1000.0
    max_order_quantity: float = 100.0
    max_order_notional: float | None = None
    max_gross_notional: float | None = None
    time_in_force: str = "day"
    prices_file: Path | None = None


def default_session_symbols() -> tuple[str, ...]:
    """Return the pre-registered forward-window symbols when available."""

    try:
        payload = json.loads(FORWARD_WINDOW_COMMITMENT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _DEFAULT_SYMBOLS
    symbols = payload.get("symbols")
    if isinstance(symbols, list) and symbols and all(isinstance(item, str) and item.strip() for item in symbols):
        return tuple(symbols)
    return _DEFAULT_SYMBOLS


def _resolved_config(config: LiveSessionConfig) -> LiveSessionConfig:
    if not _SESSION_ID_RE.fullmatch(config.session_id):
        raise LiveSessionError(
            "session_id must match [A-Za-z0-9][A-Za-z0-9._-]* and stay at or below 64 characters"
        )
    if config.engine not in _ENGINE_SPECS:
        raise LiveSessionError(f"engine must be one of {', '.join(sorted(_ENGINE_SPECS))}")
    if config.decision_source not in _DECISION_SOURCES:
        raise LiveSessionError(f"decision_source must be one of {', '.join(sorted(_DECISION_SOURCES))}")
    symbols: list[str] = []
    for symbol in config.symbols:
        if not symbol or any(character.isspace() for character in symbol):
            raise LiveSessionError("symbols must be non-empty and must not contain whitespace")
        if symbol not in symbols:
            symbols.append(symbol)
    if not symbols:
        raise LiveSessionError("at least one symbol is required")
    if not (config.per_symbol_notional > 0 and config.max_order_quantity > 0):
        raise LiveSessionError("per_symbol_notional and max_order_quantity must be positive")
    max_order_notional = config.max_order_notional
    if max_order_notional is None:
        max_order_notional = round(config.per_symbol_notional * 1.1, 8)
    max_gross_notional = config.max_gross_notional
    if max_gross_notional is None:
        max_gross_notional = round(config.per_symbol_notional * len(symbols) * 1.1, 8)
    if not (max_order_notional > 0 and max_gross_notional > 0):
        raise LiveSessionError("max_order_notional and max_gross_notional must be positive")
    return LiveSessionConfig(
        session_id=config.session_id,
        root=Path(config.root).resolve(),
        symbols=tuple(symbols),
        decision_source=config.decision_source,
        engine=config.engine,
        seed=config.seed,
        per_symbol_notional=float(config.per_symbol_notional),
        max_order_quantity=float(config.max_order_quantity),
        max_order_notional=float(max_order_notional),
        max_gross_notional=float(max_gross_notional),
        time_in_force=config.time_in_force,
        prices_file=None if config.prices_file is None else Path(config.prices_file),
    )


# ---------------------------------------------------------------------------
# Market snapshot (deterministic, zero network)
# ---------------------------------------------------------------------------


def _synthetic_price_row(seed: int, as_of_date: str, symbol: str) -> dict[str, float]:
    digest = hashlib.sha256(f"{seed}:{as_of_date}:{symbol}".encode()).digest()
    base = 50.0 + (int.from_bytes(digest[0:4], "big") % 100_000) / 1000.0
    drift = ((int.from_bytes(digest[4:8], "big") % 2001) - 1000) / 10_000.0
    prev_close = round(base, 4)
    close = round(base * (1.0 + drift), 4)
    return {"close": close, "prev_close": prev_close}


def _load_prices_file(path: Path, symbols: tuple[str, ...]) -> dict[str, dict[str, float]]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LiveSessionError(f"prices file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise LiveSessionError(f"prices file must contain valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise LiveSessionError("prices file must be a JSON object keyed by symbol")
    prices: dict[str, dict[str, float]] = {}
    for symbol in symbols:
        row = payload.get(symbol)
        if isinstance(row, (int, float)) and not isinstance(row, bool):
            row = {"close": float(row), "prev_close": float(row)}
        if not isinstance(row, dict):
            raise LiveSessionError(f"prices file is missing symbol {symbol}")
        def _positive_number(value: Any, label: str, symbol: str = symbol) -> float:
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not value > 0:
                raise LiveSessionError(f"prices file {symbol}.{label} must be a positive number")
            return float(value)

        close = _positive_number(row.get("close"), "close")
        prev_close = _positive_number(row.get("prev_close", row.get("close")), "prev_close")
        prices[symbol] = {"close": close, "prev_close": prev_close}
    return prices


def _build_market_snapshot(config: LiveSessionConfig, now: str) -> dict[str, Any]:
    as_of_date = now[:10]
    if config.prices_file is not None:
        source = "operator-prices-file"
        prices = _load_prices_file(config.prices_file, config.symbols)
    else:
        source = "synthetic-deterministic"
        prices = {symbol: _synthetic_price_row(config.seed, as_of_date, symbol) for symbol in config.symbols}
    return {
        "schema": "trellm_live_session_market_snapshot_v0.1",
        "as_of": now,
        "source": source,
        "seed": config.seed,
        "prices": prices,
        "safety_note": "Deterministic local market snapshot; no market-data network call was made.",
    }


# ---------------------------------------------------------------------------
# Decision sources (rule-based default; llm is a declared interface only)
# ---------------------------------------------------------------------------


@runtime_checkable
class LiveSessionDecisionSource(Protocol):
    """Decision source surface for one weekly session proposal."""

    name: str

    def propose_orders(self, snapshot: dict[str, Any], config: LiveSessionConfig) -> list[Order]:
        """Turn a deterministic market snapshot into order intent."""


class RuleBasedDecisionSource:
    """Deterministic weekly momentum allocation rule.

    Allocates ``per_symbol_notional`` to each symbol whose close is at or
    above the previous close, and stays in cash for the rest.
    """

    name = "rule-based"

    def propose_orders(self, snapshot: dict[str, Any], config: LiveSessionConfig) -> list[Order]:
        orders: list[Order] = []
        prices = snapshot.get("prices", {})
        for symbol in config.symbols:
            row = prices.get(symbol, {})
            close = float(row.get("close", 0.0))
            prev_close = float(row.get("prev_close", 0.0))
            if close <= 0 or prev_close <= 0 or close < prev_close:
                continue
            quantity = round(config.per_symbol_notional / close, 8)
            if quantity <= 0:
                continue
            orders.append(
                Order(
                    symbol,
                    Side.BUY,
                    quantity,
                    order_type=OrderType.LIMIT,
                    limit_price=round(close, 8),
                    reason=(
                        f"rule-based weekly allocation: close {close} >= prev_close {prev_close}, "
                        f"target notional {config.per_symbol_notional}"
                    ),
                )
            )
        return orders


class LLMDecisionSourceStub:
    """Interface placeholder: no LLM call is wired into the session path."""

    name = "llm"

    def propose_orders(self, snapshot: dict[str, Any], config: LiveSessionConfig) -> list[Order]:
        raise LiveSessionError(
            "decision source 'llm' is a declared interface only in this build; no LLM provider call "
            "is wired into the live session path. Re-run with --decision-source rule-based."
        )


_DECISION_SOURCES: dict[str, type] = {
    RuleBasedDecisionSource.name: RuleBasedDecisionSource,
    LLMDecisionSourceStub.name: LLMDecisionSourceStub,
}


def decision_source_names() -> tuple[str, ...]:
    return tuple(sorted(_DECISION_SOURCES))


def _decision_source(name: str) -> LiveSessionDecisionSource:
    return _DECISION_SOURCES[name]()


# ---------------------------------------------------------------------------
# Execution engines (zero network)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _EngineSpec:
    key: str
    adapter_name: str
    adapter_mode: BrokerAdapterMode
    account_mode: str
    handoff_filename: str
    capability_adapter_kind: str


_ENGINE_SPECS: dict[str, _EngineSpec] = {
    "dry_run": _EngineSpec(
        key="dry_run",
        adapter_name="dry-run-broker-adapter",
        adapter_mode=BrokerAdapterMode.DRY_RUN,
        account_mode="paper",
        handoff_filename="dry_run_orders.json",
        capability_adapter_kind="dry_run",
    ),
    "paper_mock": _EngineSpec(
        key="paper_mock",
        adapter_name="alpaca-paper-export-adapter",
        adapter_mode=BrokerAdapterMode.PAPER_SANDBOX,
        account_mode="paper",
        handoff_filename="alpaca_paper_orders.json",
        capability_adapter_kind="paper_sandbox",
    ),
}


def engine_names() -> tuple[str, ...]:
    return tuple(sorted(_ENGINE_SPECS))


@runtime_checkable
class LiveSessionExecutionClient(Protocol):
    """Injected client surface used by the execute step (mirrors PaperSandboxClient)."""

    def submit_paper_orders(
        self, requests: Sequence[AlpacaPaperOrder]
    ) -> Sequence[BrokerResponse | Mapping[str, object]]:
        """Return one redacted response row per already-approved request."""


class DeterministicFillClient:
    """Deterministic zero-network client that fills every request at its limit price."""

    def __init__(self, *, now: str) -> None:
        self.now = now
        self.calls = 0

    def submit_paper_orders(self, requests: Sequence[AlpacaPaperOrder]) -> list[BrokerResponse]:
        self.calls += 1
        responses: list[BrokerResponse] = []
        for request in requests:
            if request.limit_price is None or float(request.limit_price) <= 0:
                raise LiveSessionError(
                    f"deterministic fill client requires a positive limit_price for {request.client_order_id}"
                )
            digest = hashlib.sha256(request.client_order_id.encode("utf-8")).hexdigest()[:16]
            responses.append(
                BrokerResponse(
                    client_order_id=request.client_order_id,
                    status=BrokerOrderStatus.FILLED,
                    broker_order_id=f"simfill-{digest}",
                    submitted_quantity=float(request.quantity),
                    accepted_quantity=float(request.quantity),
                    fill_quantity=float(request.quantity),
                    fill_price=float(request.limit_price),
                    fees=0.0,
                    submitted_at=self.now,
                    broker_timestamp=self.now,
                    account_mode=request.account_mode,
                )
            )
        return responses


def _coerce_response(row: BrokerResponse | Mapping[str, object]) -> BrokerResponse:
    if isinstance(row, BrokerResponse):
        return row
    payload = dict(row)
    status = payload.get("status")
    if isinstance(status, str):
        payload["status"] = BrokerOrderStatus(status)
    return BrokerResponse(**payload)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ensure_now(now: str | None) -> str:
    if now is None:
        return _utc_now_iso()
    if not isinstance(now, str) or not _ISO_TIMESTAMP_WITH_TZ_RE.fullmatch(now):
        raise LiveSessionError("--now must be an ISO timestamp with timezone, for example 2026-07-02T10:00:00Z")
    return now


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _seconds_between(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    return round((_parse_timestamp(end) - _parse_timestamp(start)).total_seconds(), 3)


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LiveSessionError(f"required session file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise LiveSessionError(f"session file must contain valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise LiveSessionError(f"session file must be a JSON object: {path}")
    return payload


def _session_dir(root: Path, session_id: str) -> Path:
    if not _SESSION_ID_RE.fullmatch(session_id):
        raise LiveSessionError(
            "session_id must match [A-Za-z0-9][A-Za-z0-9._-]* and stay at or below 64 characters"
        )
    return Path(root).resolve() / session_id


def _write_state(session_dir: Path, state: dict[str, Any]) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    target = session_dir / STATE_FILENAME
    temporary = session_dir / (STATE_FILENAME + ".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def _load_state(root: Path, session_id: str) -> tuple[Path, dict[str, Any]]:
    session_dir = _session_dir(root, session_id)
    state_path = session_dir / STATE_FILENAME
    if not state_path.exists():
        raise LiveSessionError(
            f"session {session_id} does not exist under {session_dir.parent}; run propose first"
        )
    return session_dir, _read_json_file(state_path)


def _portable_ref(target: Path, session_dir: Path) -> str:
    resolved = Path(target).resolve()
    try:
        rel = resolved.relative_to(ROOT).as_posix()
    except ValueError:
        rel = None
    if rel is not None and not any(character.isspace() for character in rel):
        return rel
    return resolved.name


def _handoff_rows(payload: dict[str, Any]) -> list[AlpacaPaperOrder]:
    rows: list[AlpacaPaperOrder] = []
    for row in payload.get("orders", []):
        if not isinstance(row, dict):
            continue
        limit_price = row.get("limit_price")
        max_notional = row.get("max_notional")
        rows.append(
            AlpacaPaperOrder(
                client_order_id=str(row["client_order_id"]),
                adapter_mode=str(row["adapter_mode"]),
                account_mode=str(row["account_mode"]),
                symbol=str(row["symbol"]),
                side=str(row["side"]),
                order_type=str(row["order_type"]),
                quantity=float(row["quantity"]),
                time_in_force=str(row["time_in_force"]),
                limit_price=None if limit_price is None else float(limit_price),
                submit_live=bool(row["submit_live"]),
                approval_status=str(row["approval_status"]),
                max_notional=None if max_notional is None else float(max_notional),
                reason=str(row["reason"]),
            )
        )
    return rows


def _require_clean_operator_text(label: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LiveSessionError(f"{label} must be non-empty")
    if "@" in value:
        raise LiveSessionError(f"{label} must not contain '@'; use a redacted operator id or plain text")
    return value.strip()


# ---------------------------------------------------------------------------
# Journal (append-only hash chain)
# ---------------------------------------------------------------------------


def _journal_path(root: Path) -> Path:
    return Path(root).resolve() / JOURNAL_FILENAME


def _entry_hash(entry: dict[str, Any]) -> str:
    canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _append_journal(root: Path, *, now: str, session_id: str, event: str, details: dict[str, Any]) -> dict[str, Any]:
    journal = _journal_path(root)
    journal.parent.mkdir(parents=True, exist_ok=True)
    prev_hash = None
    if journal.exists():
        lines = [line for line in journal.read_text(encoding="utf-8").splitlines() if line.strip()]
        if lines:
            try:
                prev_hash = json.loads(lines[-1]).get("entry_hash")
            except json.JSONDecodeError as exc:
                raise LiveSessionError(f"journal file is corrupted: {journal}") from exc
    entry: dict[str, Any] = {
        "schema": _JOURNAL_SCHEMA,
        "ts": now,
        "session_id": session_id,
        "event": event,
        "details": details,
        "prev_entry_hash": prev_hash,
    }
    entry["entry_hash"] = _entry_hash({key: value for key, value in entry.items() if key != "entry_hash"})
    with journal.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n")
    return entry


def verify_journal_chain(path: str | Path) -> list[str]:
    """Recompute the journal hash chain and return a list of chain errors."""

    journal = Path(path)
    if not journal.exists():
        return [f"journal file does not exist: {journal}"]
    errors: list[str] = []
    prev_hash: str | None = None
    for line_number, line in enumerate(journal.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"line {line_number}: invalid JSON")
            continue
        recorded_hash = entry.get("entry_hash")
        body = {key: value for key, value in entry.items() if key != "entry_hash"}
        if entry.get("prev_entry_hash") != prev_hash:
            errors.append(f"line {line_number}: prev_entry_hash does not match the previous entry")
        if recorded_hash != _entry_hash(body):
            errors.append(f"line {line_number}: entry_hash does not match entry content")
        prev_hash = recorded_hash
    return errors


# ---------------------------------------------------------------------------
# Risk gate
# ---------------------------------------------------------------------------


def _session_safety(config: LiveSessionConfig, mode: BrokerAdapterMode) -> BrokerSafetyConfig:
    return BrokerSafetyConfig(
        mode=mode,
        account_mode="paper",
        max_notional=config.max_order_notional,
        max_quantity=config.max_order_quantity,
        allowed_symbols=config.symbols,
        allowed_order_types=(OrderType.LIMIT,),
    )


def _apply_risk_gate(
    orders: list[Order], config: LiveSessionConfig, *, now: str
) -> tuple[list[Order], dict[str, Any]]:
    safety = _session_safety(config, BrokerAdapterMode.DRY_RUN)
    approved: list[Order] = []
    checks: list[dict[str, Any]] = []
    gross_notional = 0.0
    for order in orders:
        limit_price = order.limit_price
        notional = None if limit_price is None else round(float(order.quantity) * float(limit_price), 8)
        row: dict[str, Any] = {
            "symbol": order.symbol,
            "side": order.side.value,
            "quantity": order.quantity,
            "limit_price": limit_price,
            "notional": notional,
            "status": "approved",
            "block_reason": None,
        }
        if limit_price is None or notional is None:
            row["status"] = "blocked"
            row["block_reason"] = "session risk gate requires limit orders with a positive limit_price"
            checks.append(row)
            continue
        try:
            safety.validate_order(order, reference_price=limit_price)
        except BrokerAdapterContractError as exc:
            row["status"] = "blocked"
            row["block_reason"] = str(exc)
            checks.append(row)
            continue
        if gross_notional + notional > float(config.max_gross_notional or 0.0):
            row["status"] = "blocked"
            row["block_reason"] = (
                f"gross notional {round(gross_notional + notional, 8)} exceeds "
                f"max_gross_notional {config.max_gross_notional}"
            )
            checks.append(row)
            continue
        gross_notional = round(gross_notional + notional, 8)
        approved.append(order)
        checks.append(row)
    report = {
        "schema": "trellm_live_session_risk_gate_v0.1",
        "checked_at": now,
        "proposed_count": len(orders),
        "approved_count": len(approved),
        "blocked_count": len(orders) - len(approved),
        "gross_notional": gross_notional,
        "max_order_quantity": config.max_order_quantity,
        "max_order_notional": config.max_order_notional,
        "max_gross_notional": config.max_gross_notional,
        "allowed_symbols": list(config.symbols),
        "allowed_order_types": [OrderType.LIMIT.value],
        "checks": checks,
    }
    return approved, report


# ---------------------------------------------------------------------------
# Artifact builders
# ---------------------------------------------------------------------------


def _build_adapter(spec: _EngineSpec, config: LiveSessionConfig) -> DryRunBrokerAdapter | AlpacaPaperExportAdapter:
    client_prefix = f"ls-{config.session_id}"
    if spec.key == "dry_run":
        return DryRunBrokerAdapter(
            time_in_force=config.time_in_force,
            client_prefix=client_prefix,
            safety=_session_safety(config, BrokerAdapterMode.DRY_RUN),
        )
    return AlpacaPaperExportAdapter(
        time_in_force=config.time_in_force,
        client_prefix=client_prefix,
        safety=_session_safety(config, BrokerAdapterMode.PAPER_SANDBOX),
    )


def _capability_manifest(spec: _EngineSpec, config: LiveSessionConfig, session_dir: Path) -> dict[str, Any]:
    capability_ref = _portable_ref(session_dir / CAPABILITY_FILENAME, session_dir)
    return {
        "schema": "trellm_broker_adapter_capability_v0.1",
        "adapter_id": spec.adapter_name,
        "adapter_name": f"Live session {spec.key} adapter",
        "adapter_kind": spec.capability_adapter_kind,
        "default_mode": "dry_run",
        "supported_modes": ["offline_export", "dry_run", "paper_sandbox"],
        "account_modes": ["none", "paper"],
        "network_access": "none",
        "supports_live_submission": False,
        "live_submission_default": False,
        "requires_credentials": False,
        "credential_policy": {
            "no_credentials_in_repo": True,
            "redacted_artifacts_only": True,
            "env_vars": [],
        },
        "safety_controls": {
            "manual_approval_required": True,
            "approval_expiry_required": True,
            "request_hash_binding_required": True,
            "kill_switch_required": True,
            "reconciliation_required": True,
            "artifact_retention_required": True,
        },
        "supported_order_types": ["market", "limit"],
        "supported_time_in_force": [config.time_in_force],
        "verification_commands": [
            f"python -m tradearena.cli validate-broker-capability {capability_ref}",
        ],
        "safety_note": (
            "Session capability manifest for a paper-only weekly review path. It does not authorize "
            "live submission, read credentials, or call a broker API."
        ),
    }


def _operator_runbook_artifact(
    *, handoff_symbols: list[str], bundle_ref: str, checked_at: str
) -> dict[str, Any]:
    checklist = [
        {
            "id": "mode-boundary",
            "owner": "operator",
            "evidence": "session state and handoff artifact record a paper-only adapter mode",
            "pass_condition": "default path cannot submit live orders",
        },
        {
            "id": "approval-expiry",
            "owner": "operator",
            "evidence": "broker approval artifact with approved_at, expires_at, limits, and request hash",
            "pass_condition": "approval is unexpired and bound to the reviewed handoff artifact",
        },
        {
            "id": "kill-switch",
            "owner": "operator",
            "evidence": "kill switch flag checked by the broker safety config before every handoff",
            "pass_condition": "tripped kill switch blocks every broker-facing path",
        },
        {
            "id": "reconciliation",
            "owner": "reviewer",
            "evidence": "broker response artifact with status counts and missing/unmatched response counts",
            "pass_condition": "reconciliation summary validates against response rows",
        },
        {
            "id": "rollback",
            "owner": "operator",
            "evidence": "rollback owner, account mode, and affected symbols are named in this runbook",
            "pass_condition": "operator can disable submission before any retry",
        },
        {
            "id": "artifact-retention",
            "owner": "reviewer",
            "evidence": "session directory retains snapshot, handoff, approval, response, and journal entries",
            "pass_condition": "audit bundle can be preserved without raw credentials or private holdings",
        },
        {
            "id": "incident-owner",
            "owner": "incident-owner",
            "evidence": "one redacted incident owner is named for escalation and final signoff",
            "pass_condition": "ownership is explicit before the path is considered live-capable",
        },
    ]
    return {
        "schema": "trellm_operator_runbook_v0.1",
        "live_submission": False,
        "default_mode": "dry_run",
        "allowed_modes": ["offline_export", "dry_run", "paper_sandbox", "live_human_approved"],
        "manual_approval_required": True,
        "kill_switch_required": True,
        "approval_expiry_required": True,
        "artifact_retention_required": True,
        "incident_owner_required": True,
        "incident_response_drill": {
            "kill_switch_action": "set BrokerSafetyConfig.kill_switch=true and stop the weekly session",
            "rollback_owner": "operator",
            "affected_account_mode": "paper",
            "affected_symbols": handoff_symbols,
            "artifact_retention_path": "outputs/examples/operator_runbook/live_session_retention/",
            "reenable_approval_gate": "new approval artifact bound to a newly reviewed handoff hash",
        },
        "checklist": checklist,
        "verification_commands": [
            f"python -m tradearena.cli validate-live-readiness {bundle_ref} --now {checked_at}",
        ],
        "safety_note": (
            "Weekly session runbook. It reads no credentials, calls no broker API, and does not "
            "authorize live submission."
        ),
    }


# ---------------------------------------------------------------------------
# Session steps
# ---------------------------------------------------------------------------


def propose_session(config: LiveSessionConfig, *, now: str | None = None) -> dict[str, Any]:
    """Build the deterministic weekly proposal and its reviewed handoff artifact."""

    now = _ensure_now(now)
    config = _resolved_config(config)
    session_dir = config.root / config.session_id
    state_path = session_dir / STATE_FILENAME
    if state_path.exists():
        existing_state = _read_json_file(state_path)
        return {
            "session_id": config.session_id,
            "status": existing_state.get("status"),
            "already_exists": True,
            "session_dir": str(session_dir),
            "note": "session already exists; propose is idempotent and did not overwrite it",
        }

    spec = _ENGINE_SPECS[config.engine]
    source = _decision_source(config.decision_source)
    snapshot = _build_market_snapshot(config, now)
    orders = source.propose_orders(snapshot, config)
    approved, risk_report = _apply_risk_gate(orders, config, now=now)

    session_dir.mkdir(parents=True, exist_ok=True)
    _write_json_file(session_dir / SNAPSHOT_FILENAME, snapshot)
    _write_json_file(session_dir / RISK_REPORT_FILENAME, risk_report)

    state: dict[str, Any] = {
        "schema": _STATE_SCHEMA,
        "session_id": config.session_id,
        "status": STATUS_NO_TRADE,
        "engine": config.engine,
        "decision_source": config.decision_source,
        "adapter_name": spec.adapter_name,
        "adapter_mode": spec.adapter_mode.value,
        "account_mode": spec.account_mode,
        "handoff_filename": spec.handoff_filename,
        "symbols": list(config.symbols),
        "time_in_force": config.time_in_force,
        "seed": config.seed,
        "per_symbol_notional": config.per_symbol_notional,
        "max_order_quantity": config.max_order_quantity,
        "max_order_notional": config.max_order_notional,
        "max_gross_notional": config.max_gross_notional,
        "proposed_at": now,
        "order_count": len(approved),
        "blocked_count": risk_report["blocked_count"],
        "request_artifact_hash": None,
        "approved_at": None,
        "approval_expires_at": None,
        "approved_by": None,
        "rejected_at": None,
        "executed_at": None,
        "reconciled_at": None,
        "reconciled_checked_at": None,
    }

    if not approved:
        _write_state(session_dir, state)
        _append_journal(
            config.root,
            now=now,
            session_id=config.session_id,
            event="session_no_trade",
            details={"proposed_count": len(orders), "blocked_count": risk_report["blocked_count"]},
        )
        _write_session_summary(session_dir, state)
        return {
            "session_id": config.session_id,
            "status": STATUS_NO_TRADE,
            "order_count": 0,
            "blocked_count": risk_report["blocked_count"],
            "session_dir": str(session_dir),
            "note": "no orders passed the decision rule and risk gate this week; recorded as a no-trade session",
        }

    adapter = _build_adapter(spec, config)
    adapter.write(approved, session_dir)
    handoff_path = session_dir / spec.handoff_filename
    _, handoff_errors = validate_broker_handoff_artifact_file(handoff_path)
    if handoff_errors:
        raise LiveSessionError("generated handoff artifact failed validation: " + "; ".join(handoff_errors))
    request_hash = broker_handoff_artifact_hash(handoff_path)

    capability = _capability_manifest(spec, config, session_dir)
    capability_errors = validate_broker_adapter_capability(capability)
    if capability_errors:
        raise LiveSessionError("generated capability manifest failed validation: " + "; ".join(capability_errors))
    _write_json_file(session_dir / CAPABILITY_FILENAME, capability)

    state["status"] = STATUS_PROPOSED
    state["request_artifact_hash"] = request_hash
    _write_state(session_dir, state)
    _append_journal(
        config.root,
        now=now,
        session_id=config.session_id,
        event="session_proposed",
        details={
            "request_artifact_hash": request_hash,
            "order_count": len(approved),
            "blocked_count": risk_report["blocked_count"],
            "engine": config.engine,
            "decision_source": config.decision_source,
        },
    )
    return {
        "session_id": config.session_id,
        "status": STATUS_PROPOSED,
        "order_count": len(approved),
        "blocked_count": risk_report["blocked_count"],
        "request_artifact_hash": request_hash,
        "handoff_artifact": str(handoff_path),
        "session_dir": str(session_dir),
        "next_step": (
            "review the handoff, then approve or reject: "
            f"python -m tradearena.cli live-session review --session {config.session_id}"
        ),
    }


def review_session(session_id: str, *, root: Path | str = DEFAULT_SESSIONS_ROOT) -> str:
    """Render a human-readable review of the pending handoff artifact."""

    session_dir, state = _load_state(Path(root), session_id)
    lines = [
        "TreLLM live session review",
        f"  session={session_id} status={state.get('status')}",
        f"  engine={state.get('engine')} decision_source={state.get('decision_source')}",
        f"  proposed_at={state.get('proposed_at')}",
        f"  adapter={state.get('adapter_name')} mode={state.get('adapter_mode')} account={state.get('account_mode')}",
    ]
    risk_path = session_dir / RISK_REPORT_FILENAME
    if risk_path.exists():
        risk = _read_json_file(risk_path)
        lines.append(
            f"  risk_gate: proposed={risk.get('proposed_count')} approved={risk.get('approved_count')} "
            f"blocked={risk.get('blocked_count')} gross_notional={risk.get('gross_notional')}"
        )
        for check in risk.get("checks", []):
            if check.get("status") == "blocked":
                lines.append(f"    blocked {check.get('symbol')}: {check.get('block_reason')}")
    if state.get("status") == STATUS_NO_TRADE:
        lines.append("  no handoff artifact: this week is a recorded no-trade session")
        return "\n".join(lines)

    handoff_path = session_dir / str(state.get("handoff_filename"))
    handoff, handoff_errors = validate_broker_handoff_artifact_file(handoff_path)
    lines.append(f"  handoff_artifact={handoff_path.name} valid={not handoff_errors}")
    for error in handoff_errors:
        lines.append(f"    handoff error: {error}")
    if not handoff_errors:
        current_hash = broker_handoff_artifact_hash(handoff)
        recorded_hash = state.get("request_artifact_hash")
        lines.append(f"  request_artifact_hash={current_hash}")
        lines.append(f"  hash_matches_proposed_state={current_hash == recorded_hash}")
        orders_value = handoff.get("orders", [])
        orders: list[Any] = orders_value if isinstance(orders_value, list) else []
        total_notional = 0.0
        lines.append(f"  orders ({len(orders)}):")
        for idx, order in enumerate(orders, start=1):
            if not isinstance(order, dict):
                continue
            quantity = float(order.get("quantity", 0.0))
            limit_price = order.get("limit_price")
            notional = None if limit_price is None else round(quantity * float(limit_price), 2)
            if notional is not None:
                total_notional = round(total_notional + notional, 2)
            lines.append(
                f"    [{idx}] {order.get('side')} {order.get('symbol')} qty={quantity} "
                f"limit={limit_price} tif={order.get('time_in_force')} notional={notional} "
                f"id={order.get('client_order_id')}"
            )
        lines.append(f"  total_notional={total_notional}")
    lines.append(
        f"  approval_caps: max_order_notional={state.get('max_order_notional')} "
        f"max_order_quantity={state.get('max_order_quantity')}"
    )
    if state.get("status") == STATUS_PROPOSED:
        lines.append("  next:")
        lines.append(
            "    approve => python -m tradearena.cli live-session approve "
            f"--session {session_id} --approved-by <operator-id> --reason \"<why>\""
        )
        lines.append(
            "    reject  => python -m tradearena.cli live-session reject "
            f"--session {session_id} --rejected-by <operator-id> --reason \"<why>\""
        )
    return "\n".join(lines)


def approve_session(
    session_id: str,
    *,
    root: Path | str = DEFAULT_SESSIONS_ROOT,
    approved_by: str,
    reason: str,
    now: str | None = None,
    valid_minutes: int = 24 * 60,
    signing_key_path: Path | str | None = None,
) -> dict[str, Any]:
    """Issue a hash-bound, expiring human approval artifact for the reviewed handoff.

    Hash binding makes the artifact tamper-*evident* against accidental corruption
    and non-collusive edits. Passing ``signing_key_path`` additionally makes it
    tamper-*resistant*: an Ed25519 detached signature commits to the approver, the
    bound request hash, the expiry, and the notional limits, so an adversary who
    can rewrite the artifact store still cannot forge an approval without the
    approver's private key. Verify with
    :func:`tradearena.tools.approval_signing.verify_approval_signature` against a
    trusted public-key set held out of band.
    """

    now = _ensure_now(now)
    root_path = Path(root).resolve()
    session_dir, state = _load_state(root_path, session_id)
    status = state.get("status")
    if status == STATUS_APPROVED or status in {STATUS_EXECUTED, STATUS_RECONCILED}:
        return {
            "session_id": session_id,
            "status": status,
            "already_approved": True,
            "approval_artifact": str(session_dir / APPROVAL_FILENAME),
            "note": "session is already approved; approve is idempotent and did not reissue the artifact",
        }
    if status == STATUS_NO_TRADE:
        raise LiveSessionError("session is a recorded no-trade week; there is nothing to approve")
    if status == STATUS_REJECTED:
        raise LiveSessionError("session was rejected; start a new session instead of approving this one")
    if status != STATUS_PROPOSED:
        raise LiveSessionError(f"session status {status} cannot be approved")

    approved_by = _require_clean_operator_text("approved_by", approved_by)
    if any(character.isspace() for character in approved_by):
        raise LiveSessionError("approved_by must not contain whitespace")
    reason = _require_clean_operator_text("reason", reason)
    if valid_minutes < 1:
        raise LiveSessionError("valid_minutes must be at least 1")

    handoff_path = session_dir / str(state.get("handoff_filename"))
    _, handoff_errors = validate_broker_handoff_artifact_file(handoff_path)
    if handoff_errors:
        raise LiveSessionError("handoff artifact failed validation: " + "; ".join(handoff_errors))
    current_hash = broker_handoff_artifact_hash(handoff_path)
    if current_hash != state.get("request_artifact_hash"):
        raise LiveSessionError(
            "handoff artifact changed after propose; refusing to approve. "
            f"proposed={state.get('request_artifact_hash')} current={current_hash}"
        )

    expires_at = (
        (_parse_timestamp(now) + timedelta(minutes=valid_minutes)).isoformat().replace("+00:00", "Z")
    )
    approval = BrokerApproval(
        approval_status="approved",
        approved_by=approved_by,
        approved_at=now,
        max_notional=float(state["max_order_notional"]),
        allowed_symbols=tuple(state["symbols"]),
        approval_reason=reason,
    )
    artifact = build_broker_approval_artifact(
        approval,
        approval_id=f"{session_id}-approval-001",
        account_mode="live",
        max_quantity=float(state["max_order_quantity"]),
        allowed_order_types=(OrderType.LIMIT,),
        expires_at=expires_at,
        request_artifact_hash=current_hash,
    )
    errors = validate_broker_approval_artifact(artifact, now=now)
    errors.extend(validate_broker_approval_request_binding(artifact, handoff_path, now=now))
    if errors:
        raise LiveSessionError("approval artifact failed validation: " + "; ".join(sorted(set(errors))))

    if signing_key_path is not None:
        artifact = sign_approval_artifact(artifact, signing_key_path)

    approval_path = session_dir / APPROVAL_FILENAME
    _write_json_file(approval_path, artifact)
    state["status"] = STATUS_APPROVED
    state["approved_at"] = now
    state["approval_expires_at"] = expires_at
    state["approved_by"] = approved_by
    _write_state(session_dir, state)
    latency = _seconds_between(state.get("proposed_at"), now)
    _append_journal(
        root_path,
        now=now,
        session_id=session_id,
        event="session_approved",
        details={
            "approval_id": artifact["approval_id"],
            "request_artifact_hash": current_hash,
            "expires_at": expires_at,
            "approval_latency_seconds": latency,
        },
    )
    return {
        "session_id": session_id,
        "status": STATUS_APPROVED,
        "approval_id": artifact["approval_id"],
        "approval_artifact": str(approval_path),
        "expires_at": expires_at,
        "approval_latency_seconds": latency,
        "next_step": f"python -m tradearena.cli live-session execute --session {session_id}",
    }


def reject_session(
    session_id: str,
    *,
    root: Path | str = DEFAULT_SESSIONS_ROOT,
    rejected_by: str,
    reason: str,
    now: str | None = None,
) -> dict[str, Any]:
    """Record a human rejection of the pending handoff artifact."""

    now = _ensure_now(now)
    root_path = Path(root).resolve()
    session_dir, state = _load_state(root_path, session_id)
    status = state.get("status")
    if status == STATUS_REJECTED:
        return {
            "session_id": session_id,
            "status": STATUS_REJECTED,
            "already_rejected": True,
            "note": "session is already rejected; reject is idempotent",
        }
    if status != STATUS_PROPOSED:
        raise LiveSessionError(
            f"only proposed sessions can be rejected; session status is {status}"
        )
    rejected_by = _require_clean_operator_text("rejected_by", rejected_by)
    if any(character.isspace() for character in rejected_by):
        raise LiveSessionError("rejected_by must not contain whitespace")
    reason = _require_clean_operator_text("reason", reason)

    record = {
        "schema": "trellm_live_session_rejection_v0.1",
        "session_id": session_id,
        "rejected_by": rejected_by,
        "rejected_at": now,
        "reason": reason,
        "request_artifact_hash": state.get("request_artifact_hash"),
    }
    _write_json_file(session_dir / REJECTION_FILENAME, record)
    state["status"] = STATUS_REJECTED
    state["rejected_at"] = now
    _write_state(session_dir, state)
    latency = _seconds_between(state.get("proposed_at"), now)
    _append_journal(
        root_path,
        now=now,
        session_id=session_id,
        event="session_rejected",
        details={
            "request_artifact_hash": state.get("request_artifact_hash"),
            "approval_latency_seconds": latency,
        },
    )
    _write_session_summary(session_dir, state)
    return {
        "session_id": session_id,
        "status": STATUS_REJECTED,
        "rejection_record": str(session_dir / REJECTION_FILENAME),
        "approval_latency_seconds": latency,
    }


def execute_session(
    session_id: str,
    *,
    root: Path | str = DEFAULT_SESSIONS_ROOT,
    now: str | None = None,
    client: LiveSessionExecutionClient | None = None,
) -> dict[str, Any]:
    """Execute the approved handoff through the zero-network engine and write the response artifact."""

    now = _ensure_now(now)
    root_path = Path(root).resolve()
    session_dir, state = _load_state(root_path, session_id)
    status = state.get("status")
    if status in {STATUS_EXECUTED, STATUS_RECONCILED}:
        return {
            "session_id": session_id,
            "status": status,
            "already_executed": True,
            "response_artifact": str(session_dir / RESPONSE_FILENAME),
            "note": "session is already executed; execute is idempotent and did not resubmit",
        }
    if status == STATUS_NO_TRADE:
        raise LiveSessionError("session is a recorded no-trade week; there is nothing to execute")
    if status == STATUS_REJECTED:
        raise LiveSessionError("session was rejected; execution is refused")
    if status != STATUS_APPROVED:
        raise LiveSessionError(f"session status {status} is not approved; execution is refused")

    handoff_path = session_dir / str(state.get("handoff_filename"))
    approval_path = session_dir / APPROVAL_FILENAME
    approval_payload, approval_errors = validate_broker_approval_artifact_file(approval_path, now=now)
    if approval_errors:
        raise LiveSessionError(
            "approval artifact failed validation; execution is refused: " + "; ".join(approval_errors)
        )
    binding_errors = validate_broker_approval_request_binding(approval_payload, handoff_path, now=now)
    if binding_errors:
        raise LiveSessionError(
            "approval does not bind to the on-disk handoff artifact; execution is refused: "
            + "; ".join(binding_errors)
        )
    current_hash = broker_handoff_artifact_hash(handoff_path)
    if current_hash != state.get("request_artifact_hash"):
        raise LiveSessionError(
            "handoff artifact hash does not match the proposed session state; execution is refused"
        )

    handoff = _read_json_file(handoff_path)
    requests = _handoff_rows(handoff)
    execution_client = client if client is not None else DeterministicFillClient(now=now)
    raw_responses = execution_client.submit_paper_orders(requests)
    responses = [_coerce_response(row) for row in raw_responses]
    spec = _ENGINE_SPECS[str(state.get("engine"))]
    response_path = session_dir / RESPONSE_FILENAME
    write_broker_response_artifact(
        requests=requests,
        responses=responses,
        output=response_path,
        adapter=spec.adapter_name,
        adapter_mode=spec.adapter_mode,
        account_mode=spec.account_mode,
        request_artifact_hash=current_hash,
    )
    state["status"] = STATUS_EXECUTED
    state["executed_at"] = now
    _write_state(session_dir, state)
    _append_journal(
        root_path,
        now=now,
        session_id=session_id,
        event="session_executed",
        details={
            "request_artifact_hash": current_hash,
            "response_count": len(responses),
            "engine": state.get("engine"),
        },
    )
    return {
        "session_id": session_id,
        "status": STATUS_EXECUTED,
        "response_count": len(responses),
        "response_artifact": str(response_path),
        "next_step": f"python -m tradearena.cli live-session reconcile --session {session_id}",
    }


def reconcile_session(
    session_id: str,
    *,
    root: Path | str = DEFAULT_SESSIONS_ROOT,
    now: str | None = None,
) -> dict[str, Any]:
    """Reconcile, build the runbook and preflight bundle, and seal the session summary."""

    now = _ensure_now(now)
    root_path = Path(root).resolve()
    session_dir, state = _load_state(root_path, session_id)
    status = state.get("status")
    if status in {STATUS_NO_TRADE, STATUS_REJECTED}:
        return {
            "session_id": session_id,
            "status": status,
            "note": "session is terminal without execution; the session summary is already recorded",
            "session_summary": str(session_dir / SESSION_SUMMARY_FILENAME),
        }
    if status == STATUS_RECONCILED:
        checked_at = str(state.get("reconciled_checked_at"))
        summary, errors = validate_live_readiness_preflight_bundle_file(
            session_dir / BUNDLE_FILENAME, now=checked_at
        )
        return {
            "session_id": session_id,
            "status": STATUS_RECONCILED,
            "already_reconciled": True,
            "preflight_ready": bool(summary.get("ready")) and not errors,
            "preflight_error_count": len(errors),
            "session_summary": str(session_dir / SESSION_SUMMARY_FILENAME),
        }
    if status != STATUS_EXECUTED:
        raise LiveSessionError(f"session status {status} cannot be reconciled; execute the session first")

    handoff_path = session_dir / str(state.get("handoff_filename"))
    response_path = session_dir / RESPONSE_FILENAME
    response_payload, response_errors = validate_broker_response_artifact_file(response_path)
    if response_errors:
        raise LiveSessionError("response artifact failed validation: " + "; ".join(response_errors))
    reconciliation = response_payload.get("reconciliation")
    if not isinstance(reconciliation, dict):
        raise LiveSessionError("response artifact is missing its reconciliation summary")

    handoff = _read_json_file(handoff_path)
    handoff_symbols = sorted(
        {
            str(order.get("symbol"))
            for order in handoff.get("orders", [])
            if isinstance(order, dict) and str(order.get("symbol", "")).strip()
        }
    )
    bundle_path = session_dir / BUNDLE_FILENAME
    bundle_ref = _portable_ref(bundle_path, session_dir)
    runbook = _operator_runbook_artifact(
        handoff_symbols=handoff_symbols, bundle_ref=bundle_ref, checked_at=now
    )
    runbook_errors = validate_operator_runbook_artifact(runbook)
    if runbook_errors:
        raise LiveSessionError("generated operator runbook failed validation: " + "; ".join(runbook_errors))
    runbook_path = session_dir / RUNBOOK_FILENAME
    _write_json_file(runbook_path, runbook)

    bundle = {
        "schema": "trellm_live_readiness_preflight_v0.1",
        "capability_manifest": _portable_ref(session_dir / CAPABILITY_FILENAME, session_dir),
        "handoff_artifact": _portable_ref(handoff_path, session_dir),
        "approval_artifact": _portable_ref(session_dir / APPROVAL_FILENAME, session_dir),
        "response_artifact": _portable_ref(response_path, session_dir),
        "operator_runbook_artifact": _portable_ref(runbook_path, session_dir),
        "approval_checked_at": now,
        "safety_note": SAFETY_NOTE,
    }
    _write_json_file(bundle_path, bundle)
    preflight_summary, preflight_errors = validate_live_readiness_preflight_bundle_file(bundle_path, now=now)
    _write_json_file(session_dir / PREFLIGHT_SUMMARY_FILENAME, preflight_summary)
    if preflight_errors:
        _append_journal(
            root_path,
            now=now,
            session_id=session_id,
            event="session_reconcile_failed",
            details={"preflight_error_count": len(preflight_errors)},
        )
        raise LiveSessionError(
            "live-readiness preflight failed; session stays executed: " + "; ".join(preflight_errors)
        )

    state["status"] = STATUS_RECONCILED
    state["reconciled_at"] = now
    state["reconciled_checked_at"] = now
    _write_state(session_dir, state)
    summary = _write_session_summary(session_dir, state, reconciliation=reconciliation)
    _append_journal(
        root_path,
        now=now,
        session_id=session_id,
        event="session_reconciled",
        details={
            "preflight_ready": True,
            "response_count": reconciliation.get("response_count"),
            "missing_response_count": reconciliation.get("missing_response_count"),
            "unmatched_response_count": reconciliation.get("unmatched_response_count"),
        },
    )
    final_gate_command = runbook["verification_commands"][0]
    return {
        "session_id": session_id,
        "status": STATUS_RECONCILED,
        "preflight_ready": True,
        "preflight_error_count": 0,
        "preflight_bundle": str(bundle_path),
        "session_summary": str(session_dir / SESSION_SUMMARY_FILENAME),
        "approval_latency_seconds": summary.get("approval_latency_seconds"),
        "final_gate_command": final_gate_command,
    }


def _write_session_summary(
    session_dir: Path, state: dict[str, Any], *, reconciliation: dict[str, Any] | None = None
) -> dict[str, Any]:
    artifacts = {}
    for label, filename in (
        ("market_snapshot", SNAPSHOT_FILENAME),
        ("risk_gate_report", RISK_REPORT_FILENAME),
        ("capability_manifest", CAPABILITY_FILENAME),
        ("handoff_artifact", str(state.get("handoff_filename") or "")),
        ("approval_artifact", APPROVAL_FILENAME),
        ("rejection_record", REJECTION_FILENAME),
        ("response_artifact", RESPONSE_FILENAME),
        ("operator_runbook_artifact", RUNBOOK_FILENAME),
        ("preflight_bundle", BUNDLE_FILENAME),
        ("preflight_summary", PREFLIGHT_SUMMARY_FILENAME),
    ):
        if filename and (session_dir / filename).exists():
            artifacts[label] = _portable_ref(session_dir / filename, session_dir)
    summary: dict[str, Any] = {
        "schema": "trellm_live_session_summary_v0.1",
        "session_id": state.get("session_id"),
        "status": state.get("status"),
        "engine": state.get("engine"),
        "decision_source": state.get("decision_source"),
        "symbols": state.get("symbols"),
        "order_count": state.get("order_count"),
        "blocked_count": state.get("blocked_count"),
        "request_artifact_hash": state.get("request_artifact_hash"),
        "proposed_at": state.get("proposed_at"),
        "approved_at": state.get("approved_at"),
        "approval_expires_at": state.get("approval_expires_at"),
        "rejected_at": state.get("rejected_at"),
        "executed_at": state.get("executed_at"),
        "reconciled_at": state.get("reconciled_at"),
        "approval_latency_seconds": _seconds_between(
            state.get("proposed_at"), state.get("approved_at") or state.get("rejected_at")
        ),
        "reconciliation": reconciliation,
        "artifacts": artifacts,
        "safety_note": SAFETY_NOTE,
    }
    if FORWARD_WINDOW_COMMITMENT_PATH.exists():
        try:
            window = json.loads(FORWARD_WINDOW_COMMITMENT_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            window = {}
        if isinstance(window, dict) and window:
            summary["forward_window"] = {
                "declaration_hash": window.get("declaration_hash"),
                "window_start": window.get("window_start"),
                "window_end": window.get("window_end"),
                "frequency": window.get("frequency"),
                "symbols": window.get("symbols"),
            }
    _write_json_file(session_dir / SESSION_SUMMARY_FILENAME, summary)
    return summary


def session_status(
    session_id: str | None = None, *, root: Path | str = DEFAULT_SESSIONS_ROOT
) -> dict[str, Any]:
    """Return the on-disk state of one session, or a listing of all sessions under root."""

    root_path = Path(root).resolve()
    if session_id is not None:
        _, state = _load_state(root_path, session_id)
        return state
    sessions = []
    if root_path.exists():
        for state_path in sorted(root_path.glob(f"*/{STATE_FILENAME}")):
            state = _read_json_file(state_path)
            sessions.append(
                {
                    "session_id": state.get("session_id"),
                    "status": state.get("status"),
                    "proposed_at": state.get("proposed_at"),
                    "order_count": state.get("order_count"),
                }
            )
    return {"root": str(root_path), "session_count": len(sessions), "sessions": sessions}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _add_session_arguments(parser: argparse.ArgumentParser, *, session_required: bool = True) -> None:
    parser.add_argument("--session", required=session_required, help="Session id, for example 2026w27.")
    parser.add_argument(
        "--root",
        default=str(DEFAULT_SESSIONS_ROOT),
        help="Sessions root directory (state, artifacts, and journal live here).",
    )


def _add_now_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--now",
        default=None,
        help="Optional ISO timestamp with timezone; defaults to the current UTC time.",
    )


def build_live_session_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tradearena live-session",
        description=(
            "Weekly human-gated live-readiness session: propose -> review -> approve|reject -> "
            "execute -> reconcile. Zero network, zero LLM calls."
        ),
    )
    subparsers = parser.add_subparsers(dest="step", required=True)

    propose = subparsers.add_parser("propose", help="Build the weekly proposal and reviewed handoff artifact.")
    _add_session_arguments(propose)
    _add_now_argument(propose)
    propose.add_argument(
        "--symbols",
        default=",".join(default_session_symbols()),
        help="Comma-separated symbols; defaults to the pre-registered forward-window symbols.",
    )
    propose.add_argument(
        "--decision-source",
        default="rule-based",
        choices=decision_source_names(),
        help="Decision source; 'llm' is a declared interface only and refuses to run.",
    )
    propose.add_argument(
        "--engine",
        default="dry_run",
        choices=engine_names(),
        help="Zero-network execution engine for this session.",
    )
    propose.add_argument("--seed", type=int, default=7, help="Seed for the synthetic market snapshot.")
    propose.add_argument(
        "--prices-file",
        default="",
        help="Optional local JSON prices file: {symbol: {close, prev_close}} or {symbol: close}.",
    )
    propose.add_argument("--per-symbol-notional", type=float, default=1000.0)
    propose.add_argument("--max-order-quantity", type=float, default=100.0)
    propose.add_argument("--max-order-notional", type=float, default=None)
    propose.add_argument("--max-gross-notional", type=float, default=None)
    propose.add_argument("--time-in-force", default="day", choices=["cls", "day", "fok", "gtc", "ioc", "opg"])

    review = subparsers.add_parser("review", help="Print a human-readable review of the pending handoff.")
    _add_session_arguments(review)

    approve = subparsers.add_parser("approve", help="Issue a hash-bound, expiring human approval artifact.")
    _add_session_arguments(approve)
    _add_now_argument(approve)
    approve.add_argument("--approved-by", required=True, help="Redacted operator id (no whitespace, no '@').")
    approve.add_argument("--reason", required=True, help="Why this handoff is approved.")
    approve.add_argument(
        "--valid-for-minutes",
        type=int,
        default=24 * 60,
        help="Approval validity window in minutes (default 1440 = 24h).",
    )

    reject = subparsers.add_parser("reject", help="Record a human rejection of the pending handoff.")
    _add_session_arguments(reject)
    _add_now_argument(reject)
    reject.add_argument("--rejected-by", required=True, help="Redacted operator id (no whitespace, no '@').")
    reject.add_argument("--reason", required=True, help="Why this handoff is rejected.")

    execute = subparsers.add_parser("execute", help="Execute the approved handoff through the zero-network engine.")
    _add_session_arguments(execute)
    _add_now_argument(execute)

    reconcile = subparsers.add_parser(
        "reconcile", help="Reconcile responses, build the preflight bundle, and seal the session summary."
    )
    _add_session_arguments(reconcile)
    _add_now_argument(reconcile)

    status = subparsers.add_parser("status", help="Show one session state or list all sessions under root.")
    _add_session_arguments(status, session_required=False)

    return parser


def _print_result(title: str, result: dict[str, Any]) -> None:
    print(title)
    for key, value in result.items():
        if isinstance(value, (dict, list)):
            print(f"  {key}={json.dumps(value, sort_keys=True)}")
        else:
            print(f"  {key}={value}")


def run_live_session_cli(argv: list[str] | None = None) -> int:
    parser = build_live_session_parser()
    args = parser.parse_args(argv)
    try:
        if args.step == "propose":
            symbols = tuple(symbol.strip() for symbol in str(args.symbols).split(",") if symbol.strip())
            config = LiveSessionConfig(
                session_id=args.session,
                root=Path(args.root),
                symbols=symbols,
                decision_source=args.decision_source,
                engine=args.engine,
                seed=args.seed,
                per_symbol_notional=args.per_symbol_notional,
                max_order_quantity=args.max_order_quantity,
                max_order_notional=args.max_order_notional,
                max_gross_notional=args.max_gross_notional,
                time_in_force=args.time_in_force,
                prices_file=Path(args.prices_file) if args.prices_file else None,
            )
            _print_result("Live session propose", propose_session(config, now=args.now))
            return 0
        if args.step == "review":
            print(review_session(args.session, root=Path(args.root)))
            return 0
        if args.step == "approve":
            result = approve_session(
                args.session,
                root=Path(args.root),
                approved_by=args.approved_by,
                reason=args.reason,
                now=args.now,
                valid_minutes=args.valid_for_minutes,
            )
            _print_result("Live session approve", result)
            return 0
        if args.step == "reject":
            result = reject_session(
                args.session,
                root=Path(args.root),
                rejected_by=args.rejected_by,
                reason=args.reason,
                now=args.now,
            )
            _print_result("Live session reject", result)
            return 0
        if args.step == "execute":
            _print_result("Live session execute", execute_session(args.session, root=Path(args.root), now=args.now))
            return 0
        if args.step == "reconcile":
            _print_result(
                "Live session reconcile", reconcile_session(args.session, root=Path(args.root), now=args.now)
            )
            return 0
        if args.step == "status":
            _print_result("Live session status", session_status(args.session, root=Path(args.root)))
            return 0
    except (LiveSessionError, BrokerAdapterContractError) as exc:
        print(f"live-session error: {exc}")
        return 1
    raise AssertionError(f"Unhandled live-session step: {args.step}")
