from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, LeadRecord


def test_lead_record_approval_status_column_defaults_to_none():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    now = datetime.now(timezone.utc)

    with Session() as session:
        record = LeadRecord(
            domain="acme.com", company_name="Acme", status="qualified", score=80,
            reasoning="ok", summary="s", key_facts=[], contacts=[], sources=[],
            first_seen_at=now, last_seen_at=now,
        )
        session.add(record)
        session.commit()

        fetched = session.query(LeadRecord).filter_by(domain="acme.com").one()
        assert fetched.approval_status is None
