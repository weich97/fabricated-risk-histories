from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast


@dataclass(frozen=True)
class ExecutionCalibrationConfig:
    """Inputs for OHLCV-based execution-model diagnostics.

    OHLCV bars do not identify quoted spread, queue position, or broker latency.
    The config therefore keeps those as explicit assumptions and estimates only
    bar-observable quantities such as range and dollar volume.
    """

    commission_bps: float = 1.0
    spread_bps: float | None = None
    participation_rate: float = 0.05
    latency_steps: int = 1
    market_impact: float = 0.15
    base_slippage_range_multiplier: float = 0.02


@dataclass(frozen=True)
class QuoteFillCalibrationConfig:
    """Inputs for quote/fill execution calibration."""

    commission_bps_default: float = 0.0
    volatility_multiplier: float = 0.1
    fallback_participation_rate: float = 0.05
    min_base_slippage_bps: float = 0.0
    max_base_slippage_bps: float = 50.0
    max_market_impact: float = 2.0


def discover_ohlcv_files(data_dir: str | Path, pattern: str = "*.csv") -> list[Path]:
    return sorted(path for path in Path(data_dir).glob(pattern) if path.is_file())


def summarize_execution_calibration(
    files: Iterable[str | Path],
    config: ExecutionCalibrationConfig | None = None,
) -> dict[str, Any]:
    config = config or ExecutionCalibrationConfig()
    symbols: set[str] = set()
    ranges_bps: list[float] = []
    dollar_volumes: list[float] = []
    volumes: list[float] = []
    closes: list[float] = []
    row_count = 0

    for file in files:
        path = Path(file)
        symbol = _symbol_from_filename(path)
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or not {"High", "Low", "Close"}.issubset(reader.fieldnames):
                continue
            for row in reader:
                high = _float(row.get("High"))
                low = _float(row.get("Low"))
                close = _float(row.get("Close"))
                volume = max(0.0, _float(row.get("Volume")))
                if close <= 0 or high <= 0 or low <= 0 or high < low:
                    continue
                symbols.add(symbol)
                row_count += 1
                range_bps = (high - low) / close * 10_000.0
                ranges_bps.append(range_bps)
                volumes.append(volume)
                closes.append(close)
                dollar_volumes.append(close * volume)

    if row_count == 0:
        raise ValueError("No valid OHLCV rows found for execution calibration.")

    median_range_bps = _percentile(ranges_bps, 0.50)
    p90_range_bps = _percentile(ranges_bps, 0.90)
    suggested_base_slippage_bps = max(0.5, min(25.0, median_range_bps * config.base_slippage_range_multiplier))
    assumed_spread_bps = 0.0 if config.spread_bps is None else max(0.0, config.spread_bps)
    impact_bps_at_cap = config.market_impact * config.participation_rate * 10_000.0
    median_volatility_component_bps = 0.1 * median_range_bps
    expected_buy_slippage_bps = (
        assumed_spread_bps / 2.0
        + suggested_base_slippage_bps
        + impact_bps_at_cap
        + median_volatility_component_bps
    )

    return {
        "schema": "tradearena_execution_calibration_v1",
        "data": {
            "symbols": sorted(symbols),
            "symbol_count": len(symbols),
            "row_count": row_count,
            "median_close": round(_percentile(closes, 0.50), 6),
            "median_volume": round(_percentile(volumes, 0.50), 6),
            "median_dollar_volume": round(_percentile(dollar_volumes, 0.50), 6),
            "median_intrabar_range_bps": round(median_range_bps, 6),
            "p90_intrabar_range_bps": round(p90_range_bps, 6),
        },
        "assumptions": {
            "commission_bps": config.commission_bps,
            "spread_bps": config.spread_bps,
            "participation_rate": config.participation_rate,
            "latency_steps": config.latency_steps,
            "market_impact": config.market_impact,
            "base_slippage_range_multiplier": config.base_slippage_range_multiplier,
        },
        "suggested_simulator_config": {
            "commission_bps": config.commission_bps,
            "base_slippage_bps": round(suggested_base_slippage_bps, 6),
            "spread_bps": None if config.spread_bps is None else assumed_spread_bps,
            "participation_rate": config.participation_rate,
            "latency_steps": config.latency_steps,
            "market_impact": config.market_impact,
        },
        "diagnostics": {
            "median_volatility_component_bps": round(median_volatility_component_bps, 6),
            "impact_bps_at_participation_cap": round(impact_bps_at_cap, 6),
            "expected_buy_slippage_bps_at_median_range": round(expected_buy_slippage_bps, 6),
            "spread_status": "assumed_zero_or_external" if config.spread_bps is None else "user_supplied",
            "identification_warning": (
                "OHLCV bars do not contain bid-ask quotes, order-book depth, queue position, "
                "broker fees, order timestamps, or realized execution shortfall. Treat this as "
                "a bar-level diagnostic, not broker-grade calibration."
            ),
        },
    }


