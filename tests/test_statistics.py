from tradearena.evaluation.statistics import (
    benjamini_hochberg,
    cliffs_delta,
    kendall_tau,
    paired_bootstrap_difference,
    paired_cohens_d,
    paired_permutation_p_value,
    random_effects_meta,
    sample_std,
    summarize_metric,
    top_k_jaccard,
    variance_components,
)


def test_summarize_metric_reports_mean_std_and_ci():
    summary = summarize_metric([0.01, 0.02, 0.03], prefix="return")

    assert summary["return_mean"] == 0.02
    assert round(float(summary["return_std"]), 6) == 0.01
    assert summary["return_ci_low"] is not None
    assert summary["return_ci_high"] is not None


def test_paired_bootstrap_difference_uses_matched_keys():
    result = paired_bootstrap_difference(
        {("calm", 1): 0.04, ("calm", 2): 0.03, ("other", 1): 0.01},
        {("calm", 1): 0.01, ("calm", 2): 0.02},
    )

    assert result["paired_n"] == 2
    assert round(float(result["mean_delta"]), 6) == 0.02
    assert result["p_value"] is not None
    assert result["bootstrap_p_value"] == result["p_value"]
    assert result["permutation_p_value"] is not None


def test_paired_permutation_p_value_is_exact_for_small_samples():
    p_value = paired_permutation_p_value([0.03, 0.02, 0.04])

    assert p_value is not None
    assert 0.0 <= p_value <= 1.0
    assert p_value < 0.5


def test_paired_permutation_monte_carlo_p_value_is_never_zero():
    p_value = paired_permutation_p_value([1.0] * 17, draws=32, seed=7)

    assert p_value is not None
    assert p_value >= 1 / 33


def test_sample_std_singleton_is_zero():
    assert sample_std([0.1]) == 0.0


def test_benjamini_hochberg_adjusts_and_preserves_order():
    q_values = benjamini_hochberg({"a": 0.01, "b": 0.04, "c": 0.03, "d": 0.20})

    assert round(float(q_values["a"]), 6) == 0.04
    assert round(float(q_values["b"]), 6) == 0.053333
    assert round(float(q_values["c"]), 6) == 0.053333
    assert round(float(q_values["d"]), 6) == 0.2
    # Monotonic in raw p-value and never below the raw p-value.
    assert q_values["a"] <= q_values["c"] <= q_values["b"] <= q_values["d"]
    assert all(q_values[key] >= p for key, p in {"a": 0.01, "b": 0.04, "c": 0.03, "d": 0.20}.items())
    assert all(q_values[key] <= 1.0 for key in q_values)


def test_benjamini_hochberg_passes_through_none():
    q_values = benjamini_hochberg({"a": 0.01, "b": None})

    assert q_values["b"] is None
    assert q_values["a"] is not None


def test_benjamini_hochberg_empty_family():
    assert benjamini_hochberg({}) == {}


def test_paired_cohens_d_known_value():
    # deltas mean=0.02, sample std=0.01 -> d=2.0
    assert round(float(paired_cohens_d([0.01, 0.02, 0.03])), 6) == 2.0
    assert paired_cohens_d([0.01]) is None
    assert paired_cohens_d([0.02, 0.02]) is None
    assert paired_cohens_d([0.0, 0.0]) == 0.0


def test_cliffs_delta_bounds_and_signs():
    assert cliffs_delta([2.0, 3.0], [0.0, 1.0]) == 1.0
    assert cliffs_delta([0.0, 1.0], [2.0, 3.0]) == -1.0
    assert cliffs_delta([1.0, 2.0], [1.0, 2.0]) == 0.0
    assert cliffs_delta([], [1.0]) is None


def test_random_effects_meta_homogeneous_groups_match_fixed_effect():
    # Identical effects and variances: tau^2 = 0, pooled equals the common effect.
    result = random_effects_meta({"a": (0.02, 0.0001), "b": (0.02, 0.0001), "c": (0.02, 0.0001)})

    assert result["group_count"] == 3
    assert round(float(result["pooled_effect"]), 6) == 0.02
    assert result["tau_squared"] == 0.0
    assert result["i_squared"] == 0.0
    assert result["pooled_ci_low"] < 0.02 < result["pooled_ci_high"]


