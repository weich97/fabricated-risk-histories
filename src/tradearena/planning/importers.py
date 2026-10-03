from __future__ import annotations

import csv
from pathlib import Path

from tradearena.planning.domain import Holding


def load_holdings_csv(path: str | Path) -> tuple[Holding, ...]:
    """Load retail-planning holdings from a small CSV file.

    Required columns are `symbol`, `market_value`, and `quantity`; `cost_basis`
    is optional. Rows with blank symbols are skipped. Numeric errors are raised
    with row context so users can fix their fixture before planning.
    """

    holdings: list[Holding] = []
    csv_path = Path(path)
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"symbol", "market_value", "quantity"}
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"holdings CSV missing required columns: {', '.join(sorted(missing))}")
        for row_number, row in enumerate(reader, start=2):
            symbol = (row.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            try:
                market_value = float(row["market_value"])
                quantity = float(row["quantity"])
                cost_basis = _optional_float(row.get("cost_basis"))
            except ValueError as exc:
                raise ValueError(f"invalid numeric value in holdings CSV row {row_number}") from exc
            holdings.append(Holding(symbol=symbol, market_value=market_value, quantity=quantity, cost_basis=cost_basis))
    if not holdings:
        raise ValueError(f"holdings CSV contains no holdings: {csv_path}")
    return tuple(holdings)


def _optional_float(value: str | None) -> float | None:
    if value is None or not str(value).strip():
        return None
    return float(value)