def write_calibration_json(summary: dict[str, Any], path: str | Path) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def write_calibration_markdown(summary: dict[str, Any], path: str | Path) -> None:
    data = summary["data"]
    config = summary["suggested_simulator_config"]
    diagnostics = summary["diagnostics"]
    spread_value = "not supplied" if config["spread_bps"] is None else str(config["spread_bps"])
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Execution Calibration Diagnostic",
        "",
        "This diagnostic is computed from OHLCV bars. It estimates bar-observable range and volume quantities, while spread, fees, market impact, and latency remain explicit assumptions unless quote, broker, or order-log data are supplied.",
        "",
        "## Data Summary",
        "",
        f"- Symbols: {data['symbol_count']}",
        f"- Rows: {data['row_count']}",
        f"- Median close: {data['median_close']}",
        f"- Median volume: {data['median_volume']}",
        f"- Median dollar volume: {data['median_dollar_volume']}",
        f"- Median intrabar range: {data['median_intrabar_range_bps']} bps",
        f"- P90 intrabar range: {data['p90_intrabar_range_bps']} bps",
        "",
        "## Suggested Simulator Configuration",
        "",
        "| Parameter | Value | Status |",
        "| --- | ---: | --- |",
        f"| `commission_bps` | {config['commission_bps']} | user/broker assumption |",
        f"| `base_slippage_bps` | {config['base_slippage_bps']} | OHLCV range proxy |",
        f"| `spread_bps` | {spread_value} | quote data required if not supplied |",
        f"| `participation_rate` | {config['participation_rate']} | policy cap |",
        f"| `latency_steps` | {config['latency_steps']} | system/broker log assumption |",
        f"| `market_impact` | {config['market_impact']} | execution-log fit required |",
        "",
        "## Model-Implied Slippage Components",
        "",
        f"- Median volatility component: {diagnostics['median_volatility_component_bps']} bps",
        f"- Impact at participation cap: {diagnostics['impact_bps_at_participation_cap']} bps",
        f"- Expected buy slippage at median range: {diagnostics['expected_buy_slippage_bps_at_median_range']} bps",
        "",
        "## Identification Warning",
        "",
        diagnostics["identification_warning"],
        "",
    ]
    output.write_text("\n".join(lines), encoding="utf-8")