def test_random_effects_meta_heterogeneity_widens_interval():
    homogeneous = random_effects_meta({"a": (0.02, 0.0001), "b": (0.02, 0.0001)})
    heterogeneous = random_effects_meta({"a": (0.10, 0.0001), "b": (-0.06, 0.0001)})

    assert heterogeneous["tau_squared"] > 0.0
    assert heterogeneous["i_squared"] > 0.5
    width_homogeneous = homogeneous["pooled_ci_high"] - homogeneous["pooled_ci_low"]
    width_heterogeneous = heterogeneous["pooled_ci_high"] - heterogeneous["pooled_ci_low"]
    assert width_heterogeneous > width_homogeneous


def test_random_effects_meta_empty_and_invalid_variances():
    assert random_effects_meta({})["pooled_effect"] is None
    assert random_effects_meta({"a": (0.1, 0.0)})["pooled_effect"] is None


def test_kendall_tau_perfect_agreement_and_reversal():
    scores = {"a": 3.0, "b": 2.0, "c": 1.0}
    reversed_scores = {"a": 1.0, "b": 2.0, "c": 3.0}

    assert kendall_tau(scores, scores) == 1.0
    assert kendall_tau(scores, reversed_scores) == -1.0
    assert kendall_tau({"a": 1.0}, {"a": 2.0}) is None


def test_kendall_tau_handles_ties():
    tau = kendall_tau({"a": 1.0, "b": 1.0, "c": 0.0}, {"a": 2.0, "b": 1.0, "c": 0.0})

    assert tau is not None
    assert 0.0 < tau < 1.0


def test_top_k_jaccard_overlap():
    level_zero = {"a": 3.0, "b": 2.0, "c": 1.0, "d": 0.5}
    level_one = {"a": 3.0, "d": 2.5, "c": 1.0, "b": 0.1}

    assert top_k_jaccard(level_zero, level_zero, k=2) == 1.0
    # top-2 sets are {a, b} vs {a, d}: intersection 1, union 3.
    assert round(float(top_k_jaccard(level_zero, level_one, k=2)), 6) == round(1 / 3, 6)
    assert top_k_jaccard({}, {}, k=2) is None


def test_variance_components_separates_between_and_within():
    # Two seeds with distinct means (between) and per-seed sampling spread (within).
    components = variance_components(
        {
            "seed_7": [0.10, 0.12],
            "seed_11": [0.20, 0.22],
        }
    )

    assert components["group_count"] == 2
    assert components["total_n"] == 4
    # The variance of the group means (0.005 here) estimates sigma_b^2 +
    # sigma_w^2/n, so the between component subtracts the sampling term:
    # 0.005 - 0.0002/2 = 0.0049. Reporting the raw 0.005 inflates the
    # denominator of the within share and makes sampling noise look smaller
    # than it is; this test previously asserted the biased value.
    assert round(float(components["within_group_variance"]), 6) == 0.0002
    assert round(float(components["between_group_variance"]), 6) == 0.0049
    assert 0.0 < float(components["within_group_share"]) < 1.0


def test_variance_components_single_samples_have_no_within():
    components = variance_components({"seed_7": [0.1], "seed_11": [0.2]})

    assert components["between_group_variance"] is not None
    assert components["within_group_variance"] is None
    assert components["within_group_share"] is None


def test_variance_components_empty():
    components = variance_components({})

    assert components["group_count"] == 0
    assert components["between_group_variance"] is None


def test_paired_bootstrap_difference_reports_effect_sizes():
    result = paired_bootstrap_difference(
        {("calm", 1): 0.04, ("calm", 2): 0.03, ("calm", 3): 0.05},
        {("calm", 1): 0.01, ("calm", 2): 0.02, ("calm", 3): 0.01},
    )

    assert result["cohens_d"] is not None
    assert result["cliffs_delta"] == 1.0

    empty = paired_bootstrap_difference({}, {})
    assert empty["cohens_d"] is None
    assert empty["cliffs_delta"] is None


def test_wilson_interval_bounds_and_extremes():
    from tradearena.evaluation.statistics import wilson_interval

    p, lo, hi = wilson_interval(8, 10)
    assert p == 0.8
    assert 0.0 < lo < 0.8 < hi < 1.0
    # Perfect success: point 1.0, upper capped at 1.0, lower below 1.
    p1, lo1, hi1 = wilson_interval(25, 25)
    assert p1 == 1.0 and hi1 == 1.0 and lo1 < 1.0
    # Zero successes: point 0, lower floored at 0.
    p0, lo0, hi0 = wilson_interval(0, 25)
    assert p0 == 0.0 and lo0 == 0.0 and hi0 > 0.0
    # Empty.
    assert wilson_interval(0, 0) == (0.0, 0.0, 0.0)
