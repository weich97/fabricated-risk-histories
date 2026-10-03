from __future__ import annotations

import html
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from tradearena.core.reproducibility import compute_reproducibility_hash
from tradearena.evaluation.evidence import (
    claim_class_for_tags,
    evidence_tier_for_tags,
    format_evidence_tags,
    parse_evidence_tags,
    validate_evidence_boundary,
    validate_evidence_tags,
)

REQUIRED_TOP_LEVEL = (
    "schema_version",
    "scenario_id",
    "agent",
    "data_source",
    "execution_config",
    "risk_config",
    "metrics",
    "trajectory_manifest",
    "reproducibility_hash",
    "redaction",
)


def load_submission(path: str | Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("benchmark submission file must contain valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("benchmark submission file must be a JSON object")
    return payload


def validate_submission(payload: dict[str, Any], *, verify_hash: bool = True) -> list[str]:
    errors: list[str] = []
    _require_keys(payload, REQUIRED_TOP_LEVEL, "submission", errors)
    if errors:
        return errors

    if payload.get("schema_version") != "0.1":
        errors.append("submission.schema_version must be '0.1'")

    _require_keys(
        payload["agent"],
        ("agent_type", "model_family", "model_identifier_redacted"),
        "agent",
        errors,
    )
    _require_keys(
        payload["data_source"],
        ("name", "frequency", "symbols", "timestamp_policy"),
        "data_source",
        errors,
    )
    _require_keys(
        payload["execution_config"],
        ("commission_bps", "base_slippage_bps", "latency_steps", "participation_rate"),
        "execution_config",
        errors,
    )
    _require_keys(payload["risk_config"], ("risk_manager", "risk_budget"), "risk_config", errors)
    _require_keys(
        payload["metrics"],
        (
            "total_return",
            "max_drawdown",
            "execution_fill_rate",
            "rejected_order_count",
            "risk_clipped_decisions",
            "risk_violation_count",
            "trajectory_reproducibility_coverage",
        ),
        "metrics",
        errors,
    )
    _require_keys(
        payload["trajectory_manifest"],
        ("format", "path_or_uri", "raw_prompts_included", "raw_responses_included"),
        "trajectory_manifest",
        errors,
    )
    _require_keys(
        payload["redaction"],
        ("provider_secrets_removed", "timestamps_masked", "raw_provider_text_removed"),
        "redaction",
        errors,
    )

    symbols = payload["data_source"].get("symbols", [])
    if not isinstance(symbols, list) or not symbols:
        errors.append("data_source.symbols must be a non-empty list")

    fill_rate = payload["metrics"].get("execution_fill_rate")
    if not _is_number(fill_rate) or not 0.0 <= float(fill_rate) <= 1.0:
        errors.append("metrics.execution_fill_rate must be a number in [0, 1]")

    parse_coverage = payload["agent"].get("parse_coverage")
    if parse_coverage is not None and (not _is_number(parse_coverage) or not 0.0 <= float(parse_coverage) <= 1.0):
        errors.append("agent.parse_coverage must be a number in [0, 1] when provided")

    coverage = payload["metrics"].get("trajectory_reproducibility_coverage")
    if not _is_number(coverage) or not 0.0 <= float(coverage) <= 1.0:
        errors.append("metrics.trajectory_reproducibility_coverage must be a number in [0, 1]")

    for path in (
        "redaction.provider_secrets_removed",
        "redaction.raw_provider_text_removed",
        "trajectory_manifest.raw_prompts_included",
        "trajectory_manifest.raw_responses_included",
    ):
        value = _get_path(payload, path)
        if not isinstance(value, bool):
            errors.append(f"{path} must be boolean")

    if verify_hash:
        expected = compute_reproducibility_hash(payload)
        observed = payload.get("reproducibility_hash")
        if observed != expected:
            errors.append(f"reproducibility_hash mismatch: expected {expected}, observed {observed}")

    evidence = payload.get("evidence")
    if evidence is not None:
        if not isinstance(evidence, dict):
            errors.append("evidence must be an object when provided")
        else:
            tags = evidence.get("tags")
            if not isinstance(tags, list) or not tags:
                errors.append("evidence.tags must be a non-empty list when evidence is provided")
            else:
                errors.extend(validate_evidence_tags(tags))
            if not isinstance(evidence.get("claim_scope"), str) or not evidence.get("claim_scope"):
                errors.append("evidence.claim_scope must be a non-empty string when evidence is provided")
            else:
                evidence = {**evidence, "provider": payload["agent"].get("provider", "")}
                errors.extend(validate_evidence_boundary(evidence))

    return errors


def validate_submission_file(path: str | Path, *, verify_hash: bool = True) -> tuple[dict[str, Any], list[str]]:
    try:
        payload = load_submission(path)
    except ValueError as exc:
        return {}, [str(exc)]
    return payload, validate_submission(payload, verify_hash=verify_hash)


def discover_submission_files(input_dir: str | Path) -> list[Path]:
    root = Path(input_dir)
    if root.is_file():
        return [root]
    return sorted(path for path in root.rglob("*.json") if path.is_file())


def build_registry_rows(input_dir: str | Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_hashes: defaultdict[str, int] = defaultdict(int)
    for path in discover_submission_files(input_dir):
        payload, payload_errors = validate_submission_file(path)
        if payload_errors:
            errors.extend(f"{path}: {error}" for error in payload_errors)
            continue
        seen_hashes[payload["reproducibility_hash"]] += 1
        evidence = payload.get("evidence", {})
        evidence_tags = evidence.get("tags", []) if isinstance(evidence, dict) else []
        evidence_tag_text = format_evidence_tags(evidence_tags)
        parsed_evidence_tags = parse_evidence_tags(evidence_tags)
        claim_scope = str(evidence.get("claim_scope", "")) if isinstance(evidence, dict) else ""
        rows.append(
            {
                "entry_id": f"ta-{payload['reproducibility_hash'].split(':')[-1][:12]}",
                "scenario_id": payload["scenario_id"],
                "agent_type": payload["agent"]["agent_type"],
                "provider": payload["agent"].get("provider", "unknown"),
                "model_family": payload["agent"]["model_family"],
                "prompt_mode": payload["agent"].get("prompt_mode", "n/a"),
                "risk_feedback_mode": payload["agent"].get("risk_feedback_mode", "n/a"),
                "parse_coverage": payload["agent"].get("parse_coverage", ""),
                "model_redacted": payload["agent"]["model_identifier_redacted"],
                "data_source": payload["data_source"]["name"],
                "frequency": payload["data_source"]["frequency"],
                "symbols": len(payload["data_source"]["symbols"]),
                "total_return": payload["metrics"]["total_return"],
                "max_drawdown": payload["metrics"]["max_drawdown"],
                "fill_rate": payload["metrics"]["execution_fill_rate"],
                "rejected_orders": payload["metrics"]["rejected_order_count"],
                "risk_edits": payload["metrics"]["risk_clipped_decisions"],
                "audit_coverage": payload["metrics"]["trajectory_reproducibility_coverage"],
                "reproducibility_hash": payload["reproducibility_hash"],
                "reproducibility_status": "Reproducible",
                "redaction_status": "Redacted",
                "evidence_tags": evidence_tag_text,
                "claim_class": str(evidence.get("claim_class") or claim_class_for_tags(parsed_evidence_tags)),
                "evidence_tier": str(evidence.get("evidence_tier") or evidence_tier_for_tags(parsed_evidence_tags)),
                "claim_scope": claim_scope,
                "source_file": path.as_posix(),
            }
        )
    for fingerprint, count in seen_hashes.items():
        if count > 1:
            errors.append(f"duplicate reproducibility_hash appears {count} times: {fingerprint}")
    return rows, errors


def write_registry_markdown(rows: list[dict[str, Any]], output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# TradeArena Leaderboard Registry",
        "",
        "This registry is generated from redacted leaderboard submission manifests.",
        "It is designed to compare audit-ready runs without exposing raw provider",
        "prompts, responses, private portfolios, or credentials.",
        "",
        "| Entry | Scenario | Agent | Prompt | Feedback | Evidence | Claim | Tier | Parse | Data | Return | Max DD | Fill | Rejected | Risk edits | Audit | Badges | Hash |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for row in rows:
        agent = f"{row['provider']} / {row['model_family']}"
        data = f"{row['data_source']} ({row['frequency']}, {row['symbols']} symbols)"
        parse_coverage = "" if row["parse_coverage"] == "" else _fmt_float(row["parse_coverage"])
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["entry_id"]),
                    str(row["scenario_id"]),
                    agent,
                    str(row["prompt_mode"]),
                    str(row["risk_feedback_mode"]),
                    _format_evidence_cell(row["evidence_tags"]),
                    str(row["claim_class"]),
                    str(row["evidence_tier"]),
                    parse_coverage,
                    data,
                    _fmt_float(row["total_return"]),
                    _fmt_float(row["max_drawdown"]),
                    _fmt_float(row["fill_rate"]),
                    str(row["rejected_orders"]),
                    str(row["risk_edits"]),
                    _fmt_float(row["audit_coverage"]),
                    f"{row['reproducibility_status']}; {row['redaction_status']}",
                    f"`{str(row['reproducibility_hash'])[:18]}...`",
                ]
            )
            + " |"
        )
    if not rows:
        lines.append("| _No accepted submissions yet._ |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |")
    lines.extend(
        [
            "",
            "## Submission Rules",
            "",
            "- Submit redacted manifests, not raw model prompt/response caches.",
            "- Do not include broker credentials, API keys, or private holdings.",
            "- Keep `reproducibility_hash` stable for the same scenario, data,",
            "  execution config, risk config, agent metadata, and trajectory manifest.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_registry_html(rows: list[dict[str, Any]], output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    providers = sorted({str(row["provider"]) for row in rows})
    provider_options = "\n".join(f'<option value="{_h(provider)}">{_h(provider)}</option>' for provider in providers)
    table_rows = "\n".join(_registry_html_row(row) for row in rows)
    if not table_rows:
        table_rows = '<tr><td colspan="18">No accepted submissions yet.</td></tr>'
    html_text = """<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TradeArena Leaderboard Registry</title>
<style>
body { margin: 0; font-family: Inter, Arial, sans-serif; background: #f8fafc; color: #0f172a; }
main { max-width: 1240px; margin: 0 auto; padding: 38px 24px 52px; }
h1 { margin: 0 0 8px; font-size: 34px; letter-spacing: 0; }
p { color: #475569; line-height: 1.55; }
.controls { display: flex; gap: 12px; flex-wrap: wrap; margin: 22px 0 18px; }
input, select { border: 1px solid #cbd5e1; border-radius: 6px; padding: 10px 12px; font-size: 14px; background: white; }
input { min-width: min(420px, 100%); flex: 1; }
.table-wrap { overflow-x: auto; border: 1px solid #d8e2ed; background: white; }
table { width: 100%; border-collapse: collapse; min-width: 1120px; }
th, td { padding: 10px 11px; border-bottom: 1px solid #e2e8f0; text-align: left; font-size: 13px; vertical-align: top; }
th { background: #eff6ff; color: #1e3a8a; cursor: pointer; user-select: none; white-space: nowrap; }
tr:hover { background: #f8fafc; }
code { background: #eef2f7; padding: 2px 4px; border-radius: 4px; }
.badge { display: inline-block; border: 1px solid #bbf7d0; background: #f0fdf4; color: #166534; border-radius: 999px; padding: 2px 7px; font-size: 12px; margin: 0 4px 4px 0; white-space: nowrap; }
.badge.redacted { border-color: #bae6fd; background: #f0f9ff; color: #075985; }
details { max-width: 260px; }
summary { color: #1d4ed8; cursor: pointer; }
.muted { color: #64748b; }
</style>
<main>
  <h1>TradeArena Leaderboard Registry</h1>
  <p>Generated from redacted leaderboard submission manifests. Raw provider
  prompts, responses, credentials, and private portfolio data are not included.</p>
  <div class="controls">
    <input id="search" type="search" placeholder="Search scenario, agent, data source, or hash">
    <select id="provider">
      <option value="">All providers</option>
      __PROVIDER_OPTIONS__
    </select>
  </div>
  <div class="table-wrap">
    <table id="registry">
      <thead>
        <tr>
          <th data-sort="text">Entry</th><th data-sort="text">Scenario</th><th data-sort="text">Agent</th>
          <th data-sort="text">Prompt</th><th data-sort="text">Feedback</th><th data-sort="text">Evidence</th>
          <th data-sort="text">Claim</th><th data-sort="text">Tier</th><th data-sort="num">Parse</th>
          <th data-sort="text">Data</th><th data-sort="num">Return</th><th data-sort="num">Max DD</th>
          <th data-sort="num">Fill</th><th data-sort="num">Rejected</th><th data-sort="num">Risk edits</th>
          <th data-sort="num">Audit</th><th data-sort="text">Badges</th><th data-sort="text">Details</th>
        </tr>
      </thead>
      <tbody>
        __TABLE_ROWS__
      </tbody>
    </table>
  </div>
  <p class="muted">Rows are accepted only after schema validation and reproducibility-hash verification.</p>
</main>
<script>
const search = document.querySelector("#search");
const provider = document.querySelector("#provider");
const table = document.querySelector("#registry");
const rows = Array.from(table.tBodies[0].rows);

function applyFilter() {
  const query = search.value.toLowerCase();
  const selectedProvider = provider.value;
  for (const row of rows) {
    const matchesQuery = row.dataset.search.includes(query);
    const matchesProvider = !selectedProvider || row.dataset.provider === selectedProvider;
    row.style.display = matchesQuery && matchesProvider ? "" : "none";
  }
}

search.addEventListener("input", applyFilter);
provider.addEventListener("change", applyFilter);

for (const [index, header] of Array.from(table.tHead.rows[0].cells).entries()) {
  header.addEventListener("click", () => {
    const numeric = header.dataset.sort === "num";
    const direction = header.dataset.direction === "asc" ? -1 : 1;
    header.dataset.direction = direction === 1 ? "asc" : "desc";
    rows.sort((left, right) => {
      const a = left.cells[index].dataset.value || left.cells[index].innerText;
      const b = right.cells[index].dataset.value || right.cells[index].innerText;
      if (numeric) return (Number(a) - Number(b)) * direction;
      return a.localeCompare(b) * direction;
    });
    for (const row of rows) table.tBodies[0].appendChild(row);
    applyFilter();
  });
}
</script>
</html>
"""
    html_text = html_text.replace("__PROVIDER_OPTIONS__", provider_options)
    html_text = html_text.replace("__TABLE_ROWS__", table_rows)
    path.write_text(html_text, encoding="utf-8")


def _registry_html_row(row: dict[str, Any]) -> str:
    parse = _fmt_float(row["parse_coverage"]) if row["parse_coverage"] != "" else "n/a"
    data = f"{row['data_source']} ({row['frequency']}, {row['symbols']} symbols)"
    hash_text = str(row["reproducibility_hash"])
    search_text = " ".join(
        str(row[key])
        for key in (
            "entry_id",
            "scenario_id",
            "provider",
            "model_family",
            "prompt_mode",
            "risk_feedback_mode",
            "evidence_tags",
            "claim_class",
            "evidence_tier",
            "claim_scope",
            "data_source",
            "frequency",
            "reproducibility_hash",
        )
    ).lower()
    badges = (
        f'<span class="badge">{_h(row["reproducibility_status"])}</span>'
        f'<span class="badge redacted">{_h(row["redaction_status"])}</span>'
    )
    evidence = _evidence_badges(row.get("evidence_tags", ""))
    details = (
        "<details>"
        "<summary>Open</summary>"
        f"<div>Model redacted: {_h(row['model_redacted'])}</div>"
        f"<div>Claim scope: {_h(row.get('claim_scope', ''))}</div>"
        f"<div>Source: <code>{_h(row['source_file'])}</code></div>"
        f"<div>Hash: <code>{_h(hash_text)}</code></div>"
        "</details>"
    )
    return (
        f'<tr data-provider="{_h(row["provider"])}" data-search="{_h(search_text)}">'
        f'<td>{_h(row["entry_id"])}</td>'
        f'<td>{_h(row["scenario_id"])}</td>'
        f'<td>{_h(row["provider"])} / {_h(row["model_family"])}</td>'
        f'<td>{_h(row["prompt_mode"])}</td>'
        f'<td>{_h(row["risk_feedback_mode"])}</td>'
        f"<td>{evidence}</td>"
        f'<td>{_h(row["claim_class"])}</td>'
        f'<td>{_h(row["evidence_tier"])}</td>'
        f'<td data-value="{_h(parse)}">{_h(parse)}</td>'
        f'<td>{_h(data)}</td>'
        f'<td data-value="{float(row["total_return"]):.6f}">{float(row["total_return"]):.4f}</td>'
        f'<td data-value="{float(row["max_drawdown"]):.6f}">{float(row["max_drawdown"]):.4f}</td>'
        f'<td data-value="{float(row["fill_rate"]):.6f}">{float(row["fill_rate"]):.4f}</td>'
        f'<td data-value="{int(row["rejected_orders"])}">{_h(row["rejected_orders"])}</td>'
        f'<td data-value="{int(row["risk_edits"])}">{_h(row["risk_edits"])}</td>'
        f'<td data-value="{float(row["audit_coverage"]):.6f}">{float(row["audit_coverage"]):.4f}</td>'
        f"<td>{badges}</td>"
        f"<td>{details}</td>"
        "</tr>"
    )


def _require_keys(payload: Any, keys: tuple[str, ...], label: str, errors: list[str]) -> None:
    if not isinstance(payload, dict):
        errors.append(f"{label} must be an object")
        return
    for key in keys:
        if key not in payload:
            errors.append(f"{label}.{key} is required")


def _get_path(payload: dict[str, Any], dotted_path: str) -> Any:
    current: Any = payload
    for part in dotted_path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _fmt_float(value: Any) -> str:
    return f"{float(value):.4f}"


def _format_evidence_cell(value: Any) -> str:
    tags = str(value or "")
    if not tags:
        return ""
    return "<br>".join(f"`{tag}`" for tag in tags.split(";") if tag)


def _evidence_badges(value: Any) -> str:
    tags = [tag for tag in str(value or "").split(";") if tag]
    if not tags:
        return ""
    return "".join(f'<span class="badge redacted">{_h(tag)}</span>' for tag in tags)


def _h(value: Any) -> str:
    return html.escape(str(value), quote=True)
