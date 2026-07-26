from sqlalchemy import UniqueConstraint

from tradingng_platform.models import Comment, Review, Webhook, WebhookDelivery


def test_collaboration_and_webhook_tables_have_required_constraints():
    assert Review.__tablename__ == "reviews"
    assert Comment.__tablename__ == "comments"
    assert Webhook.__tablename__ == "webhooks"
    assert WebhookDelivery.__tablename__ == "webhook_deliveries"

    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in WebhookDelivery.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("webhook_id", "event_id") in unique_columns
    assert "encrypted_secret" in Webhook.__table__.columns
    assert "endpoint" in Webhook.__table__.columns