def summarize_quote_fill_calibration(
    quote_file: str | Path,
    fill_file: str | Path,
    config: QuoteFillCalibrationConfig | None = None,
) -> dict[str, Any]:
    """Fit a compact execution model from top-of-book quotes and realized fills.

    The fitter aligns each fill to the latest quote at or before the fill
    timestamp, computes side-adjusted implementation shortfall, and estimates a
    linear residual model:

    residual_bps ~= base_slippage_bps + market_impact * participation * 10000.
    """

    config = config or QuoteFillCalibrationConfig()
    quotes = _read_quotes(Path(quote_file))
    fills = _read_fills(Path(fill_file))
    rows: list[dict[str, Any]] = []
    for fill in fills:
        quote = _latest_quote(quotes.get(str(fill["symbol"]), []), fill["timestamp"])
        if quote is None:
            continue
        row = _quote_fill_row(fill, quote, config)
        if row:
            rows.append(row)
    if not rows:
        raise ValueError("No fills could be aligned to quotes for calibration.")

    spread_values = [float(row["spread_bps"]) for row in rows]
    residual_targets = [float(row["fit_target_bps"]) for row in rows]
    participation_bps = [float(row["participation_bps"]) for row in rows]
    base_slippage_bps, market_impact = _fit_linear_cost(
        participation_bps,
        residual_targets,
        min_base=config.min_base_slippage_bps,
        max_base=config.max_base_slippage_bps,
        max_market_impact=config.max_market_impact,
    )
    modeled_rows = []
    residuals = []
    for row in rows:
        modeled_shortfall = (
            float(row["half_spread_bps"])
            + base_slippage_bps
            + market_impact * float(row["participation_bps"])
            + float(row["volatility_bps"])
            + float(row["commission_bps"])
        )
        residual = float(row["observed_shortfall_bps"]) - modeled_shortfall
        residuals.append(residual)
        modeled = dict(row)
        modeled["modeled_shortfall_bps"] = round(modeled_shortfall, 6)
        modeled["residual_bps"] = round(residual, 6)
        modeled_rows.append(modeled)

    latencies = [float(row["latency_seconds"]) for row in rows if row.get("latency_seconds") is not None]
    quote_event_lags = [
        float(row["quote_event_lag_seconds"]) for row in rows if row.get("quote_event_lag_seconds") is not None
    ]
    quote_staleness = [
        float(row["quote_staleness_seconds"]) for row in rows if row.get("quote_staleness_seconds") is not None
    ]
    shortfalls = [float(row["observed_shortfall_bps"]) for row in rows]
    participations = [float(row["participation"]) for row in rows]
    participation_cap = max(0.0001, _percentile(participations, 0.90))
    participation_residuals = [max(0.0, value - participation_cap) for value in participations]
    stress_rows, stress_residuals = _stress_only_residuals(rows, config)
    stress_abs = [abs(value) for value in stress_residuals]
    calibrated_abs = [abs(value) for value in residuals]
    return {
        "schema": "tradearena_quote_fill_calibration_v1",
        "input": {
            "quote_file": str(Path(quote_file)).replace("\\", "/"),
            "fill_file": str(Path(fill_file)).replace("\\", "/"),
            "quote_rows": sum(len(items) for items in quotes.values()),
            "fill_rows": len(fills),
            "aligned_rows": len(rows),
            "symbols": sorted({str(row["symbol"]) for row in rows}),
        },
        "fitted_parameters": {
            "commission_bps_default": config.commission_bps_default,
            "spread_bps_median": round(_percentile(spread_values, 0.50), 6),
            "spread_bps_p90": round(_percentile(spread_values, 0.90), 6),
            "spread_bps_p99": round(_percentile(spread_values, 0.99), 6),
            "base_slippage_bps": round(base_slippage_bps, 6),
            "market_impact": round(market_impact, 6),
            "participation_rate_p90": round(participation_cap, 8),
            "latency_seconds_median": round(_percentile(latencies, 0.50), 6) if latencies else None,
            "latency_seconds_p90": round(_percentile(latencies, 0.90), 6) if latencies else None,
            "quote_event_lag_seconds_median": round(_percentile(quote_event_lags, 0.50), 6)
            if quote_event_lags
            else None,
            "quote_event_lag_seconds_p90": round(_percentile(quote_event_lags, 0.90), 6)
            if quote_event_lags
            else None,
            "quote_staleness_seconds_median": round(_percentile(quote_staleness, 0.50), 6) if quote_staleness else None,
            "quote_staleness_seconds_p90": round(_percentile(quote_staleness, 0.90), 6) if quote_staleness else None,
        },
        "fit_quality": {
            "residual_mean_bps": round(mean(residuals), 6),
            "residual_mae_bps": round(mean(abs(value) for value in residuals), 6),
            "residual_p90_abs_bps": round(_percentile(calibrated_abs, 0.90), 6),
            "residual_max_abs_bps": round(max(abs(value) for value in residuals), 6),
            "median_shortfall_bps": round(_percentile(shortfalls, 0.50), 6),
            "p90_shortfall_bps": round(_percentile(shortfalls, 0.90), 6),
            "p99_shortfall_bps": round(_percentile(shortfalls, 0.99), 6),
            "participation_cap_residual_mean": round(mean(participation_residuals), 8),
            "participation_cap_residual_max": round(max(participation_residuals), 8),
        },
        "stress_only_comparison": {
            "default_config": {
                "commission_bps": config.commission_bps_default,
                "base_slippage_bps": 2.0,
                "spread_bps": 0.0,
                "market_impact": 0.15,
            },
            "median_modeled_shortfall_bps": round(
                _percentile([float(row["stress_only_shortfall_bps"]) for row in stress_rows], 0.50), 6
            ),
            "residual_mae_bps": round(mean(stress_abs), 6),
            "residual_p90_abs_bps": round(_percentile(stress_abs, 0.90), 6),
            "calibrated_residual_mae_bps": round(mean(calibrated_abs), 6),
            "mae_reduction_vs_stress": round(mean(stress_abs) - mean(calibrated_abs), 6),
        },
        "suggested_simulator_config": {
            "commission_bps": config.commission_bps_default,
            "base_slippage_bps": round(base_slippage_bps, 6),
            "spread_bps": round(_percentile(spread_values, 0.50), 6),
            "participation_rate": round(participation_cap, 8),
            "latency_steps": 1,
            "market_impact": round(market_impact, 6),
        },
        "rows": modeled_rows,
        "interpretation": (
            "This fit uses observed top-of-book spread and realized fills. It is stronger than OHLCV "
            "diagnostics, but it still depends on the fill sample, venue, order type, and reference-price definition."
        ),
    }


