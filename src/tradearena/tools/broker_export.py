from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from statistics import fmean
from typing import Protocol, TypeGuard, cast, runtime_checkable

from tradearena.core.domain import Order, OrderType, Side

_SHA256_ARTIFACT_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ISO_TIMESTAMP_WITH_TZ_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_SUPPORTED_ACCOUNT_MODES = ("none", "paper", "live")
_SUPPORTED_TIME_IN_FORCE = ("cls", "day", "fok", "gtc", "ioc", "opg")


class BrokerAdapterMode(str, Enum):
    """Runtime broker adapter mode."""

    OFFLINE_EXPORT = "offline_export"
    DRY_RUN = "dry_run"
    PAPER_SANDBOX = "paper_sandbox"
    LIVE_HUMAN_APPROVED = "live_human_approved"


class BrokerAdapterContractError(ValueError):
    """Raised when a broker handoff would violate the adapter contract."""


@runtime_checkable
class BrokerAdapter(Protocol):
    """Minimal broker adapter surface for export, dry-run, sandbox, or live modes."""

    name: str
    safety: BrokerSafetyConfig

    def convert(self, orders: list[Order] | tuple[Order, ...]) -> list[AlpacaPaperOrder]:
        """Convert TreLLM order intent into broker handoff rows."""

    def write(self, orders: list[Order] | tuple[Order, ...], output_dir: str | Path) -> dict[str, str | int | bool]:
        """Write a broker handoff artifact and return a small summary."""


