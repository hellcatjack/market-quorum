from tradingng_platform.models import AssessmentRun, RunIntegrityAssessment


def test_integrity_model_has_immutable_identity_and_clean_reassessment_link():
    constraint_names = {
        constraint.name for constraint in RunIntegrityAssessment.__table__.constraints
    }
    columns = set(RunIntegrityAssessment.__table__.columns.keys())

    assert RunIntegrityAssessment.__tablename__ == "run_integrity_assessments"
    assert "uq_run_integrity_policy_input" in constraint_names
    assert {
        "run_id",
        "artifact_id",
        "policy_version",
        "status",
        "audit_mode",
        "temporal_scope",
        "analysis_date",
        "checked_at",
        "reason_codes_json",
        "tool_findings_json",
        "input_fingerprint",
    } <= columns
    clean_link = AssessmentRun.__table__.columns["clean_reassessment_of_run_id"]
    assert next(iter(clean_link.foreign_keys)).target_fullname == "assessment_runs.id"
