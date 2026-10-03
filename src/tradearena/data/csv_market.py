from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from tradearena.core.domain import Bar, FilingItem, MacroPoint, MarketSnapshot, NewsItem


@dataclass
class CsvMarketDataProvider:
    """Read normalized OHLCV CSV files as historical market snapshots."""

    data_dir: str | Path
    symbols: tuple[str, ...]
    start: str | None = None
    end: str | None = None
    frequency: str = "daily"
    max_periods: int | None = None
    window_offset: int = 0
    name: str = "csv-market"
    news_path: str | Path | None = None
    macro_path: str | Path | None = None
    filings_path: str | Path | None = None
    alternative_data_path: str | Path | None = None
    include_price_proxy_news: bool = True
    include_proxy_macro: bool = True

    def stream(self) -> list[MarketSnapshot]:
        per_symbol = {symbol: self._load_symbol(symbol) for symbol in self.symbols}
        common_dates = sorted(set.intersection(*(set(rows) for rows in per_symbol.values())))
        if self.start:
            start_dt = _parse_date(self.start)
            common_dates = [date for date in common_dates if date >= start_dt]
        if self.end:
            end_dt = _parse_date(self.end)
            common_dates = [date for date in common_dates if date <= end_dt]
        if self.frequency == "weekly":
            common_dates = common_dates[::5]
        elif self.frequency not in {"daily", "hourly", "5m", "15m", "intraday"}:
            raise ValueError(f"Unsupported frequency: {self.frequency}")
        if self.max_periods is not None:
            offset = max(0, int(self.window_offset))
            end_index = len(common_dates) - offset if offset else len(common_dates)
            start_index = max(0, end_index - self.max_periods)
            common_dates = common_dates[start_index:end_index]

        news_by_day = self._load_news()
        macro_by_day = self._load_macro()
        filings_by_day = self._load_filings()
        alt_by_day = self._load_alternative_data()

        snapshots: list[MarketSnapshot] = []
        for idx, timestamp in enumerate(common_dates):
            bars = {symbol: per_symbol[symbol][timestamp] for symbol in self.symbols}
            day = _date_key(timestamp)
            macro = self._macro_points(timestamp, idx, bars) if self.include_proxy_macro else []
            macro.extend(macro_by_day.get(day, []))
            news = self._pseudo_news(timestamp, idx, bars) if self.include_price_proxy_news else []
            news.extend(news_by_day.get(day, []))
            snapshots.append(
                MarketSnapshot(
                    timestamp=timestamp,
                    bars=bars,
                    news=tuple(news),
                    macro=tuple(macro),
                    filings=tuple(filings_by_day.get(day, [])),
                    alt_data=alt_by_day.get(day, {}),
                )
            )
        return snapshots

    def _load_symbol(self, symbol: str) -> dict[datetime, Bar]:
        path = _symbol_path(Path(self.data_dir), symbol, self.frequency)
        if not path.exists():
            raise FileNotFoundError(f"Missing historical CSV for {symbol}: {path}")

        rows: dict[datetime, Bar] = {}
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if not row.get("Date"):
                    continue
                timestamp = _parse_date(row["Date"])
                try:
                    rows[timestamp] = Bar(
                        symbol=symbol,
                        timestamp=timestamp,
                        open=float(row["Open"]),
                        high=float(row["High"]),
                        low=float(row["Low"]),
                        close=float(row["Close"]),
                        volume=float(row.get("Volume") or 0.0),
                    )
                except (TypeError, ValueError):
                    continue
        return rows

    def _macro_points(self, timestamp: datetime, idx: int, bars: dict[str, Bar]) -> list[MacroPoint]:
        if idx == 0:
            market_return = 0.0
        else:
            market_return = sum((bar.close / bar.open) - 1.0 for bar in bars.values()) / len(bars)
        volume_pressure = sum(bar.volume for bar in bars.values()) / max(1, len(bars))
        return [
            MacroPoint(timestamp=timestamp, name="realized_market_return", value=market_return),
            MacroPoint(timestamp=timestamp, name="average_volume", value=volume_pressure),
        ]

    def _pseudo_news(self, timestamp: datetime, idx: int, bars: dict[str, Bar]) -> list[NewsItem]:
        news: list[NewsItem] = []
        if idx % 4:
            return news
        for symbol, bar in bars.items():
            intraperiod_return = (bar.close / bar.open) - 1.0 if bar.open else 0.0
            if abs(intraperiod_return) < 0.015:
                continue
            sentiment = max(-1.0, min(1.0, intraperiod_return * 12.0))
            direction = "positive" if sentiment > 0 else "negative"
            news.append(
                NewsItem(
                    timestamp=timestamp,
                    source="historical-price-proxy",
                    title=f"{symbol} {direction} market move",
                    body=f"Real historical OHLCV move used as a deterministic event proxy: {intraperiod_return:.3f}.",
                    sentiment=sentiment,
                    symbols=(symbol,),
                )
            )
        return news

    def _load_news(self) -> dict[str, list[NewsItem]]:
        path = _optional_sidecar_path(Path(self.data_dir), self.news_path, "news.csv")
        if path is None:
            return {}
        rows: dict[str, list[NewsItem]] = {}
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                date_value = row.get("Date") or row.get("Timestamp") or ""
                if not date_value:
                    continue
                timestamp = _parse_date(date_value)
                item = NewsItem(
                    timestamp=timestamp,
                    source=row.get("Source") or row.get("source") or "csv-news",
                    title=row.get("Title") or row.get("title") or "",
                    body=row.get("Body") or row.get("body") or "",
                    sentiment=_parse_float(row.get("Sentiment") or row.get("sentiment"), 0.0),
                    symbols=_parse_symbols(row.get("Symbols") or row.get("Symbol") or row.get("symbol")),
                )
                rows.setdefault(_date_key(timestamp), []).append(item)
        return rows

    def _load_macro(self) -> dict[str, list[MacroPoint]]:
        path = _optional_sidecar_path(Path(self.data_dir), self.macro_path, "macro.csv")
        if path is None:
            return {}
        rows: dict[str, list[MacroPoint]] = {}
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                date_value = row.get("Date") or row.get("Timestamp") or ""
                if not date_value:
                    continue
                timestamp = _parse_date(date_value)
                point = MacroPoint(
                    timestamp=timestamp,
                    name=row.get("Name") or row.get("name") or row.get("Series") or "macro",
                    value=_parse_float(row.get("Value") or row.get("value"), 0.0),
                    unit=row.get("Unit") or row.get("unit") or "",
                )
                rows.setdefault(_date_key(timestamp), []).append(point)
        return rows

    def _load_filings(self) -> dict[str, list[FilingItem]]:
        path = _optional_sidecar_path(Path(self.data_dir), self.filings_path, "filings.csv")
        if path is None:
            return {}
        rows: dict[str, list[FilingItem]] = {}
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                date_value = row.get("Date") or row.get("Timestamp") or ""
                if not date_value:
                    continue
                timestamp = _parse_date(date_value)
                item = FilingItem(
                    timestamp=timestamp,
                    source=row.get("Source") or row.get("source") or "csv-filings",
                    form_type=row.get("Form") or row.get("FormType") or row.get("form_type") or "",
                    title=row.get("Title") or row.get("title") or "",
                    body=row.get("Body") or row.get("body") or "",
                    sentiment=_parse_float(row.get("Sentiment") or row.get("sentiment"), 0.0),
                    symbols=_parse_symbols(row.get("Symbols") or row.get("Symbol") or row.get("symbol")),
                    accession=row.get("Accession") or row.get("accession") or "",
                )
                rows.setdefault(_date_key(timestamp), []).append(item)
        return rows

    def _load_alternative_data(self) -> dict[str, dict[str, Any]]:
        path = _optional_sidecar_path(Path(self.data_dir), self.alternative_data_path, "alternative_data.csv")
        if path is None:
            return {}
        rows: dict[str, dict[str, Any]] = {}
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                date_value = row.get("Date") or row.get("Timestamp") or ""
                if not date_value:
                    continue
                timestamp = _parse_date(date_value)
                symbol = (row.get("Symbol") or row.get("symbol") or "__market__").strip() or "__market__"
                name = (row.get("Name") or row.get("name") or "feature").strip() or "feature"
                value = _parse_value(row.get("Value") or row.get("value"))
                payload = {
                    "value": value,
                    "unit": row.get("Unit") or row.get("unit") or "",
                    "source": row.get("Source") or row.get("source") or "csv-alternative-data",
                }
                day_payload = rows.setdefault(_date_key(timestamp), {})
                symbol_payload = day_payload.setdefault(symbol, {})
                symbol_payload[name] = payload
        return rows