def write_quote_fill_calibration_markdown(summary: dict[str, Any], path: str | Path) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    params = summary["fitted_parameters"]
    quality = summary["fit_quality"]
    suggested = summary["suggested_simulator_config"]
    stress = summary["stress_only_comparison"]
    lines = [
        "# Quote/Fill Execution Calibration",
        "",
        "This report fits TreLLM's compact execution equation from top-of-book quotes and realized fills.",
        "",
        "## Input Coverage",
        "",
        f"- Symbols: {', '.join(summary['input']['symbols'])}",
        f"- Quote rows: {summary['input']['quote_rows']}",
        f"- Fill rows: {summary['input']['fill_rows']}",
        f"- Aligned rows: {summary['input']['aligned_rows']}",
        f"- Quote file: `{summary['input']['quote_file']}`",
        f"- Fill file: `{summary['input']['fill_file']}`",
        "",
        "## Fitted Parameters",
        "",
        "| Parameter | Value |",
        "| --- | ---: |",
        f"| Median spread | {params['spread_bps_median']} bps |",
        f"| P90 spread | {params['spread_bps_p90']} bps |",
        f"| P99 spread | {params['spread_bps_p99']} bps |",
        f"| Base slippage | {params['base_slippage_bps']} bps |",
        f"| Market impact coefficient | {params['market_impact']} |",
        f"| P90 participation | {params['participation_rate_p90']} |",
        f"| Median latency | {_format_optional(params['latency_seconds_median'], 's')} |",
        f"| P90 latency | {_format_optional(params['latency_seconds_p90'], 's')} |",
        f"| Median quote event lag | {_format_optional(params['quote_event_lag_seconds_median'], 's')} |",
        f"| P90 quote event lag | {_format_optional(params['quote_event_lag_seconds_p90'], 's')} |",
        f"| Median quote staleness at fill | {_format_optional(params['quote_staleness_seconds_median'], 's')} |",
        f"| P90 quote staleness at fill | {_format_optional(params['quote_staleness_seconds_p90'], 's')} |",
        "",
        "## Fit Quality",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Residual mean | {quality['residual_mean_bps']} bps |",
        f"| Residual MAE | {quality['residual_mae_bps']} bps |",
        f"| Residual P90 abs | {quality['residual_p90_abs_bps']} bps |",
        f"| Residual max abs | {quality['residual_max_abs_bps']} bps |",
        f"| Median shortfall | {quality['median_shortfall_bps']} bps |",
        f"| P90 shortfall | {quality['p90_shortfall_bps']} bps |",
        f"| P99 shortfall | {quality['p99_shortfall_bps']} bps |",
        f"| Mean participation cap residual | {quality['participation_cap_residual_mean']} |",
        f"| Max participation cap residual | {quality['participation_cap_residual_max']} |",
        "",
        "## Calibrated vs Stress-Only Replay Error",
        "",
        "| Model | Residual MAE | Residual P90 abs |",
        "| --- | ---: | ---: |",
        f"| Default stress-only | {stress['residual_mae_bps']} bps | {stress['residual_p90_abs_bps']} bps |",
        f"| Quote/fill calibrated | {stress['calibrated_residual_mae_bps']} bps | {quality['residual_p90_abs_bps']} bps |",
        "",
        f"MAE reduction versus the default stress-only model: {stress['mae_reduction_vs_stress']} bps.",
        "",
        "## Suggested Simulator Configuration",
        "",
        "| Parameter | Value |",
        "| --- | ---: |",
        f"| `commission_bps` | {suggested['commission_bps']} |",
        f"| `base_slippage_bps` | {suggested['base_slippage_bps']} |",
        f"| `spread_bps` | {suggested['spread_bps']} |",
        f"| `participation_rate` | {suggested['participation_rate']} |",
        f"| `latency_steps` | {suggested['latency_steps']} |",
        f"| `market_impact` | {suggested['market_impact']} |",
        "",
        "## Interpretation Boundary",
        "",
        summary["interpretation"],
        "",
    ]
    output.write_text("\n".join(lines), encoding="utf-8")


