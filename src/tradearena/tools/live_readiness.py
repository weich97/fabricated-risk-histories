from __future__ import annotations

import json
from pathlib import Path, PureWindowsPath
from typing import Any

try:
    from jsonschema import Draft202012Validator
except ModuleNotFoundError:  # pragma: no cover - public package installs may omit dev deps.
    Draft202012Validator = None  # type: ignore[assignment]

from tradearena.tools.broker_capability import validate_broker_adapter_capability_file
from tradearena.tools.broker_export import (
    broker_handoff_artifact_hash,
    validate_broker_approval_artifact_file,
    validate_broker_approval_request_binding,
    validate_broker_handoff_artifact_file,
    validate_broker_response_artifact_file,
)
from tradearena.tools.operator_runbook import (
    live_readiness_verification_bundle_path,
    live_readiness_verification_now,
    validate_operator_runbook_artifact_file,
)

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = ROOT / "schemas" / "live_readiness_preflight.schema.json"
COMPONENT_PATH_FIELDS = (
    "capability_manifest",
    "handoff_artifact",
    "approval_artifact",
    "response_artifact",
    "operator_runbook_artifact",
)


def validate_live_readiness_preflight_bundle_file(
    path: str | Path, *, now: str | None = None
) -> tuple[dict[str, Any], list[str]]:
    bundle_path = Path(path)
    bundle, errors = _read_preflight_json_file(bundle_path)
    if errors:
        return {}, errors
    if not isinstance(bundle, dict):
        return {}, ["live-readiness preflight bundle must be a JSON object"]

    schema_errors = _validate_bundle_schema(bundle)
    component_summary: dict[str, Any] = {"schema_valid": not schema_errors, "components": {}}
    component_errors = _validate_components(bundle, bundle_path=bundle_path, now=now, summary=component_summary)
    errors = sorted([*schema_errors, *component_errors], key=str)
    component_summary["ready"] = not errors
    component_summary["error_count"] = len(errors)
    return component_summary, errors


def _read_preflight_json_file(path: Path) -> tuple[Any, list[str]]:
    if path.exists() and not path.is_file():
        return {}, [f"live-readiness preflight bundle path is not a file: {path}"]
    try:
        return json.loads(path.read_text(encoding="utf-8")), []
    except FileNotFoundError:
        return {}, [f"live-readiness preflight bundle not found: {path}"]
    except json.JSONDecodeError as exc:
        return {}, [f"live-readiness preflight bundle must contain valid JSON: {exc}"]


def _validate_bundle_schema(bundle: dict[str, Any]) -> list[str]:
    if Draft202012Validator is not None and SCHEMA_PATH.exists():
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        return sorted((error.message for error in validator.iter_errors(bundle)), key=str)
    return _fallback_schema_errors(bundle)


def _fallback_schema_errors(bundle: dict[str, Any]) -> list[str]:
    required = {
        "schema",
        "capability_manifest",
        "handoff_artifact",
        "approval_artifact",
        "response_artifact",
        "operator_runbook_artifact",
        "approval_checked_at",
        "safety_note",
    }
    errors: list[str] = []
    missing = sorted(required - set(bundle))
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")
    if bundle.get("schema") != "trellm_live_readiness_preflight_v0.1":
        errors.append("schema must be trellm_live_readiness_preflight_v0.1")
    return errors