class BrokerOrderStatus(str, Enum):
    """Normalized broker response status."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BrokerApproval:
    """Human approval record required before live broker submission."""

    approval_status: str
    approved_by: str
    approved_at: str
    max_notional: float
    allowed_symbols: tuple[str, ...]
    approval_reason: str

    @property
    def is_approved(self) -> bool:
        return (
            self.approval_status == "approved"
            and _has_text(self.approved_by)
            and _has_text(self.approved_at)
            and _has_text(self.approval_reason)
        )


@dataclass(frozen=True)
class BrokerSafetyConfig:
    """Safety limits enforced before any broker handoff artifact is written."""

    mode: BrokerAdapterMode = BrokerAdapterMode.OFFLINE_EXPORT
    account_mode: str = "none"
    max_notional: float | None = None
    max_quantity: float | None = None
    allowed_symbols: tuple[str, ...] = field(default_factory=tuple)
    allowed_order_types: tuple[OrderType, ...] = field(default_factory=lambda: (OrderType.MARKET, OrderType.LIMIT))
    approved_order_fingerprints: tuple[str, ...] = field(default_factory=tuple)
    approved_order_execution_fingerprints: tuple[str, ...] = field(default_factory=tuple)
    kill_switch: bool = False
    approval: BrokerApproval | None = None

    def validate_order(self, order: Order, *, reference_price: float | None = None) -> None:
        """Validate one order before export, dry run, sandbox, or live handoff."""

        if self.max_quantity is not None and not _is_positive_finite_number(self.max_quantity):
            raise BrokerAdapterContractError("max_quantity must be a positive finite number")
        if self.max_notional is not None and not _is_positive_finite_number(self.max_notional):
            raise BrokerAdapterContractError("max_notional must be a positive finite number")
        quantity = float(order.quantity)
        if not math.isfinite(quantity) or quantity <= 0:
            raise BrokerAdapterContractError("order quantity must be a positive finite number")
        if self.kill_switch:
            raise BrokerAdapterContractError("broker adapter kill switch is enabled")
        if self.allowed_symbols and order.symbol not in self.allowed_symbols:
            raise BrokerAdapterContractError(f"symbol {order.symbol} is not in the broker adapter allow-list")
        if order.order_type not in self.allowed_order_types:
            raise BrokerAdapterContractError(f"order type {order.order_type.value} is not allowed")
        if self.max_quantity is not None and quantity > self.max_quantity:
            raise BrokerAdapterContractError(f"quantity {order.quantity} exceeds max_quantity {self.max_quantity}")
        if self.mode == BrokerAdapterMode.LIVE_HUMAN_APPROVED:
            self._validate_live_approval(order)

        notional = None
        if self.max_notional is not None:
            if reference_price is None:
                raise BrokerAdapterContractError("max_notional checks require a reference_price")
            if not _is_positive_finite_number(reference_price):
                raise BrokerAdapterContractError("max_notional checks require a positive finite reference_price")
            notional = abs(quantity * float(reference_price))
        if self.max_notional is not None and notional is not None:
            if notional > self.max_notional:
                raise BrokerAdapterContractError(
                    f"order notional {notional:.2f} exceeds max_notional {self.max_notional:.2f}"
                )
        if self.mode == BrokerAdapterMode.LIVE_HUMAN_APPROVED:
            if notional is not None and self.approval is not None and notional > self.approval.max_notional:
                raise BrokerAdapterContractError(
                    f"order notional {notional:.2f} exceeds approval max_notional {self.approval.max_notional:.2f}"
                )

    def validate_handoff_config(self) -> None:
        """Validate mode-level broker safety before writing a handoff artifact."""

        if self.account_mode not in _SUPPORTED_ACCOUNT_MODES:
            raise BrokerAdapterContractError("account_mode must be one of none, paper, live")
        if self.mode != BrokerAdapterMode.LIVE_HUMAN_APPROVED:
            if self.account_mode == "live":
                raise BrokerAdapterContractError("non-live handoff artifacts must not use account_mode live")
            return
        if self.kill_switch:
            raise BrokerAdapterContractError("broker adapter kill switch is enabled")
        if self.max_notional is None or self.max_quantity is None:
            raise BrokerAdapterContractError("live_human_approved mode requires max_notional and max_quantity limits")
        if not _is_positive_finite_number(self.max_notional):
            raise BrokerAdapterContractError("max_notional must be a positive finite number")
        if not _is_positive_finite_number(self.max_quantity):
            raise BrokerAdapterContractError("max_quantity must be a positive finite number")
        if not self.allowed_order_types or any(not isinstance(order_type, OrderType) for order_type in self.allowed_order_types):
            raise BrokerAdapterContractError("allowed_order_types must contain market or limit")
        if len(self.allowed_order_types) != len(set(self.allowed_order_types)):
            raise BrokerAdapterContractError("allowed_order_types must not contain duplicates")
        if self.approval is None:
            raise BrokerAdapterContractError("live_human_approved mode requires an approved human approval record")
        if (
            self.approval.approval_status != "approved"
            or not _has_text(self.approval.approved_by)
            or not _has_text(self.approval.approval_reason)
        ):
            raise BrokerAdapterContractError("live_human_approved mode requires an approved human approval record")
        if "@" in self.approval.approved_by:
            raise BrokerAdapterContractError("approved_by must be a redacted operator id, not an email address")
        if self.account_mode != "live":
            raise BrokerAdapterContractError("live_human_approved mode requires account_mode live")
        if not isinstance(self.approval.approved_at, str) or (
            _has_text(self.approval.approved_at) and not _is_iso_timestamp_with_timezone(self.approval.approved_at)
        ):
            raise BrokerAdapterContractError("approval approved_at must be an ISO timestamp with timezone")
        if not _has_text(self.approval.approved_at):
            raise BrokerAdapterContractError("live_human_approved mode requires an approved human approval record")
        if not _is_positive_finite_number(self.approval.max_notional):
            raise BrokerAdapterContractError("live_human_approved mode requires a positive finite approval max_notional")
        if not self.approval.allowed_symbols or not all(_has_text(symbol) for symbol in self.approval.allowed_symbols):
            raise BrokerAdapterContractError("approval allowed_symbols must be a non-empty list of symbols")
        if any(_has_whitespace(symbol) for symbol in self.approval.allowed_symbols):
            raise BrokerAdapterContractError("approval allowed_symbols must not contain whitespace")
        if len(self.approval.allowed_symbols) != len(set(self.approval.allowed_symbols)):
            raise BrokerAdapterContractError("approval allowed_symbols must not contain duplicates")

    def submit_live_flag(self) -> bool:
        return self.mode == BrokerAdapterMode.LIVE_HUMAN_APPROVED

    def _validate_live_approval(self, order: Order) -> None:
        self.validate_handoff_config()
        approval = cast(BrokerApproval, self.approval)
        if self.approved_order_fingerprints and _order_fingerprint_from_order(order) not in set(
            self.approved_order_fingerprints
        ):
            raise BrokerAdapterContractError("order does not match an approved broker handoff order")
        if approval.allowed_symbols and order.symbol not in approval.allowed_symbols:
            raise BrokerAdapterContractError(f"symbol {order.symbol} is outside the human approval scope")


@dataclass(frozen=True)
class AlpacaPaperOrder:
    """Neutral export-only order row for Alpaca review workflows."""

    client_order_id: str
    adapter_mode: str
    account_mode: str
    symbol: str
    side: str
    order_type: str
    quantity: float
    time_in_force: str
    limit_price: float | None
    submit_live: bool
    approval_status: str
    max_notional: float | None
    reason: str


@dataclass(frozen=True)
class BrokerResponse:
    """Redacted broker response row used for reconciliation artifacts."""

    client_order_id: str
    status: BrokerOrderStatus
    broker_order_id: str | None = None
    submitted_quantity: float | None = None
    accepted_quantity: float | None = None
    fill_quantity: float | None = None
    fill_price: float | None = None
    fees: float | None = None
    rejection_reason: str | None = None
    submitted_at: str | None = None
    broker_timestamp: str | None = None
    account_mode: str = "unknown"


@dataclass(frozen=True)
class BrokerReconciliationSummary:
    """Aggregate reconciliation status for broker response artifacts."""

    response_count: int
    accepted_count: int
    rejected_count: int
    partial_fill_count: int
    filled_count: int
    canceled_count: int
    expired_count: int
    unknown_count: int
    unmatched_response_count: int
    missing_response_count: int
    fill_ratio_mean: float | None


class AlpacaPaperExportAdapter:
    """Convert approved TreLLM orders into broker-review files.

    The adapter deliberately does not call Alpaca or any broker API. It creates
    a neutral JSON/CSV handoff for human review before any external system sees
    the order instructions.
    """

    name = "alpaca-paper-export-adapter"

    def __init__(
        self,
        *,
        time_in_force: str = "day",
        client_prefix: str = "ta-paper",
        safety: BrokerSafetyConfig | None = None,
    ) -> None:
        self.time_in_force = time_in_force
        self.client_prefix = client_prefix
        self.safety = safety or BrokerSafetyConfig()

    def convert(self, orders: list[Order] | tuple[Order, ...]) -> list[AlpacaPaperOrder]:
        rows: list[AlpacaPaperOrder] = []
        _validate_time_in_force(self.time_in_force)
        _validate_client_prefix(self.client_prefix)
        self.safety.validate_handoff_config()
        eligible_orders = [order for order in orders if order.side != Side.HOLD and order.quantity > 0]
        _validate_approved_order_counts(self.safety, eligible_orders, time_in_force=self.time_in_force)
        for idx, order in enumerate(eligible_orders, start=1):
            self.safety.validate_order(order, reference_price=order.limit_price)
            rows.append(
                AlpacaPaperOrder(
                    client_order_id=f"{self.client_prefix}-{idx:04d}-{_safe_symbol(order.symbol)}",
                    adapter_mode=self.safety.mode.value,
                    account_mode=self.safety.account_mode,
                    symbol=order.symbol,
                    side=order.side.value,
                    order_type=_alpaca_order_type(order.order_type),
                    quantity=round(float(order.quantity), 8),
                    time_in_force=self.time_in_force,
                    limit_price=order.limit_price,
                    submit_live=self.safety.submit_live_flag(),
                    approval_status=_approval_status(self.safety),
                    max_notional=self.safety.max_notional,
                    reason=order.reason,
                )
            )
        return rows

    def write(self, orders: list[Order] | tuple[Order, ...], output_dir: str | Path) -> dict[str, str | int | bool]:
        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)
        rows = self.convert(orders)
        _validate_adapter_name(self.name)
        json_path = path / "alpaca_paper_orders.json"
        csv_path = path / "alpaca_paper_orders.csv"
        payload = {
            "schema": "tradearena_broker_handoff_artifact_v0.1",
            "adapter": self.name,
            "adapter_mode": self.safety.mode.value,
            "account_mode": self.safety.account_mode,
            "paper_only": self.safety.mode in (BrokerAdapterMode.OFFLINE_EXPORT, BrokerAdapterMode.DRY_RUN),
            "live_submission": self.safety.submit_live_flag(),
            "manual_approval_required": self.safety.mode != BrokerAdapterMode.LIVE_HUMAN_APPROVED,
            "kill_switch": self.safety.kill_switch,
            "orders": [asdict(row) for row in rows],
        }
        json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            fieldnames = list(asdict(rows[0]).keys()) if rows else list(AlpacaPaperOrder.__dataclass_fields__)
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(asdict(row) for row in rows)
        return {
            "json": str(json_path),
            "csv": str(csv_path),
            "order_count": len(rows),
            "adapter_mode": self.safety.mode.value,
            "account_mode": self.safety.account_mode,
            "paper_only": self.safety.mode in (BrokerAdapterMode.OFFLINE_EXPORT, BrokerAdapterMode.DRY_RUN),
            "manual_approval_required": self.safety.mode != BrokerAdapterMode.LIVE_HUMAN_APPROVED,
        }


class DryRunBrokerAdapter:
    """Validate broker handoff rows locally without submitting to any broker API."""

    name = "dry-run-broker-adapter"

    def __init__(
        self,
        *,
        time_in_force: str = "day",
        client_prefix: str = "ta-dry-run",
        safety: BrokerSafetyConfig | None = None,
    ) -> None:
        self.time_in_force = time_in_force
        self.client_prefix = client_prefix
        self.safety = _dry_run_safety(safety)

    def convert(self, orders: list[Order] | tuple[Order, ...]) -> list[AlpacaPaperOrder]:
        rows: list[AlpacaPaperOrder] = []
        _validate_time_in_force(self.time_in_force)
        _validate_client_prefix(self.client_prefix)
        self.safety.validate_handoff_config()
        for idx, order in enumerate(orders, start=1):
            if order.side == Side.HOLD or order.quantity <= 0:
                continue
            self.safety.validate_order(order, reference_price=order.limit_price)
            rows.append(
                AlpacaPaperOrder(
                    client_order_id=f"{self.client_prefix}-{idx:04d}-{_safe_symbol(order.symbol)}",
                    adapter_mode=self.safety.mode.value,
                    account_mode=self.safety.account_mode,
                    symbol=order.symbol,
                    side=order.side.value,
                    order_type=_alpaca_order_type(order.order_type),
                    quantity=round(float(order.quantity), 8),
                    time_in_force=self.time_in_force,
                    limit_price=order.limit_price,
                    submit_live=False,
                    approval_status="requires_human_approval",
                    max_notional=self.safety.max_notional,
                    reason=order.reason,
                )
            )
        return rows

    def write(self, orders: list[Order] | tuple[Order, ...], output_dir: str | Path) -> dict[str, str | int | bool]:
        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)
        rows = self.convert(orders)
        _validate_adapter_name(self.name)
        json_path = path / "dry_run_orders.json"
        csv_path = path / "dry_run_orders.csv"
        payload = {
            "schema": "tradearena_broker_handoff_artifact_v0.1",
            "adapter": self.name,
            "adapter_mode": self.safety.mode.value,
            "account_mode": self.safety.account_mode,
            "paper_only": True,
            "live_submission": False,
            "manual_approval_required": True,
            "kill_switch": self.safety.kill_switch,
            "orders": [asdict(row) for row in rows],
        }
        json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            fieldnames = list(asdict(rows[0]).keys()) if rows else list(AlpacaPaperOrder.__dataclass_fields__)
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(asdict(row) for row in rows)
        return {
            "json": str(json_path),
            "csv": str(csv_path),
            "order_count": len(rows),
            "adapter_mode": self.safety.mode.value,
            "account_mode": self.safety.account_mode,
            "paper_only": True,
            "manual_approval_required": True,
        }


def reconcile_broker_responses(
    requests: list[AlpacaPaperOrder] | tuple[AlpacaPaperOrder, ...],
    responses: list[BrokerResponse] | tuple[BrokerResponse, ...],
) -> BrokerReconciliationSummary:
    request_ids = {request.client_order_id for request in requests}
    response_ids = {response.client_order_id for response in responses}
    status_counts = dict.fromkeys(BrokerOrderStatus, 0)
    fill_ratios: list[float] = []
    for response in responses:
        status_counts[response.status] += 1
        if response.submitted_quantity and response.fill_quantity is not None and response.submitted_quantity > 0:
            fill_ratios.append(float(response.fill_quantity) / float(response.submitted_quantity))
    return BrokerReconciliationSummary(
        response_count=len(responses),
        accepted_count=status_counts[BrokerOrderStatus.ACCEPTED],
        rejected_count=status_counts[BrokerOrderStatus.REJECTED],
        partial_fill_count=status_counts[BrokerOrderStatus.PARTIALLY_FILLED],
        filled_count=status_counts[BrokerOrderStatus.FILLED],
        canceled_count=status_counts[BrokerOrderStatus.CANCELED],
        expired_count=status_counts[BrokerOrderStatus.EXPIRED],
        unknown_count=status_counts[BrokerOrderStatus.UNKNOWN],
        unmatched_response_count=len(response_ids - request_ids),
        missing_response_count=len(request_ids - response_ids),
        fill_ratio_mean=round(fmean(fill_ratios), 8) if fill_ratios else None,
    )


def write_broker_response_artifact(
    *,
    requests: list[AlpacaPaperOrder] | tuple[AlpacaPaperOrder, ...],
    responses: list[BrokerResponse] | tuple[BrokerResponse, ...],
    output: str | Path,
    adapter: str,
    adapter_mode: BrokerAdapterMode,
    account_mode: str,
    request_artifact_hash: str | None = None,
) -> dict[str, str | int | bool]:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    artifact_errors: list[str] = []
    if not _has_text(adapter):
        artifact_errors.append("adapter must be non-empty")
    if _has_whitespace(adapter):
        artifact_errors.append("adapter must not contain whitespace")
    if not _has_text(account_mode):
        artifact_errors.append("account_mode must be non-empty")
    if request_artifact_hash is not None and (
        not isinstance(request_artifact_hash, str) or not _SHA256_ARTIFACT_HASH_RE.fullmatch(request_artifact_hash)
    ):
        artifact_errors.append("request_artifact_hash must be sha256:<64 lowercase hex chars>")
    binding_errors = _validate_response_request_bindings(
        requests,
        responses,
        adapter_mode=adapter_mode,
        account_mode=account_mode,
    )
    if artifact_errors:
        raise BrokerAdapterContractError("; ".join([*artifact_errors, *binding_errors]))
    if binding_errors:
        raise BrokerAdapterContractError("; ".join(binding_errors))
    summary = reconcile_broker_responses(requests, responses)
    live_submission = adapter_mode == BrokerAdapterMode.LIVE_HUMAN_APPROVED
    written_at = _utc_now_iso()
    payload: dict[str, object] = {
        "schema": "tradearena_broker_response_artifact_v0.1",
        "adapter": adapter,
        "adapter_mode": adapter_mode.value,
        "account_mode": account_mode,
        "live_submission": live_submission,
        "reconciliation": asdict(summary),
        "responses": [_response_dict(response, default_timestamp=written_at) for response in responses],
    }
    if request_artifact_hash is not None:
        payload["request_artifact_hash"] = request_artifact_hash
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "path": str(path),
        "response_count": summary.response_count,
        "unmatched_response_count": summary.unmatched_response_count,
        "missing_response_count": summary.missing_response_count,
        "live_submission": live_submission,
    }


def build_broker_approval_artifact(
    approval: BrokerApproval,
    *,
    approval_id: str,
    account_mode: str,
    max_quantity: float,
    request_artifact_hash: str,
    expires_at: str,
    allowed_order_types: tuple[OrderType, ...] = (OrderType.MARKET, OrderType.LIMIT),
) -> dict[str, object]:
    """Build a redacted human approval artifact for future live handoff review."""

    if approval.approval_status != "approved":
        raise BrokerAdapterContractError("approval_status must be approved")
    if not _has_text(approval.approved_by):
        raise BrokerAdapterContractError("approved_by must be non-empty")
    if _has_whitespace(approval.approved_by):
        raise BrokerAdapterContractError("approved_by must not contain whitespace")
    if "@" in approval.approved_by:
        raise BrokerAdapterContractError("approved_by must be a redacted operator id, not an email address")
    if not approval.approved_at or not _is_iso_timestamp_with_timezone(approval.approved_at):
        raise BrokerAdapterContractError("approved_at must be an ISO timestamp with timezone")
    if not _is_positive_finite_number(approval.max_notional):
        raise BrokerAdapterContractError("max_notional must be a positive number")
    if not _is_positive_finite_number(max_quantity):
        raise BrokerAdapterContractError("max_quantity must be a positive number")
    if not _has_text(approval.approval_reason):
        raise BrokerAdapterContractError("approval_reason must be non-empty")
    if not _has_text(approval_id):
        raise BrokerAdapterContractError("approval_id must be non-empty")
    if _has_whitespace(approval_id):
        raise BrokerAdapterContractError("approval_id must not contain whitespace")
    if account_mode != "live":
        raise BrokerAdapterContractError("account_mode must be live for broker approval artifacts")
    if not approval.allowed_symbols or not all(_has_text(symbol) for symbol in approval.allowed_symbols):
        raise BrokerAdapterContractError("allowed_symbols must be a non-empty list of symbols")
    if any(_has_whitespace(symbol) for symbol in approval.allowed_symbols):
        raise BrokerAdapterContractError("allowed_symbols must not contain whitespace")
    if len(approval.allowed_symbols) != len(set(approval.allowed_symbols)):
        raise BrokerAdapterContractError("allowed_symbols must not contain duplicates")
    if not allowed_order_types or any(not isinstance(order_type, OrderType) for order_type in allowed_order_types):
        raise BrokerAdapterContractError("allowed_order_types must contain market or limit")
    order_type_values = [order_type.value for order_type in allowed_order_types]
    if len(order_type_values) != len(set(order_type_values)):
        raise BrokerAdapterContractError("allowed_order_types must not contain duplicates")
    if not expires_at:
        raise BrokerAdapterContractError("expires_at is required for broker approval artifacts")
    if not isinstance(expires_at, str) or not _is_iso_timestamp_with_timezone(expires_at):
        raise BrokerAdapterContractError("expires_at must be an ISO timestamp with timezone")
    if _is_iso_timestamp_with_timezone(approval.approved_at):
        approved_dt = _parse_timestamp(approval.approved_at)
        expires_dt = _parse_timestamp(expires_at)
        if approved_dt is not None and expires_dt is not None and expires_dt <= approved_dt:
            raise BrokerAdapterContractError("expires_at must be after approved_at")
    if not request_artifact_hash:
        raise BrokerAdapterContractError(
            "request_artifact_hash is required to bind approval to a broker handoff artifact"
        )
    if not isinstance(request_artifact_hash, str) or not _SHA256_ARTIFACT_HASH_RE.fullmatch(request_artifact_hash):
        raise BrokerAdapterContractError("request_artifact_hash must be sha256:<64 lowercase hex chars>")

    return {
        "schema": "tradearena_broker_approval_artifact_v0.1",
        "approval_id": approval_id,
        "approval_status": approval.approval_status,
        "approved_by": approval.approved_by,
        "approved_at": approval.approved_at,
        "expires_at": expires_at,
        "account_mode": account_mode,
        "max_notional": approval.max_notional,
        "max_quantity": max_quantity,
        "allowed_symbols": list(approval.allowed_symbols),
        "allowed_order_types": order_type_values,
        "approval_reason": approval.approval_reason,
        "request_artifact_hash": request_artifact_hash,
    }


def validate_broker_approval_artifact(
    payload: dict[str, object],
    *,
    now: str | datetime | None = None,
) -> list[str]:
    errors: list[str] = []
    now_dt = None
    if now is not None:
        if not _is_timestamp_with_timezone(now):
            errors.append("now must be an ISO timestamp with timezone")
        else:
            now_dt = _parse_timestamp(now)
    required = {
        "schema",
        "approval_id",
        "approval_status",
        "approved_by",
        "approved_at",
        "expires_at",
        "account_mode",
        "max_notional",
        "max_quantity",
        "allowed_symbols",
        "allowed_order_types",
        "approval_reason",
        "request_artifact_hash",
    }
    # The artifact is closed-world: an undeclared field is a rejection. The
    # detached approval signature is a declared *optional* member, so that a
    # signed approval still passes the gate that consumes it.
    optional = {"approval_signature"}
    missing = sorted(required - set(payload))
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")
    extra = sorted(set(payload) - required - optional)
    if extra:
        errors.append(f"unexpected fields: {', '.join(extra)}")
    if payload.get("schema") != "tradearena_broker_approval_artifact_v0.1":
        errors.append("schema must be 'tradearena_broker_approval_artifact_v0.1'")
    if not _has_text(payload.get("approval_id")):
        errors.append("approval_id must be non-empty")
    if _has_whitespace(payload.get("approval_id")):
        errors.append("approval_id must not contain whitespace")
    if payload.get("approval_status") != "approved":
        errors.append("approval_status must be approved")
    approved_by = payload.get("approved_by")
    if not _has_text(approved_by):
        errors.append("approved_by must be non-empty")
    elif _has_whitespace(approved_by):
        errors.append("approved_by must not contain whitespace")
    elif "@" in approved_by:
        errors.append("approved_by must be a redacted operator id, not an email address")
    approved_at = payload.get("approved_at")
    approved_dt: datetime | None = None
    if not approved_at:
        errors.append("approved_at must be non-empty")
    elif not isinstance(approved_at, str) or not _is_iso_timestamp_with_timezone(approved_at):
        errors.append("approved_at must be an ISO timestamp with timezone")
    else:
        approved_dt = _parse_timestamp(approved_at)
    if payload.get("account_mode") != "live":
        errors.append("account_mode must be live for broker approval artifacts")
    for field_name in ("max_notional", "max_quantity"):
        value = payload.get(field_name)
        if not _is_positive_finite_number(value):
            errors.append(f"{field_name} must be a positive number")
    allowed_symbols = payload.get("allowed_symbols")
    if (
        not isinstance(allowed_symbols, list)
        or not allowed_symbols
        or not all(_has_text(item) for item in allowed_symbols)
    ):
        errors.append("allowed_symbols must be a non-empty list of symbols")
    elif any(_has_whitespace(item) for item in allowed_symbols):
        errors.append("allowed_symbols must not contain whitespace")
    elif len(allowed_symbols) != len(set(allowed_symbols)):
        errors.append("allowed_symbols must not contain duplicates")
    allowed_order_types = payload.get("allowed_order_types")
    supported_types = {OrderType.MARKET.value, OrderType.LIMIT.value}
    if (
        not isinstance(allowed_order_types, list)
        or not allowed_order_types
        or any(item not in supported_types for item in allowed_order_types)
    ):
        errors.append("allowed_order_types must contain market or limit")
    elif len(allowed_order_types) != len(set(allowed_order_types)):
        errors.append("allowed_order_types must not contain duplicates")
    if not _has_text(payload.get("approval_reason")):
        errors.append("approval_reason must be non-empty")
    request_hash = payload.get("request_artifact_hash")
    if not isinstance(request_hash, str) or not request_hash:
        errors.append("request_artifact_hash is required to bind approval to a broker handoff artifact")
    elif not _SHA256_ARTIFACT_HASH_RE.fullmatch(request_hash):
        errors.append("request_artifact_hash must be sha256:<64 lowercase hex chars>")
    expires_at = payload.get("expires_at")
    if not isinstance(expires_at, str) or not expires_at:
        errors.append("expires_at is required for broker approval artifacts")
    elif not _is_iso_timestamp_with_timezone(expires_at):
        errors.append("expires_at must be an ISO timestamp with timezone")
    else:
        expires_dt = _parse_timestamp(expires_at)
        if approved_dt is not None and expires_dt is not None and expires_dt <= approved_dt:
            errors.append("expires_at must be after approved_at")
        if now is not None and now_dt is not None:
            if expires_dt is None:
                errors.append("expires_at must be an ISO timestamp")
            elif expires_dt <= now_dt:
                errors.append("approval artifact is expired")
    return errors


def validate_broker_approval_artifact_file(
    path: str | Path,
    *,
    now: str | datetime | None = None,
) -> tuple[dict[str, object], list[str]]:
    payload, errors = _read_broker_artifact_json_file(path)
    if errors:
        return {}, errors
    if not isinstance(payload, dict):
        return {}, ["broker approval artifact must be a JSON object"]
    return payload, validate_broker_approval_artifact(payload, now=now)


def broker_handoff_artifact_hash(payload_or_path: dict[str, object] | str | Path) -> str:
    """Return a stable SHA-256 hash for a valid broker handoff artifact."""

    payload = _load_broker_artifact_payload(payload_or_path)
    errors = validate_broker_handoff_artifact(payload)
    if errors:
        raise BrokerAdapterContractError("; ".join(errors))
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_broker_approval_request_binding(
    approval_payload: dict[str, object],
    request_payload_or_path: dict[str, object] | str | Path,
    *,
    now: str | datetime | None = None,
) -> list[str]:
    """Validate that an approval artifact names the exact broker handoff artifact."""

    errors = validate_broker_approval_artifact(approval_payload, now=now)
    try:
        request_payload = _load_broker_artifact_payload(request_payload_or_path)
    except BrokerAdapterContractError as exc:
        return [*errors, str(exc)]
    request_errors = validate_broker_handoff_artifact(request_payload)
    errors.extend(request_errors)
    if (
        not request_errors
        and (
            request_payload.get("adapter_mode") == BrokerAdapterMode.LIVE_HUMAN_APPROVED.value
            or request_payload.get("live_submission") is True
            or request_payload.get("manual_approval_required") is not True
        )
    ):
        errors.append("request artifact must be a pre-live broker-review handoff, not live_human_approved")
    if not request_errors and not request_payload.get("orders"):
        errors.append("request artifact must contain at least one reviewed order")
    request_hash = approval_payload.get("request_artifact_hash")
    if not isinstance(request_hash, str) or not request_hash:
        errors.append("request_artifact_hash is required to bind approval to a broker handoff artifact")
    elif not request_errors and request_hash != broker_handoff_artifact_hash(request_payload):
        errors.append("request_artifact_hash does not match broker handoff artifact")
    if not errors:
        errors.extend(_validate_approval_request_scope(approval_payload, request_payload))
    return errors


def broker_approval_from_artifact(
    payload: dict[str, object],
    *,
    now: str | datetime | None = None,
    request_artifact: dict[str, object] | str | Path | None = None,
) -> BrokerApproval:
    """Convert a schema-valid broker approval artifact into a BrokerApproval."""

    errors = validate_broker_approval_artifact(payload, now=now)
    if request_artifact is not None:
        errors.extend(validate_broker_approval_request_binding(payload, request_artifact))
    if errors:
        raise BrokerAdapterContractError("; ".join(errors))
    return BrokerApproval(
        approval_status=str(payload["approval_status"]),
        approved_by=str(payload["approved_by"]),
        approved_at=str(payload["approved_at"]),
        max_notional=float(cast(str | int | float, payload["max_notional"])),
        allowed_symbols=tuple(
            str(symbol) for symbol in cast(list[object] | tuple[object, ...], payload["allowed_symbols"])
        ),
        approval_reason=str(payload["approval_reason"]),
    )


def broker_safety_from_approval_artifact(
    payload: dict[str, object],
    *,
    now: str | datetime | None = None,
    request_artifact: dict[str, object] | str | Path | None = None,
) -> BrokerSafetyConfig:
    """Build live human-approved safety limits from a broker approval artifact."""

    errors = validate_broker_approval_artifact(payload, now=now)
    if errors:
        raise BrokerAdapterContractError("; ".join(errors))
    if request_artifact is None:
        raise BrokerAdapterContractError(
            "request_artifact is required to build live safety from a broker approval artifact"
        )
    approval = broker_approval_from_artifact(payload, now=now, request_artifact=request_artifact)
    order_types = tuple(
        OrderType(str(order_type))
        for order_type in cast(list[object] | tuple[object, ...], payload["allowed_order_types"])
    )
    approved_order_fingerprints = (
        _approved_order_fingerprints_from_request(request_artifact) if request_artifact is not None else ()
    )
    approved_order_execution_fingerprints = (
        _approved_order_execution_fingerprints_from_request(request_artifact) if request_artifact is not None else ()
    )
    return BrokerSafetyConfig(
        mode=BrokerAdapterMode.LIVE_HUMAN_APPROVED,
        account_mode=str(payload["account_mode"]),
        max_notional=float(cast(str | int | float, payload["max_notional"])),
        max_quantity=float(cast(str | int | float, payload["max_quantity"])),
        allowed_symbols=tuple(approval.allowed_symbols),
        allowed_order_types=order_types,
        approved_order_fingerprints=approved_order_fingerprints,
        approved_order_execution_fingerprints=approved_order_execution_fingerprints,
        approval=approval,
    )


def validate_broker_response_artifact(payload: dict[str, object]) -> list[str]:
    errors: list[str] = []
    required = {
        "schema",
        "adapter",
        "adapter_mode",
        "account_mode",
        "live_submission",
        "reconciliation",
        "responses",
    }
    optional = {"request_artifact_hash"}
    missing = sorted(required - set(payload))
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")
    extra = sorted(set(payload) - required - optional)
    if extra:
        errors.append(f"unexpected fields: {', '.join(extra)}")
    request_hash = payload.get("request_artifact_hash")
    if request_hash is not None and (
        not isinstance(request_hash, str) or not _SHA256_ARTIFACT_HASH_RE.fullmatch(request_hash)
    ):
        errors.append("request_artifact_hash must be sha256:<64 lowercase hex chars>")
    if payload.get("schema") != "tradearena_broker_response_artifact_v0.1":
        errors.append("schema must be 'tradearena_broker_response_artifact_v0.1'")
    if not _has_text(payload.get("adapter")):
        errors.append("adapter must be non-empty")
    if _has_whitespace(payload.get("adapter")):
        errors.append("adapter must not contain whitespace")
    adapter_mode = payload.get("adapter_mode")
    if adapter_mode not in {mode.value for mode in BrokerAdapterMode}:
        errors.append("adapter_mode must be one of offline_export, dry_run, paper_sandbox, live_human_approved")
    if not _has_text(payload.get("account_mode")):
        errors.append("account_mode must be non-empty")
    elif payload.get("account_mode") not in _SUPPORTED_ACCOUNT_MODES:
        errors.append("account_mode must be one of none, paper, live")
    if payload.get("live_submission") is not (adapter_mode == BrokerAdapterMode.LIVE_HUMAN_APPROVED.value):
        errors.append("live_submission must match adapter_mode == live_human_approved")
    if adapter_mode == BrokerAdapterMode.LIVE_HUMAN_APPROVED.value and payload.get("account_mode") != "live":
        errors.append("account_mode must be live for live_human_approved broker response artifacts")
    if adapter_mode != BrokerAdapterMode.LIVE_HUMAN_APPROVED.value and payload.get("account_mode") == "live":
        errors.append("non-live response artifacts must not use account_mode live")

    responses = payload.get("responses")
    if not isinstance(responses, list):
        errors.append("responses must be a list")
        responses = []
    reconciliation = payload.get("reconciliation")
    if not isinstance(reconciliation, dict):
        errors.append("reconciliation must be an object")
        reconciliation = {}

    status_counts = {status.value: 0 for status in BrokerOrderStatus}
    seen_response_ids: set[str] = set()
    seen_broker_order_ids: set[str] = set()
    fill_ratios: list[float] = []
    for idx, response in enumerate(responses):
        if not isinstance(response, dict):
            errors.append(f"responses[{idx}] must be an object")
            continue
        response_errors = _validate_broker_response_row(response, idx)
        errors.extend(response_errors)
        if response.get("account_mode") != payload.get("account_mode"):
            errors.append(f"responses[{idx}].account_mode must match artifact account_mode")
        client_order_id = response.get("client_order_id")
        if isinstance(client_order_id, str) and client_order_id:
            if client_order_id in seen_response_ids:
                errors.append(f"responses[{idx}].client_order_id duplicates an earlier response")
            seen_response_ids.add(client_order_id)
        broker_order_id = response.get("broker_order_id")
        if isinstance(broker_order_id, str) and broker_order_id:
            if broker_order_id in seen_broker_order_ids:
                errors.append(f"responses[{idx}].broker_order_id duplicates an earlier response")
            seen_broker_order_ids.add(broker_order_id)
        status = response.get("status")
        if isinstance(status, str) and status in status_counts:
            status_counts[status] += 1
        submitted_quantity = response.get("submitted_quantity")
        fill_quantity = response.get("fill_quantity")
        if _is_positive_finite_number(submitted_quantity) and _is_non_negative_finite_number(fill_quantity):
            fill_ratios.append(float(fill_quantity) / float(submitted_quantity))

    expected_fill_ratio = round(fmean(fill_ratios), 8) if fill_ratios else None
    errors.extend(_validate_reconciliation(reconciliation, len(responses), status_counts, expected_fill_ratio))
    return errors


def validate_broker_response_artifact_file(path: str | Path) -> tuple[dict[str, object], list[str]]:
    payload, errors = _read_broker_artifact_json_file(path)
    if errors:
        return {}, errors
    if not isinstance(payload, dict):
        return {}, ["broker response artifact must be a JSON object"]
    return payload, validate_broker_response_artifact(payload)


def validate_broker_handoff_artifact(payload: dict[str, object]) -> list[str]:
    errors: list[str] = []
    required = {
        "schema",
        "adapter",
        "adapter_mode",
        "account_mode",
        "paper_only",
        "live_submission",
        "manual_approval_required",
        "kill_switch",
        "orders",
    }
    missing = sorted(required - set(payload))
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")
    extra = sorted(set(payload) - required)
    if extra:
        errors.append(f"unexpected fields: {', '.join(extra)}")
    if payload.get("schema") != "tradearena_broker_handoff_artifact_v0.1":
        errors.append("schema must be 'tradearena_broker_handoff_artifact_v0.1'")
    if not _has_text(payload.get("adapter")):
        errors.append("adapter must be non-empty")
    if _has_whitespace(payload.get("adapter")):
        errors.append("adapter must not contain whitespace")
    adapter_mode = payload.get("adapter_mode")
    if adapter_mode not in {mode.value for mode in BrokerAdapterMode}:
        errors.append("adapter_mode must be one of offline_export, dry_run, paper_sandbox, live_human_approved")
    if not payload.get("account_mode"):
        errors.append("account_mode must be non-empty")
    elif payload.get("account_mode") not in _SUPPORTED_ACCOUNT_MODES:
        errors.append("account_mode must be one of none, paper, live")
    if adapter_mode == BrokerAdapterMode.LIVE_HUMAN_APPROVED.value and payload.get("account_mode") != "live":
        errors.append("account_mode must be live for live_human_approved broker handoff artifacts")
    if adapter_mode != BrokerAdapterMode.LIVE_HUMAN_APPROVED.value and payload.get("account_mode") == "live":
        errors.append("non-live handoff artifacts must not use account_mode live")
    if adapter_mode == BrokerAdapterMode.LIVE_HUMAN_APPROVED.value and payload.get("kill_switch") is True:
        errors.append("live_human_approved handoff artifacts must not set kill_switch")
    paper_only_modes = {BrokerAdapterMode.OFFLINE_EXPORT.value, BrokerAdapterMode.DRY_RUN.value}
    if payload.get("paper_only") is not (adapter_mode in paper_only_modes):
        errors.append("paper_only must match adapter_mode in offline_export or dry_run")
    if payload.get("live_submission") is not (adapter_mode == BrokerAdapterMode.LIVE_HUMAN_APPROVED.value):
        errors.append("live_submission must match adapter_mode == live_human_approved")
    if payload.get("manual_approval_required") is not (adapter_mode != BrokerAdapterMode.LIVE_HUMAN_APPROVED.value):
        errors.append("manual_approval_required must be false only for live_human_approved mode")
    if not isinstance(payload.get("kill_switch"), bool):
        errors.append("kill_switch must be boolean")

    orders = payload.get("orders")
    if not isinstance(orders, list):
        errors.append("orders must be a list")
        orders = []
    seen_client_order_ids: set[str] = set()
    for idx, order in enumerate(orders):
        if not isinstance(order, dict):
            errors.append(f"orders[{idx}] must be an object")
            continue
        errors.extend(_validate_broker_handoff_order(order, idx, adapter_mode, payload.get("account_mode")))
        client_order_id = order.get("client_order_id")
        if isinstance(client_order_id, str) and client_order_id:
            if client_order_id in seen_client_order_ids:
                errors.append(f"orders[{idx}].client_order_id duplicates an earlier order")
            seen_client_order_ids.add(client_order_id)
    return errors


def validate_broker_handoff_artifact_file(path: str | Path) -> tuple[dict[str, object], list[str]]:
    payload, errors = _read_broker_artifact_json_file(path)
    if errors:
        return {}, errors
    if not isinstance(payload, dict):
        return {}, ["broker handoff artifact must be a JSON object"]
    return payload, validate_broker_handoff_artifact(payload)


def _read_broker_artifact_json_file(path: str | Path) -> tuple[object, list[str]]:
    artifact_path = Path(path)
    if artifact_path.exists() and not artifact_path.is_file():
        return {}, [f"broker artifact path is not a file: {artifact_path}"]
    try:
        return json.loads(artifact_path.read_text(encoding="utf-8")), []
    except FileNotFoundError:
        return {}, [f"broker artifact file does not exist: {artifact_path}"]
    except json.JSONDecodeError:
        return {}, ["broker artifact file must contain valid JSON"]


def _load_broker_artifact_payload(payload_or_path: dict[str, object] | str | Path) -> dict[str, object]:
    if isinstance(payload_or_path, dict):
        return payload_or_path
    payload, errors = _read_broker_artifact_json_file(payload_or_path)
    if errors:
        raise BrokerAdapterContractError("; ".join(errors))
    if not isinstance(payload, dict):
        raise BrokerAdapterContractError("broker artifact must be a JSON object")
    return payload


def _validate_approval_request_scope(
    approval_payload: dict[str, object],
    request_payload: dict[str, object],
) -> list[str]:
    errors: list[str] = []
    allowed_symbols = {
        str(symbol) for symbol in cast(list[object] | tuple[object, ...], approval_payload["allowed_symbols"])
    }
    allowed_order_types = {
        str(order_type) for order_type in cast(list[object] | tuple[object, ...], approval_payload["allowed_order_types"])
    }
    max_quantity = float(cast(str | int | float, approval_payload["max_quantity"]))
    max_notional = float(cast(str | int | float, approval_payload["max_notional"]))
    orders = cast(list[object] | tuple[object, ...], request_payload["orders"])
    for idx, order in enumerate(orders):
        if not isinstance(order, dict):
            continue
        symbol = str(order.get("symbol"))
        order_type = str(order.get("order_type"))
        quantity = float(order.get("quantity", 0.0))
        if symbol not in allowed_symbols:
            errors.append(f"orders[{idx}].symbol {symbol} is outside approval allowed_symbols")
        if order_type not in allowed_order_types:
            errors.append(f"orders[{idx}].order_type {order_type} is outside approval allowed_order_types")
        if quantity > max_quantity:
            errors.append(f"orders[{idx}].quantity {quantity} exceeds approval max_quantity {max_quantity}")
        limit_price = order.get("limit_price")
        if not _is_positive_finite_number(limit_price):
            errors.append(f"orders[{idx}].notional cannot be checked without a positive limit_price")
            continue
        notional = abs(quantity * float(limit_price))
        if notional > max_notional:
            errors.append(f"orders[{idx}].notional {notional:.2f} exceeds approval max_notional {max_notional:.2f}")
    return errors


def _approved_order_fingerprints_from_request(payload_or_path: dict[str, object] | str | Path) -> tuple[str, ...]:
    request_payload = _load_broker_artifact_payload(payload_or_path)
    errors = validate_broker_handoff_artifact(request_payload)
    if errors:
        raise BrokerAdapterContractError("; ".join(errors))
    orders = cast(list[object] | tuple[object, ...], request_payload["orders"])
    fingerprints = []
    for order in orders:
        if isinstance(order, dict):
            fingerprints.append(_order_fingerprint_from_handoff(order))
    return tuple(fingerprints)


def _approved_order_execution_fingerprints_from_request(payload_or_path: dict[str, object] | str | Path) -> tuple[str, ...]:
    request_payload = _load_broker_artifact_payload(payload_or_path)
    errors = validate_broker_handoff_artifact(request_payload)
    if errors:
        raise BrokerAdapterContractError("; ".join(errors))
    orders = cast(list[object] | tuple[object, ...], request_payload["orders"])
    fingerprints = []
    for order in orders:
        if isinstance(order, dict):
            fingerprints.append(_order_execution_fingerprint_from_handoff(order))
    return tuple(fingerprints)


def _validate_response_request_bindings(
    requests: list[AlpacaPaperOrder] | tuple[AlpacaPaperOrder, ...],
    responses: list[BrokerResponse] | tuple[BrokerResponse, ...],
    *,
    adapter_mode: BrokerAdapterMode,
    account_mode: str,
) -> list[str]:
    errors: list[str] = []
    request_quantities: dict[str, float] = {}
    seen_request_ids: set[str] = set()
    for idx, request in enumerate(requests):
        if request.client_order_id in seen_request_ids:
            errors.append(f"requests[{idx}].client_order_id duplicates an earlier request")
        seen_request_ids.add(request.client_order_id)
        request_quantities[request.client_order_id] = float(request.quantity)
    seen_response_ids: set[str] = set()
    seen_broker_order_ids: set[str] = set()
    if account_mode not in _SUPPORTED_ACCOUNT_MODES:
        errors.append("account_mode must be one of none, paper, live")
    if adapter_mode == BrokerAdapterMode.LIVE_HUMAN_APPROVED and account_mode != "live":
        errors.append("live_human_approved response artifacts require account_mode live")
    if adapter_mode != BrokerAdapterMode.LIVE_HUMAN_APPROVED and account_mode == "live":
        errors.append("non-live response artifacts must not use account_mode live")
    for idx, response in enumerate(responses):
        submitted_dt = None
        broker_dt = None
        for field_name in ("submitted_at", "broker_timestamp"):
            value = getattr(response, field_name)
            if value is not None:
                if not _is_iso_timestamp_with_timezone(value):
                    errors.append(f"responses[{idx}].{field_name} must be an ISO timestamp with timezone")
                elif field_name == "submitted_at":
                    submitted_dt = _parse_timestamp(value)
                else:
                    broker_dt = _parse_timestamp(value)
        if submitted_dt is not None and broker_dt is not None and broker_dt < submitted_dt:
            errors.append(f"responses[{idx}].broker_timestamp must be at or after submitted_at")
        if not _has_text(response.client_order_id):
            errors.append(f"responses[{idx}].client_order_id must be non-empty")
        if _has_whitespace(response.client_order_id):
            errors.append(f"responses[{idx}].client_order_id must not contain whitespace")
        if response.status in {
            BrokerOrderStatus.ACCEPTED,
            BrokerOrderStatus.PARTIALLY_FILLED,
            BrokerOrderStatus.FILLED,
            BrokerOrderStatus.CANCELED,
            BrokerOrderStatus.EXPIRED,
        } and not _has_text(response.broker_order_id):
            errors.append(
                f"responses[{idx}].broker_order_id must be non-empty for {response.status.value} broker responses"
            )
        if _has_whitespace(response.broker_order_id):
            errors.append(f"responses[{idx}].broker_order_id must not contain whitespace")
        for field_name in ("accepted_quantity", "fill_quantity", "fill_price", "fees"):
            value = getattr(response, field_name)
            if value is not None and not _is_non_negative_finite_number(value):
                errors.append(f"responses[{idx}].{field_name} must be a non-negative number or null")
        if not _is_positive_finite_number(response.submitted_quantity):
            errors.append(f"responses[{idx}].submitted_quantity must be a positive number")
        if response.status in {
            BrokerOrderStatus.ACCEPTED,
            BrokerOrderStatus.PARTIALLY_FILLED,
            BrokerOrderStatus.FILLED,
        } and not _is_positive_finite_number(response.accepted_quantity):
            errors.append(f"responses[{idx}].{response.status.value} responses require a positive accepted_quantity")
        if _is_finite_number(response.submitted_quantity):
            if _is_finite_number(response.accepted_quantity) and float(response.accepted_quantity) > float(
                response.submitted_quantity
            ):
                errors.append(f"responses[{idx}].accepted_quantity cannot exceed submitted_quantity")
            if _is_finite_number(response.fill_quantity) and float(response.fill_quantity) > float(
                response.submitted_quantity
            ):
                errors.append(f"responses[{idx}].fill_quantity cannot exceed submitted_quantity")
        if _is_finite_number(response.accepted_quantity) and _is_finite_number(response.fill_quantity):
            if float(response.fill_quantity) > float(response.accepted_quantity):
                errors.append(f"responses[{idx}].fill_quantity cannot exceed accepted_quantity")
        if response.status == BrokerOrderStatus.ACCEPTED:
            if _is_positive_finite_number(response.fill_quantity):
                errors.append(f"responses[{idx}].accepted responses must not report fill_quantity")
            if _is_positive_finite_number(response.fill_price):
                errors.append(f"responses[{idx}].accepted responses must not report fill_price")
        if response.status == BrokerOrderStatus.PARTIALLY_FILLED:
            if not _is_positive_finite_number(response.fill_quantity):
                errors.append(f"responses[{idx}].partial fill_quantity must be positive")
            elif _is_finite_number(response.submitted_quantity) and float(response.fill_quantity) >= float(
                response.submitted_quantity
            ):
                errors.append(f"responses[{idx}].partial fill_quantity must be less than submitted_quantity")
        if response.status == BrokerOrderStatus.FILLED:
            if not _is_positive_finite_number(response.fill_quantity):
                errors.append(f"responses[{idx}].filled responses require a positive fill_quantity")
            elif _is_finite_number(response.submitted_quantity) and float(response.fill_quantity) != float(
                response.submitted_quantity
            ):
                errors.append(f"responses[{idx}].filled fill_quantity must equal submitted_quantity")
        if response.status in {BrokerOrderStatus.PARTIALLY_FILLED, BrokerOrderStatus.FILLED}:
            if not _is_positive_finite_number(response.fill_price):
                errors.append(f"responses[{idx}].filled or partially_filled responses require a positive fill_price")
        if response.status == BrokerOrderStatus.REJECTED:
            if not _has_text(response.rejection_reason):
                errors.append(f"responses[{idx}].rejection_reason must be non-empty for rejected responses")
            if _is_positive_finite_number(response.accepted_quantity):
                errors.append(f"responses[{idx}].rejected responses must not report accepted_quantity")
            if _is_positive_finite_number(response.fill_quantity):
                errors.append(f"responses[{idx}].rejected responses must not report fill_quantity")
            if _is_positive_finite_number(response.fill_price):
                errors.append(f"responses[{idx}].rejected responses must not report fill_price")
            if _is_positive_finite_number(response.fees):
                errors.append(f"responses[{idx}].rejected responses must not report fees")
        if response.status == BrokerOrderStatus.UNKNOWN and not _has_text(response.rejection_reason):
            errors.append(f"responses[{idx}].rejection_reason must be non-empty for unknown responses")
        if response.status == BrokerOrderStatus.UNKNOWN:
            for field_name in ("accepted_quantity", "fill_quantity", "fill_price", "fees"):
                if _is_positive_finite_number(getattr(response, field_name)):
                    errors.append(f"responses[{idx}].unknown responses must not report {field_name}")
        if response.status in {BrokerOrderStatus.CANCELED, BrokerOrderStatus.EXPIRED}:
            for field_name in ("fill_quantity", "fill_price"):
                if _is_positive_finite_number(getattr(response, field_name)):
                    errors.append(f"responses[{idx}].{response.status.value} responses must not report {field_name}")
        if response.client_order_id in seen_response_ids:
            errors.append(f"responses[{idx}].client_order_id duplicates an earlier response")
        seen_response_ids.add(response.client_order_id)
        if _has_text(response.broker_order_id):
            if response.broker_order_id in seen_broker_order_ids:
                errors.append(f"responses[{idx}].broker_order_id duplicates an earlier response")
            seen_broker_order_ids.add(response.broker_order_id)
        if response.account_mode != account_mode:
            errors.append(
                f"responses[{idx}].account_mode {response.account_mode} "
                f"does not match artifact account_mode {account_mode}"
            )
        request_quantity = request_quantities.get(response.client_order_id)
        if adapter_mode == BrokerAdapterMode.LIVE_HUMAN_APPROVED and request_quantity is None:
            errors.append(
                f"responses[{idx}].client_order_id {response.client_order_id} is not present in live request set"
            )
        if request_quantity is None or response.submitted_quantity is None:
            continue
        if not _is_positive_finite_number(response.submitted_quantity):
            continue
        submitted_quantity = float(response.submitted_quantity)
        if round(submitted_quantity, 8) != round(request_quantity, 8):
            errors.append(
                f"responses[{idx}].submitted_quantity {submitted_quantity} "
                f"does not match request quantity {request_quantity}"
            )
    return errors


def _validate_approved_order_counts(
    safety: BrokerSafetyConfig,
    orders: list[Order],
    *,
    time_in_force: str,
) -> None:
    if safety.mode != BrokerAdapterMode.LIVE_HUMAN_APPROVED or not safety.approved_order_fingerprints:
        return
    approved_counts = Counter(safety.approved_order_fingerprints)
    requested_counts = Counter(_order_fingerprint_from_order(order) for order in orders)
    for fingerprint, requested_count in requested_counts.items():
        approved_count = approved_counts.get(fingerprint, 0)
        if requested_count > approved_count:
            raise BrokerAdapterContractError(
                f"requested order count {requested_count} exceeds approved broker handoff order count {approved_count}"
            )
    if not safety.approved_order_execution_fingerprints:
        return
    approved_execution_counts = Counter(safety.approved_order_execution_fingerprints)
    requested_execution_counts = Counter(
        _order_execution_fingerprint_from_order(order, time_in_force=time_in_force) for order in orders
    )
    for fingerprint, requested_count in requested_execution_counts.items():
        approved_count = approved_execution_counts.get(fingerprint, 0)
        if requested_count > approved_count:
            raise BrokerAdapterContractError(
                "requested order time_in_force or count does not match the approved broker handoff order"
            )


def _order_fingerprint_from_order(order: Order) -> str:
    return _order_fingerprint(
        symbol=order.symbol,
        side=order.side.value,
        order_type=_alpaca_order_type(order.order_type),
        quantity=order.quantity,
        limit_price=order.limit_price,
    )


def _order_fingerprint_from_handoff(order: dict[object, object]) -> str:
    return _order_fingerprint(
        symbol=str(order.get("symbol")),
        side=str(order.get("side")),
        order_type=str(order.get("order_type")),
        quantity=float(cast(str | int | float, order.get("quantity", 0.0))),
        limit_price=cast(float | None, order.get("limit_price")),
    )


def _order_execution_fingerprint_from_order(order: Order, *, time_in_force: str) -> str:
    return _order_fingerprint(
        symbol=order.symbol,
        side=order.side.value,
        order_type=_alpaca_order_type(order.order_type),
        quantity=order.quantity,
        limit_price=order.limit_price,
        time_in_force=time_in_force,
    )


def _order_execution_fingerprint_from_handoff(order: dict[object, object]) -> str:
    return _order_fingerprint(
        symbol=str(order.get("symbol")),
        side=str(order.get("side")),
        order_type=str(order.get("order_type")),
        quantity=float(cast(str | int | float, order.get("quantity", 0.0))),
        limit_price=cast(float | None, order.get("limit_price")),
        time_in_force=str(order.get("time_in_force")),
    )


def _order_fingerprint(
    *,
    symbol: str,
    side: str,
    order_type: str,
    quantity: float,
    limit_price: float | None,
    time_in_force: str | None = None,
) -> str:
    payload = {
        "symbol": symbol,
        "side": side,
        "order_type": order_type,
        "quantity": round(float(quantity), 8),
        "limit_price": None if limit_price is None else round(float(limit_price), 8),
    }
    if time_in_force is not None:
        payload["time_in_force"] = time_in_force
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _alpaca_order_type(order_type: OrderType) -> str:
    return "limit" if order_type == OrderType.LIMIT else "market"


def _parse_timestamp(value: str | datetime) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_iso_timestamp_with_timezone(value: str) -> bool:
    return bool(_ISO_TIMESTAMP_WITH_TZ_RE.fullmatch(value)) and _parse_timestamp(value) is not None


def _is_timestamp_with_timezone(value: str | datetime) -> bool:
    if isinstance(value, str):
        return _is_iso_timestamp_with_timezone(value)
    return value.tzinfo is not None and value.utcoffset() is not None


def _is_finite_number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _is_positive_finite_number(value: object) -> TypeGuard[int | float]:
    return _is_finite_number(value) and float(value) > 0


def _is_non_negative_finite_number(value: object) -> TypeGuard[int | float]:
    return _is_finite_number(value) and float(value) >= 0


def _has_text(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value.strip())


def _has_whitespace(value: object) -> bool:
    return isinstance(value, str) and any(character.isspace() for character in value)


def _dry_run_safety(safety: BrokerSafetyConfig | None) -> BrokerSafetyConfig:
    if safety is None:
        return BrokerSafetyConfig(mode=BrokerAdapterMode.DRY_RUN)
    return replace(safety, mode=BrokerAdapterMode.DRY_RUN, approval=None)


def _safe_symbol(symbol: str) -> str:
    return "".join(char.lower() if char.isalnum() else "-" for char in symbol).strip("-")


def _validate_client_prefix(client_prefix: object) -> None:
    if not _has_text(client_prefix):
        raise BrokerAdapterContractError("client_prefix must be non-empty")
    if _has_whitespace(client_prefix):
        raise BrokerAdapterContractError("client_prefix must not contain whitespace")


def _validate_adapter_name(adapter: object) -> None:
    if not _has_text(adapter):
        raise BrokerAdapterContractError("adapter must be non-empty")
    if _has_whitespace(adapter):
        raise BrokerAdapterContractError("adapter must not contain whitespace")


def _validate_time_in_force(time_in_force: object) -> None:
    if time_in_force not in _SUPPORTED_TIME_IN_FORCE:
        raise BrokerAdapterContractError(
            f"time_in_force must be one of {', '.join(_SUPPORTED_TIME_IN_FORCE)}"
        )


def _approval_status(safety: BrokerSafetyConfig) -> str:
    if safety.mode == BrokerAdapterMode.LIVE_HUMAN_APPROVED:
        return "approved"
    return "requires_human_approval"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _response_dict(response: BrokerResponse, *, default_timestamp: str | None = None) -> dict[str, object]:
    row = asdict(response)
    row["status"] = response.status.value
    if default_timestamp is not None:
        row["submitted_at"] = row["submitted_at"] or default_timestamp
        row["broker_timestamp"] = row["broker_timestamp"] or default_timestamp
    return row


def _validate_broker_response_row(response: dict[str, object], idx: int) -> list[str]:
    errors: list[str] = []
    required = {
        "client_order_id",
        "status",
        "broker_order_id",
        "submitted_quantity",
        "accepted_quantity",
        "fill_quantity",
        "fill_price",
        "fees",
        "rejection_reason",
        "submitted_at",
        "broker_timestamp",
        "account_mode",
    }
    missing = sorted(required - set(response))
    if missing:
        errors.append(f"responses[{idx}] missing required fields: {', '.join(missing)}")
    extra = sorted(set(response) - required)
    if extra:
        errors.append(f"responses[{idx}] has unexpected fields: {', '.join(extra)}")
    if not _has_text(response.get("client_order_id")):
        errors.append(f"responses[{idx}].client_order_id must be non-empty")
    if _has_whitespace(response.get("client_order_id")):
        errors.append(f"responses[{idx}].client_order_id must not contain whitespace")
    if response.get("status") not in {status.value for status in BrokerOrderStatus}:
        errors.append(f"responses[{idx}].status is not a supported broker order status")
    if response.get("status") == BrokerOrderStatus.REJECTED.value and not _has_text(
        response.get("rejection_reason")
    ):
        errors.append(f"responses[{idx}].rejection_reason must be non-empty for rejected responses")
    if response.get("status") == BrokerOrderStatus.UNKNOWN.value and not _has_text(response.get("rejection_reason")):
        errors.append(f"responses[{idx}].rejection_reason must be non-empty for unknown responses")
    broker_order_statuses = {
        BrokerOrderStatus.ACCEPTED.value,
        BrokerOrderStatus.PARTIALLY_FILLED.value,
        BrokerOrderStatus.FILLED.value,
        BrokerOrderStatus.CANCELED.value,
        BrokerOrderStatus.EXPIRED.value,
    }
    if response.get("status") in broker_order_statuses and not _has_text(response.get("broker_order_id")):
        errors.append(f"responses[{idx}].broker_order_id must be non-empty for {response.get('status')} broker responses")
    if _has_whitespace(response.get("broker_order_id")):
        errors.append(f"responses[{idx}].broker_order_id must not contain whitespace")
    if not _has_text(response.get("account_mode")):
        errors.append(f"responses[{idx}].account_mode must be non-empty")
    for field_name in ("submitted_at", "broker_timestamp"):
        value = response.get(field_name)
        if not isinstance(value, str) or not _is_iso_timestamp_with_timezone(value):
            errors.append(f"responses[{idx}].{field_name} must be an ISO timestamp with timezone")
    submitted_at = response.get("submitted_at")
    broker_timestamp = response.get("broker_timestamp")
    submitted_dt = _parse_timestamp(submitted_at) if isinstance(submitted_at, str) else None
    broker_dt = _parse_timestamp(broker_timestamp) if isinstance(broker_timestamp, str) else None
    if submitted_dt is not None and broker_dt is not None and broker_dt < submitted_dt:
        errors.append(f"responses[{idx}].broker_timestamp must be at or after submitted_at")
    for field_name in ("submitted_quantity", "accepted_quantity", "fill_quantity", "fill_price", "fees"):
        value = response.get(field_name)
        if value is not None and not _is_non_negative_finite_number(value):
            errors.append(f"responses[{idx}].{field_name} must be a non-negative number or null")
    submitted_quantity = response.get("submitted_quantity")
    accepted_quantity = response.get("accepted_quantity")
    fill_quantity = response.get("fill_quantity")
    fill_price = response.get("fill_price")
    if not _is_positive_finite_number(submitted_quantity):
        errors.append(f"responses[{idx}].submitted_quantity must be a positive number")
    if _is_finite_number(submitted_quantity):
        if _is_finite_number(accepted_quantity) and float(accepted_quantity) > float(submitted_quantity):
            errors.append(f"responses[{idx}].accepted_quantity cannot exceed submitted_quantity")
        if _is_finite_number(fill_quantity) and float(fill_quantity) > float(submitted_quantity):
            errors.append(f"responses[{idx}].fill_quantity cannot exceed submitted_quantity")
    if _is_finite_number(accepted_quantity) and _is_finite_number(fill_quantity):
        if float(fill_quantity) > float(accepted_quantity):
            errors.append(f"responses[{idx}].fill_quantity cannot exceed accepted_quantity")
    accepted_quantity_statuses = {
        BrokerOrderStatus.ACCEPTED.value,
        BrokerOrderStatus.PARTIALLY_FILLED.value,
        BrokerOrderStatus.FILLED.value,
    }
    if response.get("status") in accepted_quantity_statuses:
        if not _is_positive_finite_number(accepted_quantity):
            errors.append(
                f"responses[{idx}].{response.get('status')} responses require a positive accepted_quantity"
            )
    if response.get("status") == BrokerOrderStatus.ACCEPTED.value:
        if _is_positive_finite_number(fill_quantity):
            errors.append(f"responses[{idx}].accepted responses must not report fill_quantity")
        if _is_positive_finite_number(fill_price):
            errors.append(f"responses[{idx}].accepted responses must not report fill_price")
    if response.get("status") == BrokerOrderStatus.REJECTED.value:
        if _is_positive_finite_number(accepted_quantity):
            errors.append(f"responses[{idx}].rejected responses must not report accepted_quantity")
        if _is_positive_finite_number(fill_quantity):
            errors.append(f"responses[{idx}].rejected responses must not report fill_quantity")
        if _is_positive_finite_number(fill_price):
            errors.append(f"responses[{idx}].rejected responses must not report fill_price")
        if _is_positive_finite_number(response.get("fees")):
            errors.append(f"responses[{idx}].rejected responses must not report fees")
    if response.get("status") == BrokerOrderStatus.UNKNOWN.value:
        for field_name in ("accepted_quantity", "fill_quantity", "fill_price", "fees"):
            if _is_positive_finite_number(response.get(field_name)):
                errors.append(f"responses[{idx}].unknown responses must not report {field_name}")
    if response.get("status") in {BrokerOrderStatus.CANCELED.value, BrokerOrderStatus.EXPIRED.value}:
        for field_name in ("fill_quantity", "fill_price"):
            if _is_positive_finite_number(response.get(field_name)):
                errors.append(f"responses[{idx}].{response.get('status')} responses must not report {field_name}")
    if response.get("status") == BrokerOrderStatus.PARTIALLY_FILLED.value:
        if not _is_positive_finite_number(fill_quantity):
            errors.append(f"responses[{idx}].partial fill_quantity must be positive")
        elif _is_finite_number(submitted_quantity) and float(fill_quantity) >= float(submitted_quantity):
            errors.append(f"responses[{idx}].partial fill_quantity must be less than submitted_quantity")
    if response.get("status") == BrokerOrderStatus.FILLED.value:
        if not _is_positive_finite_number(fill_quantity):
            errors.append(f"responses[{idx}].filled responses require a positive fill_quantity")
        elif _is_finite_number(submitted_quantity) and float(fill_quantity) != float(submitted_quantity):
            errors.append(f"responses[{idx}].filled fill_quantity must equal submitted_quantity")
    if response.get("status") in {BrokerOrderStatus.PARTIALLY_FILLED.value, BrokerOrderStatus.FILLED.value}:
        if not _is_positive_finite_number(fill_price):
            errors.append(f"responses[{idx}].filled or partially_filled responses require a positive fill_price")
    return errors


def _validate_broker_handoff_order(
    order: dict[str, object],
    idx: int,
    adapter_mode: object,
    artifact_account_mode: object,
) -> list[str]:
    errors: list[str] = []
    required = set(AlpacaPaperOrder.__dataclass_fields__)
    missing = sorted(required - set(order))
    if missing:
        errors.append(f"orders[{idx}] missing required fields: {', '.join(missing)}")
    extra = sorted(set(order) - required)
    if extra:
        errors.append(f"orders[{idx}] has unexpected fields: {', '.join(extra)}")
    required_text_fields = (
        "client_order_id",
        "adapter_mode",
        "account_mode",
        "symbol",
        "side",
        "order_type",
        "time_in_force",
        "reason",
    )
    for field_name in required_text_fields:
        if not _has_text(order.get(field_name)):
            errors.append(f"orders[{idx}].{field_name} must be non-empty")
    if _has_whitespace(order.get("client_order_id")):
        errors.append(f"orders[{idx}].client_order_id must not contain whitespace")
    if _has_whitespace(order.get("symbol")):
        errors.append(f"orders[{idx}].symbol must not contain whitespace")
    if order.get("adapter_mode") != adapter_mode:
        errors.append(f"orders[{idx}].adapter_mode must match artifact adapter_mode")
    if order.get("account_mode") != artifact_account_mode:
        errors.append(f"orders[{idx}].account_mode must match artifact account_mode")
    if order.get("side") not in {"buy", "sell"}:
        errors.append(f"orders[{idx}].side must be buy or sell")
    if order.get("order_type") not in {"market", "limit"}:
        errors.append(f"orders[{idx}].order_type must be market or limit")
    if order.get("time_in_force") not in _SUPPORTED_TIME_IN_FORCE:
        errors.append(f"orders[{idx}].time_in_force must be one of {', '.join(_SUPPORTED_TIME_IN_FORCE)}")
    quantity = order.get("quantity")
    if not _is_positive_finite_number(quantity):
        errors.append(f"orders[{idx}].quantity must be a positive finite number")
    limit_price = order.get("limit_price")
    if limit_price is not None and not _is_positive_finite_number(limit_price):
        errors.append(f"orders[{idx}].limit_price must be a positive finite number or null")
    if order.get("order_type") == OrderType.LIMIT.value and (
        not _is_positive_finite_number(limit_price)
    ):
        errors.append(f"orders[{idx}].limit orders require a positive limit_price")
    if order.get("order_type") == OrderType.MARKET.value and limit_price is not None:
        errors.append(f"orders[{idx}].market orders must not include limit_price")
    max_notional = order.get("max_notional")
    if max_notional is not None and not _is_positive_finite_number(max_notional):
        errors.append(f"orders[{idx}].max_notional must be a positive finite number or null")
    if order.get("submit_live") is not (adapter_mode == BrokerAdapterMode.LIVE_HUMAN_APPROVED.value):
        errors.append(f"orders[{idx}].submit_live must match live_human_approved mode")
    expected_approval = (
        "approved" if adapter_mode == BrokerAdapterMode.LIVE_HUMAN_APPROVED.value else "requires_human_approval"
    )
    if order.get("approval_status") != expected_approval:
        errors.append(f"orders[{idx}].approval_status must be {expected_approval}")
    return errors


def _validate_reconciliation(
    reconciliation: dict[str, object],
    response_count: int,
    status_counts: dict[str, int],
    expected_fill_ratio: float | None,
) -> list[str]:
    errors: list[str] = []
    count_fields = {
        "response_count": response_count,
        "accepted_count": status_counts[BrokerOrderStatus.ACCEPTED.value],
        "rejected_count": status_counts[BrokerOrderStatus.REJECTED.value],
        "partial_fill_count": status_counts[BrokerOrderStatus.PARTIALLY_FILLED.value],
        "filled_count": status_counts[BrokerOrderStatus.FILLED.value],
        "canceled_count": status_counts[BrokerOrderStatus.CANCELED.value],
        "expired_count": status_counts[BrokerOrderStatus.EXPIRED.value],
        "unknown_count": status_counts[BrokerOrderStatus.UNKNOWN.value],
    }
    required = set(count_fields) | {"unmatched_response_count", "missing_response_count", "fill_ratio_mean"}
    missing = sorted(required - set(reconciliation))
    if missing:
        errors.append(f"reconciliation missing required fields: {', '.join(missing)}")
    extra = sorted(set(reconciliation) - required)
    if extra:
        errors.append(f"reconciliation has unexpected fields: {', '.join(extra)}")
    for field_name, expected in count_fields.items():
        actual = reconciliation.get(field_name)
        if actual != expected:
            errors.append(f"reconciliation.{field_name} must be {expected}; got {actual}")
    for field_name in ("unmatched_response_count", "missing_response_count"):
        value = reconciliation.get(field_name)
        if not isinstance(value, int) or value < 0:
            errors.append(f"reconciliation.{field_name} must be a non-negative integer")
    unmatched_response_count = reconciliation.get("unmatched_response_count")
    if isinstance(unmatched_response_count, int) and unmatched_response_count > response_count:
        errors.append(f"reconciliation.unmatched_response_count cannot exceed response_count {response_count}")
    fill_ratio = reconciliation.get("fill_ratio_mean")
    if fill_ratio is not None and not _is_non_negative_finite_number(fill_ratio):
        errors.append("reconciliation.fill_ratio_mean must be a non-negative number or null")
    elif fill_ratio != expected_fill_ratio:
        errors.append(f"reconciliation.fill_ratio_mean must be {expected_fill_ratio}; got {fill_ratio}")
    return errors
