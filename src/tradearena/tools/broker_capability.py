from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft202012Validator
except ModuleNotFoundError:  # pragma: no cover - public package installs may omit dev deps.
    Draft202012Validator = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = ROOT / "schemas" / "broker_adapter_capability.schema.json"
_SUPPORTED_MODES = {"offline_export", "dry_run", "paper_sandbox", "live_human_approved"}
_SUPPORTED_ACCOUNT_MODES = {"none", "paper", "live"}
_SUPPORTED_ORDER_TYPES = {"market", "limit"}
_SUPPORTED_TIME_IN_FORCE = {"cls", "day", "fok", "gtc", "ioc", "opg"}


def validate_broker_adapter_capability(payload: dict[str, Any]) -> list[str]:
    if Draft202012Validator is not None and SCHEMA_PATH.exists():
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        errors = sorted((error.message for error in validator.iter_errors(payload)), key=str)
    else:
        errors = _fallback_schema_errors(payload)
    errors.extend(_semantic_errors(payload))
    return sorted(set(errors), key=str)


def validate_broker_adapter_capability_file(path: str | Path) -> tuple[dict[str, Any], list[str]]:
    payload, errors = _read_capability_json_file(path)
    if errors:
        return {}, errors
    if not isinstance(payload, dict):
        return {}, ["broker adapter capability manifest must be a JSON object"]
    return payload, validate_broker_adapter_capability(payload)


def _read_capability_json_file(path: str | Path) -> tuple[Any, list[str]]:
    artifact_path = Path(path)
    if artifact_path.exists() and not artifact_path.is_file():
        return {}, [f"broker adapter capability manifest path is not a file: {artifact_path}"]
    try:
        return json.loads(artifact_path.read_text(encoding="utf-8")), []
    except FileNotFoundError:
        return {}, [f"broker adapter capability manifest not found: {artifact_path}"]
    except json.JSONDecodeError as exc:
        return {}, [f"broker adapter capability manifest must contain valid JSON: {exc}"]


def _fallback_schema_errors(payload: dict[str, Any]) -> list[str]:
    required = {
        "schema",
        "adapter_id",
        "adapter_name",
        "adapter_kind",
        "default_mode",
        "supported_modes",
        "account_modes",
        "network_access",
        "supports_live_submission",
        "live_submission_default",
        "requires_credentials",
        "credential_policy",
        "safety_controls",
        "supported_order_types",
        "supported_time_in_force",
        "verification_commands",
        "safety_note",
    }
    errors: list[str] = []
    missing = sorted(required - set(payload))
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")
    if payload.get("schema") != "trellm_broker_adapter_capability_v0.1":
        errors.append("schema must be trellm_broker_adapter_capability_v0.1")
    if payload.get("default_mode") == "live_human_approved":
        errors.append("default_mode must not be live_human_approved")
    if payload.get("live_submission_default") is not False:
        errors.append("live_submission_default must be false")
    return errors


def _semantic_errors(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    supported_modes = _string_set(payload.get("supported_modes"))
    account_modes = _string_set(payload.get("account_modes"))
    safety_controls = payload.get("safety_controls")
    credential_policy = payload.get("credential_policy")

    adapter_id = payload.get("adapter_id")
    if isinstance(adapter_id, str) and any(char.isspace() for char in adapter_id):
        errors.append("adapter_id must not contain whitespace")

    default_mode = payload.get("default_mode")
    if isinstance(default_mode, str) and default_mode not in _SUPPORTED_MODES - {"live_human_approved"}:
        errors.append("default_mode must be offline_export, dry_run, or paper_sandbox")
    if not _contains_only_supported_strings(payload.get("supported_modes"), _SUPPORTED_MODES):
        errors.append("supported_modes must contain only supported broker adapter modes")
    if not _contains_only_supported_strings(payload.get("account_modes"), _SUPPORTED_ACCOUNT_MODES):
        errors.append("account_modes must contain only none, paper, or live")
    if not _contains_only_supported_strings(payload.get("supported_order_types"), _SUPPORTED_ORDER_TYPES):
        errors.append("supported_order_types must contain only market or limit")
    if not _contains_only_supported_strings(payload.get("supported_time_in_force"), _SUPPORTED_TIME_IN_FORCE):
        errors.append("supported_time_in_force must contain only cls, day, fok, gtc, ioc, or opg")

    if isinstance(default_mode, str) and supported_modes and default_mode not in supported_modes:
        errors.append("default_mode must be included in supported_modes")

    if payload.get("adapter_kind") == "live_capable" and payload.get("supports_live_submission") is not True:
        errors.append("adapter_kind live_capable requires supports_live_submission true")

    if payload.get("supports_live_submission") is True:
        if payload.get("adapter_kind") != "live_capable":
            errors.append("live-capable adapters must set adapter_kind to live_capable")
        if payload.get("network_access") != "required_for_live":
            errors.append("live-capable adapters must set network_access to required_for_live")
        if "live_human_approved" not in supported_modes:
            errors.append("live-capable adapters must include live_human_approved in supported_modes")
        if "live" not in account_modes:
            errors.append("live-capable adapters must include live in account_modes")
        if payload.get("requires_credentials") is not True:
            errors.append("live-capable adapters must require credentials")
        for field in (
            "manual_approval_required",
            "approval_expiry_required",
            "request_hash_binding_required",
            "kill_switch_required",
            "reconciliation_required",
            "artifact_retention_required",
        ):
            if not isinstance(safety_controls, dict) or safety_controls.get(field) is not True:
                errors.append(f"live-capable adapters must set safety_controls.{field} to true")

    if isinstance(credential_policy, dict):
        if credential_policy.get("no_credentials_in_repo") is not True:
            errors.append("credential_policy.no_credentials_in_repo must be true")
        if credential_policy.get("redacted_artifacts_only") is not True:
            errors.append("credential_policy.redacted_artifacts_only must be true")
        env_vars = credential_policy.get("env_vars")
        if isinstance(env_vars, list) and any(
            isinstance(item, str) and any(char.isspace() for char in item) for item in env_vars
        ):
            errors.append("credential_policy.env_vars must not contain whitespace")
        if payload.get("requires_credentials") is True and not _string_set(credential_policy.get("env_vars")):
            errors.append("credential_policy.env_vars must list credential environment variables when credentials are required")
    return errors


def _string_set(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str)}


def _contains_only_supported_strings(value: object, allowed: set[str]) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and item in allowed for item in value)