def _symbol_from_filename(path: Path) -> str:
    stem = path.stem
    for suffix in ("_Daily_2021_2026", "_Daily", "_Hourly_1h", "_Hourly", "_1h", "_5m", "_15m"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def _float(value: object) -> float:
    try:
        return float(cast(Any, value))
    except (TypeError, ValueError):
        return 0.0


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = max(0.0, min(1.0, quantile)) * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def mean(values: Iterable[float]) -> float:
    numbers = [float(value) for value in values]
    return sum(numbers) / len(numbers) if numbers else 0.0


def _read_quotes(path: Path) -> dict[str, list[dict[str, Any]]]:
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"timestamp", "symbol", "bid", "ask"}
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"quote CSV missing required columns: {', '.join(sorted(missing))}")
        for row in reader:
            timestamp = _parse_timestamp(row.get("timestamp"))
            bid = _float(row.get("bid"))
            ask = _float(row.get("ask"))
            if timestamp is None or bid <= 0 or ask <= 0 or ask < bid:
                continue
            symbol = str(row.get("symbol", "")).strip()
            by_symbol.setdefault(symbol, []).append({"timestamp": timestamp, "symbol": symbol, "bid": bid, "ask": ask})
            quote = by_symbol[symbol][-1]
            quote["bid_qty"] = _float(row.get("bid_qty") or row.get("best_bid_qty"))
            quote["ask_qty"] = _float(row.get("ask_qty") or row.get("best_ask_qty"))
            quote["transaction_time"] = _parse_timestamp(row.get("transaction_time"))
            quote["event_time"] = _parse_timestamp(row.get("event_time"))
            quote["update_id"] = str(row.get("update_id", "")).strip()
    for symbol in by_symbol:
        by_symbol[symbol].sort(key=lambda item: item["timestamp"])
    return by_symbol


def _read_fills(path: Path) -> list[dict[str, Any]]:
    fills: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"timestamp", "symbol", "side", "quantity", "fill_price"}
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"fill CSV missing required columns: {', '.join(sorted(missing))}")
        for row in reader:
            timestamp = _parse_timestamp(row.get("timestamp"))
            side = str(row.get("side", "")).strip().lower()
            quantity = _float(row.get("quantity"))
            fill_price = _float(row.get("fill_price"))
            if timestamp is None or side not in {"buy", "sell"} or quantity <= 0 or fill_price <= 0:
                continue
            fills.append(
                {
                    **row,
                    "timestamp": timestamp,
                    "symbol": str(row.get("symbol", "")).strip(),
                    "side": side,
                    "quantity": quantity,
                    "fill_price": fill_price,
                }
            )
    return fills


def _latest_quote(quotes: list[dict[str, Any]], timestamp: datetime) -> dict[str, Any] | None:
    latest = None
    for quote in quotes:
        if quote["timestamp"] <= timestamp:
            latest = quote
        else:
            break
    return latest


