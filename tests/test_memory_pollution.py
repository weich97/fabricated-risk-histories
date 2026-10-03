import pytest

from tradearena.factory import build_default_system
from tradearena.memory import (
    InMemoryResearchMemory,
    JournalVerifiedMemory,
    PollutedResearchMemory,
    PollutionConfig,
)
from tradearena.memory.pollution import _risk_signature


def _seed_steps(memory: InMemoryResearchMemory, count: int = 6) -> None:
    for index in range(count):
        memory.record(
            "step",
            {
                "equity": 100_000.0 + 100.0 * index,
                "execution_report": {"rejected_orders": 0},
                "risk_violations": [],
            },
        )


def test_zero_dose_returns_events_unchanged():
    base = InMemoryResearchMemory()
    _seed_steps(base)
    polluted = PollutedResearchMemory(base=base, config=PollutionConfig(kind="fake_rejections", dose=0.0))

    events = polluted.recent("step", 5)

    assert events == base.recent("step", 5)
    assert not any(event.get("injected") for event in events)


def test_full_dose_fake_rejections_marks_every_event():
    base = InMemoryResearchMemory()
    _seed_steps(base)
    polluted = PollutedResearchMemory(base=base, config=PollutionConfig(kind="fake_rejections", dose=1.0))

    events = polluted.recent("step", 5)

    assert all(event["injected"] for event in events)
    assert all(event["payload"]["execution_report"]["rejected_orders"] >= 2 for event in events)
    # The journal itself stays clean.
    assert all("injected" not in event for event in base.events)
    assert all(event["payload"]["execution_report"]["rejected_orders"] == 0 for event in base.events)


def test_partial_dose_is_deterministic_for_fixed_journal_state():
    base = InMemoryResearchMemory()
    _seed_steps(base)
    polluted = PollutedResearchMemory(base=base, config=PollutionConfig(kind="fake_violations", dose=0.4, seed=11))

    first = polluted.recent("step", 5)
    second = polluted.recent("step", 5)

    assert first == second
    injected = [event for event in first if event.get("injected")]
    assert len(injected) == 2  # round(0.4 * 5)
    assert all(event["payload"]["risk_violations"][-1]["rule"] == "fabricated_max_abs_weight" for event in injected)


def test_missing_equity_removes_the_mark():
    base = InMemoryResearchMemory()
    _seed_steps(base)
    polluted = PollutedResearchMemory(base=base, config=PollutionConfig(kind="missing_equity", dose=1.0))

    events = polluted.recent("step", 5)

    assert all("equity" not in event["payload"] for event in events)


def test_loss_streak_rewrites_recent_equity_downward():
    base = InMemoryResearchMemory()
    _seed_steps(base)
    polluted = PollutedResearchMemory(
        base=base,
        config=PollutionConfig(kind="loss_streak", dose=0.0, loss_streak_length=3, loss_step_return=-0.05),
    )

    events = polluted.recent("step", 5)

    streak = events[-3:]
    equities = [event["payload"]["equity"] for event in streak]
    assert all(event["injected"] for event in streak)
    assert equities[0] > equities[1] > equities[2]
    assert not any(event.get("injected") for event in events[:-3])


def test_non_step_events_and_other_types_pass_through():
    base = InMemoryResearchMemory()
    base.record("thesis", {"symbol": "SYN", "text": "hold"})
    polluted = PollutedResearchMemory(base=base, config=PollutionConfig(kind="fake_rejections", dose=1.0))

    assert polluted.recent("thesis", 5) == base.recent("thesis", 5)
    assert polluted.theses == {"SYN": "hold"}


def test_invalid_config_rejected():
    with pytest.raises(ValueError):
        PollutionConfig(kind="unknown", dose=0.5)
    with pytest.raises(ValueError):
        PollutionConfig(kind="fake_rejections", dose=1.5)


def test_pollution_reaches_llm_risk_feedback_path():
    from tradearena.agents.llm import _recent_risk_feedback

    base = InMemoryResearchMemory()
    _seed_steps(base)
    polluted = PollutedResearchMemory(base=base, config=PollutionConfig(kind="fake_rejections", dose=1.0))

    clean_feedback = _recent_risk_feedback(base)
    polluted_feedback = _recent_risk_feedback(polluted)

    assert all(item["rejected_orders"] == 0 for item in clean_feedback)
    assert all(item["rejected_orders"] >= 2 for item in polluted_feedback)


