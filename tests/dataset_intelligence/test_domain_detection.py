import pandas as pd
from services.dataset_intelligence.domain_detection import detect_domain
from services.dataset_intelligence.domain_knowledge import GENERAL_DOMAIN
from services.dataset_intelligence.profiler import profile_dataset


def _healthcare_df():
    return pd.DataFrame({
        "age": [45, 52, 61, 39, 70, 44, 58, 33, 66, 48],
        "blood_pressure": [120, 135, 145, 118, 150, 122, 138, 115, 148, 125],
        "heart_rate": [72, 80, 90, 68, 95, 74, 82, 65, 92, 76],
        "cholesterol": [190, 210, 240, 180, 260, 195, 220, 175, 250, 200],
        "diagnosis": [0, 1, 1, 0, 1, 0, 1, 0, 1, 0],
    })


def _finance_df():
    return pd.DataFrame({
        "income": [55000, 42000, 71000, 38000, 63000, 29000, 58000, 45000, 80000, 33000],
        "credit_score": [680, 610, 720, 590, 700, 560, 690, 620, 750, 575],
        "loan_amount": [15000, 22000, 10000, 25000, 12000, 30000, 14000, 20000, 8000, 27000],
        "debt_ratio": [0.32, 0.55, 0.21, 0.61, 0.28, 0.72, 0.30, 0.50, 0.15, 0.65],
        "default": [0, 1, 0, 1, 0, 1, 0, 1, 0, 1],
    })


def test_healthcare_domain_correctly_identified():
    df = _healthcare_df()
    profile = profile_dataset(df, "patients.csv")
    result = detect_domain(df, profile)
    assert result.top_domain == "Healthcare"
    assert result.top_confidence > 60  # confident match, not a coin flip


def test_finance_domain_correctly_identified():
    df = _finance_df()
    profile = profile_dataset(df, "loans.csv")
    result = detect_domain(df, profile)
    assert result.top_domain == "Finance"
    assert result.top_confidence > 60


def test_domain_detection_discriminates_not_just_matches():
    """The real test isn't just 'top domain is correct' — it's that the
    correct domain wins by a meaningful margin over the runner-up. A
    detector that scores everything ~50% would technically 'match' but be
    useless. This formalizes the manual check from Segment 2's checkpoint."""
    df = _healthcare_df()
    profile = profile_dataset(df, "patients.csv")
    result = detect_domain(df, profile)
    top_score = result.all_scores[0].confidence
    runner_up_score = result.all_scores[1].confidence
    assert top_score - runner_up_score > 20  # meaningful separation


def test_ambiguous_dataset_falls_back_to_general():
    """A dataset with no domain-indicative column names at all should not
    be force-fit into a misleading domain label."""
    df = pd.DataFrame({
        "col_x": [1, 2, 3, 4, 5], "col_y": [5, 4, 3, 2, 1], "col_z": ["p", "q", "r", "s", "t"],
    })
    profile = profile_dataset(df, "ambiguous.csv")
    result = detect_domain(df, profile)
    assert result.top_domain == GENERAL_DOMAIN


def test_reverse_substring_matching_bug_does_not_recur():
    """Regression test for the bug found in Segment 5: bidirectional
    substring matching let short column names (e.g. 'income') falsely
    match inside unrelated longer vocabulary terms (e.g. domain keywords
    containing 'income' as a substring). A retail-shaped dataset with an
    'average_revenue' column should NOT have "age" falsely match inside it
    via reverse substring containment.

    Asserts on `matched_columns` directly — the component the bug actually
    affected — rather than the overall confidence score, which is also
    legitimately influenced by other components (e.g. data_types ratio can
    coincidentally align for a small synthetic dataset regardless of this
    bug, and asserting on the composite score would conflate the two)."""
    df = pd.DataFrame({
        "product": ["Widget A", "Widget B"] * 5,
        "average_revenue": [100.0, 150.0] * 5,
        "quantity": list(range(10)),
    })
    profile = profile_dataset(df, "retail.csv")
    result = detect_domain(df, profile)
    healthcare_score = next(s for s in result.all_scores if s.domain == "Healthcare")
    # "average_revenue" must not appear as a matched column via "age" being
    # a substring of "average" — if the bug recurred, this list would
    # include it and column_similarity/feature_names would be inflated.
    assert healthcare_score.matched_columns == []
    assert healthcare_score.component_scores["feature_names"] == 0.0
    assert healthcare_score.component_scores["schema_completeness"] == 0.0
    # Healthcare must not win outright against a retail-shaped dataset
    assert result.top_domain != "Healthcare"