def _quote_fill_row(
    fill: dict[str, Any],
    quote: dict[str, Any],
    config: QuoteFillCalibrationConfig,
) -> dict[str, Any] | None:
    bid = float(quote["bid"])
    ask = float(quote["ask"])
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None
    reference_price = _float(fill.get("reference_price")) or mid
    fill_price = float(fill["fill_price"])
    quantity = float(fill["quantity"])
    direction = 1.0 if fill["side"] == "buy" else -1.0
    observed_shortfall_bps = direction * (fill_price - reference_price) / reference_price * 10_000.0
    spread_bps = (ask - bid) / mid * 10_000.0
    half_spread_bps = spread_bps / 2.0
    bar_volume = max(0.0, _float(fill.get("bar_volume")))
    participation = quantity / max(1.0, bar_volume) if bar_volume > 0 else config.fallback_participation_rate
    high = _float(fill.get("bar_high"))
    low = _float(fill.get("bar_low"))
    close = _float(fill.get("bar_close")) or reference_price
    intrabar_range_bps = (high - low) / close * 10_000.0 if close > 0 and high >= low else 0.0
    volatility_bps = config.volatility_multiplier * intrabar_range_bps
    commission_bps = _commission_bps(fill, quantity, fill_price, config)
    fit_target = observed_shortfall_bps - half_spread_bps - volatility_bps - commission_bps
    return {
        "timestamp": fill["timestamp"].isoformat(),
        "symbol": fill["symbol"],
        "side": fill["side"],
        "quantity": quantity,
        "reference_price": round(reference_price, 8),
        "fill_price": round(fill_price, 8),
        "bid": bid,
        "ask": ask,
        "spread_bps": round(spread_bps, 6),
        "half_spread_bps": round(half_spread_bps, 6),
        "observed_shortfall_bps": round(observed_shortfall_bps, 6),
        "volatility_bps": round(volatility_bps, 6),
        "commission_bps": round(commission_bps, 6),
        "participation": round(participation, 8),
        "participation_bps": participation * 10_000.0,
        "fit_target_bps": round(fit_target, 6),
        "latency_seconds": _latency_seconds(fill.get("submitted_at"), fill.get("filled_at")),
        "quote_event_lag_seconds": _time_delta_seconds(quote.get("transaction_time"), quote.get("event_time")),
        "quote_staleness_seconds": _time_delta_seconds(quote.get("timestamp"), fill["timestamp"]),
    }


def _commission_bps(
    fill: dict[str, Any],
    quantity: float,
    fill_price: float,
    config: QuoteFillCalibrationConfig,
) -> float:
    explicit_bps = _float(fill.get("commission_bps"))
    if explicit_bps > 0:
        return explicit_bps
    commission = _float(fill.get("commission"))
    notional = quantity * fill_price
    if commission > 0 and notional > 0:
        return commission / notional * 10_000.0
    return config.commission_bps_default


def _fit_linear_cost(
    participation_bps: list[float],
    targets: list[float],
    *,
    min_base: float,
    max_base: float,
    max_market_impact: float,
) -> tuple[float, float]:
    if len(targets) < 2 or len({round(value, 8) for value in participation_bps}) < 2:
        return max(min_base, min(max_base, mean(targets))), 0.0
    x_bar = mean(participation_bps)
    y_bar = mean(targets)
    denominator = sum((x - x_bar) ** 2 for x in participation_bps)
    slope = 0.0 if denominator == 0 else sum((x - x_bar) * (y - y_bar) for x, y in zip(participation_bps, targets)) / denominator
    intercept = y_bar - slope * x_bar
    return max(min_base, min(max_base, intercept)), max(0.0, min(max_market_impact, slope))


def _stress_only_residuals(
    rows: list[dict[str, Any]], config: QuoteFillCalibrationConfig
) -> tuple[list[dict[str, Any]], list[float]]:
    modeled_rows: list[dict[str, Any]] = []
    residuals: list[float] = []
    for row in rows:
        modeled_shortfall = (
            config.commission_bps_default
            + 2.0
            + 0.15 * float(row["participation_bps"])
            + float(row["volatility_bps"])
        )
        residual = float(row["observed_shortfall_bps"]) - modeled_shortfall
        modeled = dict(row)
        modeled["stress_only_shortfall_bps"] = round(modeled_shortfall, 6)
        modeled["stress_only_residual_bps"] = round(residual, 6)
        modeled_rows.append(modeled)
        residuals.append(residual)
    return modeled_rows, residuals


def _parse_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _latency_seconds(submitted_at: object, filled_at: object) -> float | None:
    submitted = _parse_timestamp(submitted_at)
    filled = _parse_timestamp(filled_at)
    if submitted is None or filled is None:
        return None
    seconds = (filled - submitted).total_seconds()
    return round(seconds, 6) if seconds >= 0 else None


def _time_delta_seconds(start: object, end: object) -> float | None:
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        return None
    seconds = (end - start).total_seconds()
    return round(seconds, 6) if seconds >= 0 else None


def _format_optional(value: object, suffix: str) -> str:
    if value is None:
        return "not supplied"
    return f"{value} {suffix}"
