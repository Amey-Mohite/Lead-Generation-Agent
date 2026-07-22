from datetime import datetime, timezone
from functools import lru_cache

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
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
            if lead.status == "qualified" and record.approval_status is None:
                record.approval_status = "pending"
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

    def list_leads(
        self, status: str | None = None, approval_status: str | None = None,
        limit: int = 50, offset: int = 0
    ) -> list[LeadRecord]:
        with self._session_factory() as session:
            stmt = select(LeadRecord).order_by(LeadRecord.last_seen_at.desc())
            if status is not None:
                stmt = stmt.where(LeadRecord.status == status)
            if approval_status is not None:
                stmt = stmt.where(LeadRecord.approval_status == approval_status)
            stmt = stmt.offset(offset).limit(limit)
            return list(session.scalars(stmt))

    def get_by_domain(self, domain: str) -> LeadRecord | None:
        with self._session_factory() as session:
            return session.scalar(select(LeadRecord).where(LeadRecord.domain == domain))

    def set_approval_status(self, domain: str, approval_status: str) -> LeadRecord | None:
        with self._session_factory() as session:
            record = session.scalar(select(LeadRecord).where(LeadRecord.domain == domain))
            if record is None:
                return None
            record.approval_status = approval_status
            session.commit()
            session.refresh(record)
            return record


@lru_cache
def _get_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True)


def build_lead_repository(settings) -> LeadRepository:
    engine = _get_engine(settings.database_url)
    return LeadRepository(sessionmaker(bind=engine))
