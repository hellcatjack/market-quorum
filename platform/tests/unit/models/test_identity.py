from tradingng_platform.models import User


def test_user_tracks_authoritative_identity_sync_time():
    assert "synced_at" in User.__table__.columns
    assert User.__table__.c.synced_at.nullable is False
