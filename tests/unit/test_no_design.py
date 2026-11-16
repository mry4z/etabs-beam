from src.design.no_design import excluded_no_design_source_names, is_explicit_no_design


def test_only_explicit_skipped_no_design_or_exclusion_statuses_match() -> None:
    assert is_explicit_no_design("SKIPPED", "GetDesignProcedure=7 No Design")
    assert is_explicit_no_design("EXCLUDED_NO_DESIGN")
    assert not is_explicit_no_design("SKIPPED", "非混凝土或无有效截面")
    assert not is_explicit_no_design("NO_RESULTS", "No Design result was returned")
    assert not is_explicit_no_design("API_ERROR", "No Design API result was available")
    assert not is_explicit_no_design("NO_RESULTS", "N.A.")


def test_exclusion_audit_names_include_only_explicit_no_design_objects() -> None:
    exclusions = (
        {"object_name": "B1", "status": "SKIPPED", "reason": "GetDesignProcedure=7 No Design"},
        {"object_name": "B2", "status": "NO_RESULTS", "reason": "No Design result was returned"},
        {"object_name": "B3", "status": "API_ERROR", "reason": "No Design API result was available"},
    )

    assert excluded_no_design_source_names(exclusions) == frozenset({"B1"})
