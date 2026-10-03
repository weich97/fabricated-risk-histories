from __future__ import annotations

import json
import re
import shlex
from pathlib import Path, PureWindowsPath
from typing import Any

try:
    from jsonschema import Draft202012Validator
except ModuleNotFoundError:  # pragma: no cover - public package installs may omit dev deps.
    Draft202012Validator = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = ROOT / "schemas" / "operator_runbook_artifact.schema.json"
_ISO_TIMESTAMP_WITH_TZ_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_SHELL_CHAINING_MARKERS = (";", "&", "|")
_REQUIRED_CHECKLIST_IDS = (
    "mode-boundary",
    "approval-expiry",
    "kill-switch",
    "reconciliation",
    "rollback",
    "artifact-retention",
    "incident-owner",
)


def validate_operator_runbook_artifact(payload: dict[str, Any]) -> list[str]:
    if Draft202012Validator is not None and SCHEMA_PATH.exists():
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        schema_errors = [error.message for error in validator.iter_errors(payload)]
        return sorted(
            [
                *schema_errors,
                *_incident_response_drill_errors(payload.get("incident_response_drill")),
                *_checklist_errors(payload),
                *_verification_command_errors(payload),
            ],
            key=str,
        )
    return _fallback_schema_errors(payload)


def validate_operator_runbook_artifact_file(path: str | Path) -> tuple[dict[str, Any], list[str]]:
    payload, errors = _read_operator_runbook_json_file(path)
    if errors:
        return {}, errors
    if not isinstance(payload, dict):
        return {}, ["operator runbook artifact must be a JSON object"]
    return payload, validate_operator_runbook_artifact(payload)


def live_readiness_verification_now(payload: dict[str, Any]) -> str | None:
    """Return the --now timestamp from the supported live-readiness command."""
    args = _live_readiness_verification_args(payload)
    if args is None:
        return None
    return args[2]


def live_readiness_verification_bundle_path(payload: dict[str, Any]) -> str | None:
    """Return the bundle path from the supported live-readiness command."""
    args = _live_readiness_verification_args(payload)
    if args is None:
        return None
    return args[0]


def _live_readiness_verification_args(payload: dict[str, Any]) -> list[str] | None:
    verification_commands = payload.get("verification_commands")
    if not isinstance(verification_commands, list):
        return None
    for command in verification_commands:
        if not isinstance(command, str):
            continue
        args = _live_readiness_args_for_supported_command(command)
        if args is not None and len(args) == 3 and args[1] == "--now" and _is_iso_timestamp_with_timezone(args[2]):
            return args
    return None


def _read_operator_runbook_json_file(path: str | Path) -> tuple[Any, list[str]]:
    artifact_path = Path(path)
    if artifact_path.exists() and not artifact_path.is_file():
        return {}, [f"operator runbook artifact path is not a file: {artifact_path}"]
    try:
        return json.loads(artifact_path.read_text(encoding="utf-8")), []
    except FileNotFoundError:
        return {}, [f"operator runbook artifact not found: {artifact_path}"]
    except json.JSONDecodeError as exc:
        return {}, [f"operator runbook artifact must contain valid JSON: {exc}"]


def _fallback_schema_errors(payload: dict[str, Any]) -> list[str]:
    required = {
        "schema",
        "live_submission",
        "default_mode",
        "allowed_modes",
        "manual_approval_required",
        "kill_switch_required",
        "approval_expiry_required",
        "artifact_retention_required",
        "incident_owner_required",
        "incident_response_drill",
        "checklist",
        "verification_commands",
        "safety_note",
    }
    errors: list[str] = []
    missing = sorted(required - set(payload))
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")
    if payload.get("schema") != "trellm_operator_runbook_v0.1":
        errors.append("schema must be trellm_operator_runbook_v0.1")
    if payload.get("live_submission") is not False:
        errors.append("live_submission must be false")
    if payload.get("default_mode") not in {"offline_export", "dry_run"}:
        errors.append("default_mode must be offline_export or dry_run")
    allowed_modes = payload.get("allowed_modes")
    if not isinstance(allowed_modes, list) or "live_human_approved" not in allowed_modes:
        errors.append("allowed_modes must include live_human_approved")
    for field in [
        "manual_approval_required",
        "kill_switch_required",
        "approval_expiry_required",
        "artifact_retention_required",
        "incident_owner_required",
    ]:
        if payload.get(field) is not True:
            errors.append(f"{field} must be true")
    errors.extend(_incident_response_drill_errors(payload.get("incident_response_drill")))
    checklist = payload.get("checklist")
    if not isinstance(checklist, list) or len(checklist) < 7:
        errors.append("checklist must contain at least seven items")
    errors.extend(_checklist_errors(payload))
    errors.extend(_verification_command_errors(payload))
    return errors


