from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.models import LeadRecord
from app.schemas.discovery import Candidate
from app.schemas.lead import Lead


class LeadRepository:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def save(self, lead: Lead) -> None:
        domain = lead.research.domain
        if not domain:
            return

        now = datetime.now(timezone.utc)
        with self._session_factory() as session:
            record = session.scalar(select(LeadRecord).where(LeadRecord.domain == domain))
            if record is None:
                record = LeadRecord(domain=domain, first_seen_at=now)
                session.add(record)

            record.company_name = lead.research.company_name
            record.industry = lead.research.industry
            record.status = lead.status
            record.score = lead.qualification.score
            record.reasoning = lead.qualification.reasoning
            record.summary = lead.research.summary
            record.key_facts = lead.research.key_facts
            record.contacts = [c.model_dump() for c in lead.research.contacts]
            record.sources = lead.research.sources
            record.outreach_subject = lead.outreach.subject if lead.outreach else None
            record.outreach_body = lead.outreach.body if lead.outreach else None
            record.last_seen_at = now
            session.commit()

    def filter_unseen(self, candidates: list[Candidate]) -> list[Candidate]:
        """Returns only candidates whose domain has never been saved before (permanent dedup)."""
        if not candidates:
            return []

        domains = [c.domain for c in candidates]
        with self._session_factory() as session:
            seen = set(
                session.scalars(select(LeadRecord.domain).where(LeadRecord.domain.in_(domains)))
            )
        return [c for c in candidates if c.domain not in seen]

    def all_domains(self) -> list[str]:
        """Every domain ever saved -- used to tell Discovery what to avoid re-suggesting."""
        with self._session_factory() as session:
            return list(session.scalars(select(LeadRecord.domain)))


def build_lead_repository(settings) -> LeadRepository:
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    return LeadRepository(sessionmaker(bind=engine))