def test_pollution_sweep_writes_agent_and_sample_columns(tmp_path):
    import csv
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "run_memory_pollution_sweep.py"
    spec = importlib.util.spec_from_file_location("run_memory_pollution_sweep", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    exit_code = module.main(
        [
            "--kinds", "fake_rejections",
            "--doses", "0,0.5",
            "--decays", "0.85",
            "--risks", "max-position",
            "--seeds", "3,5",
            "--periods", "15",
            "--output-dir", str(tmp_path),
        ]
    )

    assert exit_code == 0
    rows = list(csv.DictReader((tmp_path / "memory_pollution_runs.csv").open(encoding="utf-8")))
    assert len(rows) == 4
    assert all(row["agent"] == "memory-aware" for row in rows)
    assert all(row["market_regime"] == "bullish" for row in rows)
    assert all(row["sample"] == "0" for row in rows)
    assert all(row["hold_ratio"] != "" for row in rows)
    # Resume skips everything already checkpointed.
    assert module.main(
        [
            "--kinds", "fake_rejections",
            "--doses", "0,0.5",
            "--decays", "0.85",
            "--risks", "max-position",
            "--seeds", "3,5",
            "--periods", "15",
            "--output-dir", str(tmp_path),
        ]
    ) == 0
    rows_after = list(csv.DictReader((tmp_path / "memory_pollution_runs.csv").open(encoding="utf-8")))
    assert len(rows_after) == 4


def test_memory_pollution_sweep_records_and_isolates_market_regime(tmp_path):
    import csv
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "run_memory_pollution_sweep.py"
    spec = importlib.util.spec_from_file_location("run_memory_pollution_sweep_regime", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    common = [
        "--kinds", "fake_violations",
        "--doses", "0",
        "--decays", "0.85",
        "--risks", "none",
        "--seeds", "1",
        "--periods", "4",
        "--output-dir", str(tmp_path),
    ]
    assert module.main([*common, "--market-regime", "sideways"]) == 0
    rows = list(csv.DictReader((tmp_path / "memory_pollution_runs.csv").open(encoding="utf-8")))
    assert [row["market_regime"] for row in rows] == ["sideways"]

    with pytest.raises(SystemExit, match="already contains regimes"):
        module.main([*common, "--market-regime", "bearish"])


def test_frozen_market_regimes_separate_terminal_paths():
    from statistics import mean

    from tradearena.data.synthetic import SyntheticMarketDataProvider

    configurations = {
        "bullish": (1.0, 1.0, 1.0),
        "bearish": (-1.0, -1.0, -1.0),
        "sideways": (0.0, 0.0, 0.0),
    }
    regime_means = {}
    for regime, (trend, seasonal, macro) in configurations.items():
        seed_returns = []
        for seed in range(1, 31):
            snapshots = SyntheticMarketDataProvider(
                symbols=("SYN", "ALT"),
                periods=24,
                seed=seed,
                trend_scale=trend,
                seasonal_scale=seasonal,
                macro_scale=macro,
            ).stream()
            seed_returns.append(
                mean(
                    snapshots[-1].bars[symbol].close / snapshots[0].bars[symbol].open - 1.0
                    for symbol in ("SYN", "ALT")
                )
            )
        regime_means[regime] = mean(seed_returns)

    assert regime_means["bullish"] > 0.30
    assert regime_means["bearish"] < -0.20
    assert abs(regime_means["sideways"]) < 0.01


def test_journal_verify_defense_quarantines_fabrications():
    base = InMemoryResearchMemory()
    _seed_steps(base, 8)
    polluted = PollutedResearchMemory(base=base, config=PollutionConfig(kind="fake_violations", dose=0.75, seed=1))
    defended = JournalVerifiedMemory(inner=polluted)

    recalled = polluted.recent("step", 8)
    verified = defended.recent("step", 8)
    injected = [event for event in recalled if event.get("injected")]

    assert injected, "test setup expected fabricated events"
    # the defense drops exactly the journal-contradicted events, keeping clean ones
    assert not any(event.get("injected") for event in verified)
    assert len(verified) == 8 - len(injected)
    # and it reaches that verdict by journal reconciliation, never by reading the
    # `injected` tag: every surviving event matches a true journal signature.
    truth_signatures = {_risk_signature(event) for event in base.recent("step", 8)}
    assert all(_risk_signature(event) in truth_signatures for event in verified)


def test_journal_verify_defense_is_noop_on_clean_recall():
    base = InMemoryResearchMemory()
    _seed_steps(base)
    defended = JournalVerifiedMemory(
        inner=PollutedResearchMemory(base=base, config=PollutionConfig(kind="fake_rejections", dose=0.0)),
    )

    assert defended.recent("step", 5) == base.recent("step", 5)


def test_factory_wires_defense_over_pollution_and_runs():
    system = build_default_system(
        name="defense_smoke",
        symbols=("SYN",),
        periods=20,
        seed=5,
        strategy_name="memory-aware",
        analyst_names=("momentum",),
        memory_pollution_kind="fake_violations",
        memory_pollution_dose=0.75,
        memory_pollution_seed=5,
        memory_pollution_defense="journal-verify",
    )

    assert isinstance(system.memory, JournalVerifiedMemory)
    _, metrics = system.run()
    assert metrics["hold_ratio"] != ""


def test_factory_wires_polluted_memory_and_run_records_pollution_ratio():
    system = build_default_system(
        name="pollution_smoke",
        symbols=("SYN",),
        periods=20,
        seed=5,
        strategy_name="memory-aware",
        analyst_names=("momentum",),
        memory_pollution_kind="fake_rejections",
        memory_pollution_dose=0.75,
        memory_pollution_seed=5,
    )

    assert isinstance(system.memory, PollutedResearchMemory)
    _, metrics = system.run()
    # Manipulation check: the strategy's perceived pollution must respond to dose.
    assert metrics["max_memory_pollution_ratio"] > 0.0

    clean = build_default_system(
        name="pollution_smoke_clean",
        symbols=("SYN",),
        periods=20,
        seed=5,
        strategy_name="memory-aware",
        analyst_names=("momentum",),
    )
    assert isinstance(clean.memory, InMemoryResearchMemory)
