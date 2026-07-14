from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, LeadRecord
from app.db.repository import LeadRepository
from app.schemas.discovery import Candidate
from app.schemas.lead import Lead, OutreachDraft, Qualification
from app.schemas.research import Contact, ResearchBrief


def _repository():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return LeadRepository(sessionmaker(bind=engine)), sessionmaker(bind=engine)


def _lead(domain: str, company_name: str = "Acme") -> Lead:
    return Lead(
        research=ResearchBrief(
            company_name=company_name,
            domain=domain,
            industry="Financial Services",
            summary="A company.",
            key_facts=["Founded 1990"],
            contacts=[Contact(name="Jane Doe", role="CTO", email="jane@example.com")],
            sources=["https://example.com"],
        ),
        qualification=Qualification(score=85, reasoning="Strong fit."),
        outreach=OutreachDraft(subject="Hi", body="Hello there"),
        status="qualified",
    )


def test_save_creates_a_new_row():
    repo, session_factory = _repository()
    repo.save(_lead("acme.com"))

    with session_factory() as session:
        record = session.query(LeadRecord).filter_by(domain="acme.com").one()
        assert record.company_name == "Acme"
        assert record.score == 85
        assert record.key_facts == ["Founded 1990"]
        assert record.outreach_subject == "Hi"
        assert record.first_seen_at is not None
        assert record.last_seen_at is not None


def test_save_upserts_on_repeat_domain_and_keeps_first_seen_at():
    repo, session_factory = _repository()
    repo.save(_lead("acme.com", company_name="Acme"))
    with session_factory() as session:
        first_seen = session.query(LeadRecord).filter_by(domain="acme.com").one().first_seen_at

    repo.save(_lead("acme.com", company_name="Acme Renamed"))

    with session_factory() as session:
        records = session.query(LeadRecord).filter_by(domain="acme.com").all()
        assert len(records) == 1
        assert records[0].company_name == "Acme Renamed"
        assert records[0].first_seen_at == first_seen


def test_save_skips_leads_with_no_domain():
    repo, session_factory = _repository()
    lead = Lead(
        research=ResearchBrief(company_name="No Domain Co", summary="n/a"),
        qualification=Qualification(score=10, reasoning="no domain"),
        status="disqualified",
    )
    repo.save(lead)

    with session_factory() as session:
        assert session.query(LeadRecord).count() == 0


def test_filter_unseen_excludes_any_previously_seen_domain():
    repo, session_factory = _repository()
    repo.save(_lead("acme.com"))

    candidates = [Candidate(name="Acme", domain="acme.com"), Candidate(name="Beta", domain="beta.com")]
    unseen = repo.filter_unseen(candidates)

    assert [c.domain for c in unseen] == ["beta.com"]


def test_filter_unseen_excludes_regardless_of_how_long_ago_it_was_seen():
    repo, session_factory = _repository()
    repo.save(_lead("acme.com"))
    with session_factory() as session:
        record = session.query(LeadRecord).filter_by(domain="acme.com").one()
        record.last_seen_at = record.last_seen_at.replace(year=2000)  # ages it artificially
        session.commit()

    candidates = [Candidate(name="Acme", domain="acme.com")]
    unseen = repo.filter_unseen(candidates)

    assert unseen == []  # still excluded -- dedup is permanent, not time-windowed


def test_filter_unseen_with_empty_candidates_returns_empty():
    repo, _ = _repository()
    assert repo.filter_unseen([]) == []


def test_all_domains_returns_every_saved_domain():
    repo, _ = _repository()
    repo.save(_lead("acme.com"))
    repo.save(_lead("beta.com", company_name="Beta"))

    assert set(repo.all_domains()) == {"acme.com", "beta.com"}


def test_all_domains_empty_when_nothing_saved():
    repo, _ = _repository()
    assert repo.all_domains() == []