def _safe_symbol(symbol: str) -> str:
    return symbol.replace("^", "").replace("/", "-")


def _symbol_path(data_dir: Path, symbol: str, frequency: str) -> Path:
    safe = _safe_symbol(symbol)
    candidates = (
        (f"{safe}_Hourly_1h.csv", f"{safe}_Hourly.csv", f"{safe}_1h.csv")
        if frequency == "hourly"
        else (f"{safe}_5m.csv", f"{safe}_Intraday_5m.csv", f"{safe}_Hourly_1h.csv")
        if frequency in {"5m", "intraday"}
        else (f"{safe}_15m.csv", f"{safe}_Intraday_15m.csv", f"{safe}_Hourly_1h.csv")
        if frequency == "15m"
        else (f"{safe}_Daily_2021_2026.csv", f"{safe}_Daily.csv")
    )
    for filename in candidates:
        path = data_dir / filename
        if path.exists():
            return path
    return data_dir / candidates[0]


def _parse_date(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def _date_key(timestamp: datetime) -> str:
    return timestamp.date().isoformat()


def _optional_sidecar_path(data_dir: Path, configured: str | Path | None, default_name: str) -> Path | None:
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            path = data_dir / path
        return path if path.exists() else None
    path = data_dir / default_name
    return path if path.exists() else None


def _parse_symbols(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(symbol.strip() for symbol in value.replace(";", ",").split(",") if symbol.strip())


def _parse_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_value(value: object) -> float | str:
    try:
        return float(value)
    except (TypeError, ValueError):
        return "" if value is None else str(value)