def _incident_response_drill_errors(value: object) -> list[str]:
    if not isinstance(value, dict):
        return ["incident_response_drill must be an object"]
    required = {
        "kill_switch_action",
        "rollback_owner",
        "affected_account_mode",
        "affected_symbols",
        "artifact_retention_path",
        "reenable_approval_gate",
    }
    errors: list[str] = []
    missing = sorted(required - set(value))
    if missing:
        errors.append(f"incident_response_drill missing required fields: {', '.join(missing)}")
    for field in ("kill_switch_action", "rollback_owner", "artifact_retention_path", "reenable_approval_gate"):
        field_value = value.get(field)
        if not isinstance(field_value, str) or not field_value.strip():
            errors.append(f"incident_response_drill.{field} must be non-empty")
    rollback_owner = value.get("rollback_owner")
    if isinstance(rollback_owner, str) and rollback_owner.strip() and _has_whitespace(rollback_owner):
        errors.append("incident_response_drill.rollback_owner must not contain whitespace")
    retention_path = value.get("artifact_retention_path")
    if isinstance(retention_path, str) and not _is_operator_runbook_retention_path(retention_path):
        errors.append(
            "incident_response_drill.artifact_retention_path must stay under outputs/examples/operator_runbook/"
        )
    if value.get("affected_account_mode") not in {"paper", "live"}:
        errors.append("incident_response_drill.affected_account_mode must be paper or live")
    symbols = value.get("affected_symbols")
    if not isinstance(symbols, list) or not symbols or any(
        not isinstance(symbol, str) or not symbol.strip() or _has_whitespace(symbol) for symbol in symbols
    ):
        errors.append("incident_response_drill.affected_symbols must contain non-empty symbols")
    return errors


def _checklist_errors(payload: dict[str, Any]) -> list[str]:
    checklist = payload.get("checklist")
    if not isinstance(checklist, list):
        return ["checklist must contain the required control ids"]
    ids = _checklist_ids(checklist)
    id_set = set(ids)
    missing = [item_id for item_id in _REQUIRED_CHECKLIST_IDS if item_id not in id_set]
    errors: list[str] = []
    if missing:
        errors.append(f"checklist missing required ids: {', '.join(missing)}")
    duplicates = sorted({item_id for item_id in ids if ids.count(item_id) > 1})
    if duplicates:
        errors.append(f"checklist duplicate ids: {', '.join(duplicates)}")
    for index, item in enumerate(checklist):
        if not isinstance(item, dict):
            continue
        owner = item.get("owner")
        if not isinstance(owner, str) or not owner.strip() or _has_whitespace(owner):
            errors.append(f"checklist[{index}].owner must be a non-empty string without whitespace")
    return errors


def _checklist_ids(checklist: list[object]) -> list[str]:
    ids: list[str] = []
    for item in checklist:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if isinstance(item_id, str) and item_id:
            ids.append(item_id)
    return ids


def _verification_command_errors(payload: dict[str, Any]) -> list[str]:
    verification_commands = payload.get("verification_commands")
    if not isinstance(verification_commands, list):
        return ["verification_commands must include validate-live-readiness"]
    commands = [command for command in verification_commands if isinstance(command, str)]
    if any(_has_live_readiness_command_with_shell_chaining(command) for command in commands):
        return ["verification_commands validate-live-readiness command must not contain shell chaining"]
    if _supported_live_readiness_command_count(commands) > 1:
        return ["verification_commands must include exactly one supported validate-live-readiness command"]
    if any(_has_unsupported_live_readiness_command(command) for command in commands):
        return ["verification_commands must not include unsupported validate-live-readiness commands"]
    if any(_has_live_readiness_command_with_unportable_bundle_path(command) for command in commands):
        return ["verification_commands validate-live-readiness preflight bundle path must be portable and relative"]
    if any(_is_runnable_live_readiness_command(command) for command in commands):
        return []
    if any(_has_live_readiness_command_with_invalid_now(command) for command in commands):
        return ["verification_commands validate-live-readiness --now value must be an ISO timestamp with timezone"]
    if any(_has_live_readiness_command_with_unexpected_args(command) for command in commands):
        return [
            "verification_commands validate-live-readiness command must only include "
            "the preflight bundle path, --now, and its timestamp"
        ]
    if any("validate-live-readiness" in command for command in commands):
        return [
            "verification_commands must include a runnable validate-live-readiness command "
            "with a preflight bundle path and --now timestamp"
        ]
    return ["verification_commands must include validate-live-readiness"]