def _validate_components(
    bundle: dict[str, Any], *, bundle_path: Path, now: str | None, summary: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    bundle_checked_at = _text(bundle.get("approval_checked_at"))
    checked_at = now or bundle_checked_at
    components = summary["components"]
    if now is not None and bundle_checked_at is not None and bundle_checked_at != now:
        errors.append(f"approval_checked_at {bundle_checked_at} does not match validation --now {now}")
    errors.extend(_component_path_reference_errors(bundle))

    capability_path = _resolve_bundle_path(bundle_path, bundle.get("capability_manifest"))
    handoff_path = _resolve_bundle_path(bundle_path, bundle.get("handoff_artifact"))
    approval_path = _resolve_bundle_path(bundle_path, bundle.get("approval_artifact"))
    response_path = _resolve_bundle_path(bundle_path, bundle.get("response_artifact"))
    runbook_path = _resolve_bundle_path(bundle_path, bundle.get("operator_runbook_artifact"))

    capability, capability_errors = validate_broker_adapter_capability_file(capability_path)
    components["capability_manifest"] = _component_result(capability_path, capability_errors)
    errors.extend(_prefix_errors("capability_manifest", capability_errors))

    handoff, handoff_errors = validate_broker_handoff_artifact_file(handoff_path)
    components["handoff_artifact"] = _component_result(handoff_path, handoff_errors)
    errors.extend(_prefix_errors("handoff_artifact", handoff_errors))

    approval, approval_errors = validate_broker_approval_artifact_file(approval_path, now=checked_at)
    components["approval_artifact"] = _component_result(approval_path, approval_errors)
    errors.extend(_prefix_errors("approval_artifact", approval_errors))

    if not approval_errors and not handoff_errors:
        binding_errors = validate_broker_approval_request_binding(approval, handoff_path, now=checked_at)
    else:
        binding_errors = ["approval binding skipped until approval and handoff artifacts validate"]
    components["approval_binding"] = {"valid": not binding_errors, "error_count": len(binding_errors)}
    errors.extend(_prefix_errors("approval_binding", binding_errors))

    response, response_errors = validate_broker_response_artifact_file(response_path)
    components["response_artifact"] = _component_result(response_path, response_errors)
    errors.extend(_prefix_errors("response_artifact", response_errors))

    runbook, runbook_errors = validate_operator_runbook_artifact_file(runbook_path)
    components["operator_runbook_artifact"] = _component_result(runbook_path, runbook_errors)
    errors.extend(_prefix_errors("operator_runbook_artifact", runbook_errors))

    if not capability_errors:
        errors.extend(_capability_boundary_errors(capability, handoff, response))
    if not capability_errors and not runbook_errors:
        errors.extend(_runbook_capability_errors(capability, runbook))
    if not runbook_errors and not handoff_errors:
        errors.extend(_runbook_handoff_scope_errors(runbook, handoff))
    if not runbook_errors and checked_at is not None:
        errors.extend(_runbook_command_binding_errors(runbook, checked_at, bundle_path))
    if not handoff_errors and not response_errors:
        errors.extend(_handoff_response_linkage_errors(handoff, response))
    return errors


def _capability_boundary_errors(
    capability: dict[str, Any], handoff: dict[str, Any], response: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    adapter_id = capability.get("adapter_id")
    supported_modes = _string_set(capability.get("supported_modes"))
    account_modes = _string_set(capability.get("account_modes"))
    supported_order_types = _string_set(capability.get("supported_order_types"))
    supported_time_in_force = _string_set(capability.get("supported_time_in_force"))
    for label, payload in (("handoff_artifact", handoff), ("response_artifact", response)):
        adapter = payload.get("adapter")
        adapter_mode = payload.get("adapter_mode")
        account_mode = payload.get("account_mode")
        if isinstance(adapter, str) and isinstance(adapter_id, str) and adapter != adapter_id:
            errors.append(f"{label}.adapter {adapter} does not match capability_manifest.adapter_id {adapter_id}")
        if isinstance(adapter_mode, str) and adapter_mode not in supported_modes:
            errors.append(f"{label}.adapter_mode {adapter_mode} is not declared in capability_manifest.supported_modes")
        if isinstance(account_mode, str) and account_mode not in account_modes:
            errors.append(f"{label}.account_mode {account_mode} is not declared in capability_manifest.account_modes")
        if capability.get("supports_live_submission") is not True and payload.get("live_submission") is True:
            errors.append(f"{label} uses live_submission but capability_manifest does not support live submission")
    for idx, order in enumerate(_object_rows(handoff.get("orders"))):
        order_type = order.get("order_type")
        time_in_force = order.get("time_in_force")
        if isinstance(order_type, str) and order_type not in supported_order_types:
            errors.append(
                f"handoff_artifact.orders[{idx}].order_type {order_type} "
                "is not declared in capability_manifest.supported_order_types"
            )
        if isinstance(time_in_force, str) and time_in_force not in supported_time_in_force:
            errors.append(
                f"handoff_artifact.orders[{idx}].time_in_force {time_in_force} "
                "is not declared in capability_manifest.supported_time_in_force"
            )
    return errors


def _runbook_capability_errors(capability: dict[str, Any], runbook: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    capability_default = capability.get("default_mode")
    runbook_default = runbook.get("default_mode")
    if isinstance(capability_default, str) and isinstance(runbook_default, str) and capability_default != runbook_default:
        errors.append(
            f"operator_runbook_artifact.default_mode {runbook_default} does not match "
            f"capability_manifest.default_mode {capability_default}"
        )
    errors.extend(_runbook_capability_safety_control_errors(capability, runbook))
    return errors


def _runbook_capability_safety_control_errors(capability: dict[str, Any], runbook: dict[str, Any]) -> list[str]:
    capability_controls = capability.get("safety_controls")
    if not isinstance(capability_controls, dict):
        return []
    required_controls = (
        "manual_approval_required",
        "approval_expiry_required",
        "kill_switch_required",
        "artifact_retention_required",
    )
    errors: list[str] = []
    for field in required_controls:
        if runbook.get(field) is True and capability_controls.get(field) is not True:
            errors.append(
                f"capability_manifest.safety_controls.{field} "
                f"{str(capability_controls.get(field)).lower()} does not satisfy "
                f"operator_runbook_artifact.{field} true"
            )
    if (
        "reconciliation" in _checklist_ids(runbook.get("checklist"))
        and capability_controls.get("reconciliation_required") is not True
    ):
        errors.append(
            "capability_manifest.safety_controls.reconciliation_required "
            f"{str(capability_controls.get('reconciliation_required')).lower()} does not satisfy "
            "operator_runbook_artifact checklist id reconciliation"
        )
    return errors


def _checklist_ids(value: object) -> set[str]:
    return {
        str(item["id"])
        for item in _object_rows(value)
        if isinstance(item.get("id"), str) and str(item["id"]).strip()
    }


def _runbook_handoff_scope_errors(runbook: dict[str, Any], handoff: dict[str, Any]) -> list[str]:
    drill = runbook.get("incident_response_drill")
    if not isinstance(drill, dict):
        return []
    errors: list[str] = []
    affected_account_mode = drill.get("affected_account_mode")
    handoff_account_mode = handoff.get("account_mode")
    if (
        isinstance(affected_account_mode, str)
        and isinstance(handoff_account_mode, str)
        and affected_account_mode != handoff_account_mode
    ):
        errors.append(
            "operator_runbook_artifact.incident_response_drill.affected_account_mode "
            f"{affected_account_mode} does not cover handoff_artifact.account_mode {handoff_account_mode}"
        )
    affected_symbols = _string_set(drill.get("affected_symbols"))
    handoff_symbols = _order_symbols(handoff.get("orders"))
    missing_symbols = sorted(handoff_symbols - affected_symbols)
    if missing_symbols:
        errors.append(
            "operator_runbook_artifact.incident_response_drill.affected_symbols missing "
            f"handoff symbols: {', '.join(missing_symbols)}"
        )
    return errors


def _runbook_command_binding_errors(runbook: dict[str, Any], checked_at: str, bundle_path: Path) -> list[str]:
    errors: list[str] = []
    runbook_now = live_readiness_verification_now(runbook)
    if runbook_now is not None and runbook_now != checked_at:
        errors.append(
            f"operator_runbook_artifact verification command --now {runbook_now} "
            f"does not match preflight approval_checked_at {checked_at}"
        )
    runbook_bundle_path = live_readiness_verification_bundle_path(runbook)
    if runbook_bundle_path is not None and _resolved_command_bundle_path(bundle_path, runbook_bundle_path) != _resolved_path(bundle_path):
        errors.append(
            f"operator_runbook_artifact verification command bundle path {runbook_bundle_path} "
            "does not match current preflight bundle"
        )
    return errors


def _resolved_command_bundle_path(bundle_path: Path, value: str) -> Path:
    return _resolved_path(_resolve_bundle_path(bundle_path, value))


def _resolved_path(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def _handoff_response_linkage_errors(handoff: dict[str, Any], response: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    response_request_hash = response.get("request_artifact_hash")
    if not isinstance(response_request_hash, str) or not response_request_hash.strip():
        errors.append("response_artifact.request_artifact_hash is required for live-readiness preflight")
    elif response_request_hash != broker_handoff_artifact_hash(handoff):
        errors.append("response_artifact.request_artifact_hash does not match handoff_artifact hash")
    for field in ("adapter", "adapter_mode", "account_mode", "live_submission"):
        handoff_value = handoff.get(field)
        response_value = response.get(field)
        if handoff_value != response_value:
            errors.append(
                f"response_artifact.{field} {response_value} does not match "
                f"handoff_artifact.{field} {handoff_value}"
            )
    handoff_ids = _client_order_ids(handoff.get("orders"))
    handoff_quantities = _quantity_by_client_order_id(handoff.get("orders"))
    response_ids: set[str] = set()
    unmatched_response_count = 0
    for idx, row in enumerate(_object_rows(response.get("responses"))):
        client_order_id = row.get("client_order_id")
        if not isinstance(client_order_id, str):
            continue
        if client_order_id not in handoff_ids:
            unmatched_response_count += 1
            errors.append(
                f"response_artifact.responses[{idx}].client_order_id {client_order_id} "
                "is not present in handoff_artifact.orders"
            )
            continue
        submitted_quantity = row.get("submitted_quantity")
        handoff_quantity = handoff_quantities.get(client_order_id)
        if isinstance(submitted_quantity, (int, float)) and handoff_quantity is not None and submitted_quantity != handoff_quantity:
            errors.append(
                f"response_artifact.responses[{idx}].submitted_quantity {submitted_quantity} does not match "
                f"handoff_artifact.orders quantity {handoff_quantity} for client_order_id {client_order_id}"
            )
        response_ids.add(client_order_id)
    missing_response_count = 0
    for idx, client_order_id in _client_order_id_rows(handoff.get("orders")):
        if client_order_id not in response_ids:
            missing_response_count += 1
            errors.append(
                f"handoff_artifact.orders[{idx}].client_order_id {client_order_id} "
                "is missing from response_artifact.responses"
            )
    errors.extend(
        _reconciliation_linkage_count_errors(
            response.get("reconciliation"),
            missing_response_count=missing_response_count,
            unmatched_response_count=unmatched_response_count,
        )
    )
    return errors


def _reconciliation_linkage_count_errors(
    value: object, *, missing_response_count: int, unmatched_response_count: int
) -> list[str]:
    if not isinstance(value, dict):
        return []
    expected_counts = {
        "missing_response_count": missing_response_count,
        "unmatched_response_count": unmatched_response_count,
    }
    errors: list[str] = []
    for field_name, expected in expected_counts.items():
        actual = value.get(field_name)
        if actual != expected:
            errors.append(
                f"response_artifact.reconciliation.{field_name} {actual} "
                f"does not match handoff/response linkage count {expected}"
            )
    return errors


def _client_order_ids(value: object) -> set[str]:
    return {
        str(row["client_order_id"])
        for row in _object_rows(value)
        if isinstance(row.get("client_order_id"), str) and str(row["client_order_id"]).strip()
    }


def _order_symbols(value: object) -> set[str]:
    return {
        str(row["symbol"])
        for row in _object_rows(value)
        if isinstance(row.get("symbol"), str) and str(row["symbol"]).strip()
    }


def _client_order_id_rows(value: object) -> list[tuple[int, str]]:
    return [
        (idx, str(row["client_order_id"]))
        for idx, row in enumerate(_object_rows(value))
        if isinstance(row.get("client_order_id"), str) and str(row["client_order_id"]).strip()
    ]


def _quantity_by_client_order_id(value: object) -> dict[str, float]:
    quantities: dict[str, float] = {}
    for row in _object_rows(value):
        client_order_id = row.get("client_order_id")
        quantity = row.get("quantity")
        if isinstance(client_order_id, str) and isinstance(quantity, (int, float)):
            quantities[client_order_id] = float(quantity)
    return quantities


def _object_rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _component_result(path: Path, errors: list[str]) -> dict[str, Any]:
    return {
        "path": _display_path(path),
        "exists": path.exists(),
        "valid": not errors,
        "error_count": len(errors),
    }


def _prefix_errors(label: str, errors: list[str]) -> list[str]:
    return [f"{label}: {error}" for error in errors]


def _resolve_bundle_path(bundle_path: Path, value: object) -> Path:
    if not isinstance(value, str) or not value.strip():
        return bundle_path.parent / "<missing>"
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    root_candidate = ROOT / candidate
    if root_candidate.exists() or value.startswith(("outputs/", "docs/", "examples/", "schemas/")):
        return root_candidate
    return bundle_path.parent / candidate


def _component_path_reference_errors(bundle: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in COMPONENT_PATH_FIELDS:
        value = bundle.get(field)
        if isinstance(value, str) and _has_parent_traversal(value):
            errors.append(f"{field} path must not contain parent traversal")
        if isinstance(value, str) and _is_absolute_component_path(value):
            errors.append(f"{field} path must be relative")
        if isinstance(value, str) and _is_drive_qualified_component_path(value):
            errors.append(f"{field} path must not be drive-qualified")
        if isinstance(value, str) and "\\" in value:
            errors.append(f"{field} path must use forward slashes")
        if isinstance(value, str) and _has_path_whitespace(value):
            errors.append(f"{field} path must not contain whitespace")
    return errors


def _has_parent_traversal(value: str) -> bool:
    return any(part == ".." for part in Path(value).parts)


def _is_absolute_component_path(value: str) -> bool:
    return Path(value).is_absolute() or PureWindowsPath(value).is_absolute()


def _is_drive_qualified_component_path(value: str) -> bool:
    return bool(PureWindowsPath(value).drive)


def _has_path_whitespace(value: str) -> bool:
    return any(character.isspace() for character in value)


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except (OSError, ValueError):
        return path.as_posix()


def _string_set(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str)}


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None
