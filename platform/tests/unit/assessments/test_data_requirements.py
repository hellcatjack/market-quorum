import uuid

from sqlalchemy import create_engine, inspect

from tradingng_platform.models import AssessmentDataRequirement, Base


def test_data_requirement_schema_pins_provider_request_and_manifest():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    columns = {
        column["name"] for column in inspect(engine).get_columns("assessment_data_requirements")
    }
    assert {
        "run_id",
        "provider_request_id",
        "external_request_key",
        "required_products_json",
        "status",
        "progress_json",
        "manifest_snapshot_id",
        "manifest_sha256",
        "next_poll_at",
        "lease_owner",
        "lease_expires_at",
        "version",
    } <= columns


def test_data_requirement_defaults_are_waiting_and_unpinned():
    requirement = AssessmentDataRequirement(
        run_id=uuid.uuid4(),
        provider_request_id="123",
        external_request_key="batch:item",
        required_products_json=["market"],
    )

    assert requirement.status in (None, "waiting")
    assert requirement.manifest_snapshot_id is None
    assert requirement.manifest_sha256 is None