def _is_runnable_live_readiness_command(command: str) -> bool:
    args = _live_readiness_args_for_supported_command(command)
    if args is None or len(args) != 3:
        return False
    preflight_bundle, now_flag, now_value = args
    if now_flag != "--now":
        return False
    return _is_preflight_bundle_path(preflight_bundle) and _is_iso_timestamp_with_timezone(now_value)


def _has_live_readiness_command_with_unportable_bundle_path(command: str) -> bool:
    args = _live_readiness_args_for_supported_command(command)
    if args is None or len(args) != 3 or args[1] != "--now":
        return False
    return _is_iso_timestamp_with_timezone(args[2]) and not _is_portable_relative_path(args[0])


def _has_live_readiness_command_with_invalid_now(command: str) -> bool:
    args = _live_readiness_args_for_supported_command(command)
    if args is None or "--now" not in args:
        return False
    now_index = args.index("--now")
    return now_index + 1 < len(args) and not _is_iso_timestamp_with_timezone(args[now_index + 1])


def _has_live_readiness_command_with_unexpected_args(command: str) -> bool:
    args = _live_readiness_args_for_supported_command(command)
    if args is None or "--now" not in args:
        return False
    now_index = args.index("--now")
    if now_index + 1 >= len(args) or not _is_iso_timestamp_with_timezone(args[now_index + 1]):
        return False
    return len(args) != 3 or args[1] != "--now" or not _is_preflight_bundle_path(args[0])


def _has_live_readiness_command_with_shell_chaining(command: str) -> bool:
    tokens = _command_tokens(command)
    return "validate-live-readiness" in tokens and _contains_shell_chaining(command)


def _supported_live_readiness_command_count(commands: list[str]) -> int:
    return sum(1 for command in commands if _live_readiness_args_for_supported_command(command) is not None)


def _has_unsupported_live_readiness_command(command: str) -> bool:
    return "validate-live-readiness" in command and _live_readiness_args_for_supported_command(command) is None


def _live_readiness_args_for_supported_command(command: str) -> list[str] | None:
    tokens = _command_tokens(command)
    if _contains_shell_chaining(command) or "validate-live-readiness" not in tokens:
        return None
    command_index = tokens.index("validate-live-readiness")
    if command_index == 0:
        return None
    prefix = tokens[:command_index]
    if prefix != ["tradearena"] and prefix != ["python", "-m", "tradearena.cli"]:
        return None
    return tokens[command_index + 1 :]


def _command_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _is_iso_timestamp_with_timezone(value: str) -> bool:
    return bool(_ISO_TIMESTAMP_WITH_TZ_RE.fullmatch(value))


def _contains_shell_chaining(command: str) -> bool:
    return any(marker in command for marker in _SHELL_CHAINING_MARKERS)


def _has_whitespace(value: str) -> bool:
    return any(character.isspace() for character in value)


def _is_preflight_bundle_path(value: str) -> bool:
    return _is_portable_relative_path(value) and value.endswith(".json") and "preflight" in value


def _is_portable_relative_path(value: str) -> bool:
    if not value or any(character.isspace() for character in value):
        return False
    if "\\" in value:
        return False
    if Path(value).is_absolute() or PureWindowsPath(value).is_absolute() or PureWindowsPath(value).drive:
        return False
    return ".." not in Path(value).parts


def _is_operator_runbook_retention_path(value: str) -> bool:
    return _is_portable_relative_path(value) and value.startswith("outputs/examples/operator_runbook/")
