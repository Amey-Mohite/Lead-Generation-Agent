from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.models import Base, LeadRecord
from app.db.repository import LeadRepository, _get_engine, build_lead_repository
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


def _insert_record(session_factory, domain: str, company_name: str = "Acme",
                    status: str = "qualified", last_seen_at=None) -> None:
    now = last_seen_at or datetime.now(timezone.utc)
    with session_factory() as session:
        session.add(
            LeadRecord(
                domain=domain, company_name=company_name, status=status, score=80,
                reasoning="ok", summary="s", key_facts=[], contacts=[], sources=[],
                first_seen_at=now, last_seen_at=now,
            )
        )
        session.commit()


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


def test_list_leads_returns_all_by_default_most_recent_first():
    repo, session_factory = _repository()
    base = datetime.now(timezone.utc)
    _insert_record(session_factory, "acme.com", last_seen_at=base)
    _insert_record(session_factory, "beta.com", company_name="Beta", last_seen_at=base + timedelta(seconds=10))

    results = repo.list_leads()

    assert [r.domain for r in results] == ["beta.com", "acme.com"]


def test_list_leads_filters_by_status():
    repo, session_factory = _repository()
    base = datetime.now(timezone.utc)
    _insert_record(session_factory, "acme.com", status="disqualified", last_seen_at=base)
    _insert_record(session_factory, "beta.com", company_name="Beta", status="qualified", last_seen_at=base + timedelta(seconds=10))

    qualified = repo.list_leads(status="qualified")
    disqualified = repo.list_leads(status="disqualified")

    assert [r.domain for r in qualified] == ["beta.com"]
    assert [r.domain for r in disqualified] == ["acme.com"]


def test_list_leads_respects_limit_and_offset():
    repo, session_factory = _repository()
    base = datetime.now(timezone.utc)
    _insert_record(session_factory, "acme.com", last_seen_at=base)
    _insert_record(session_factory, "beta.com", company_name="Beta", last_seen_at=base + timedelta(seconds=10))
    _insert_record(session_factory, "gamma.com", company_name="Gamma", last_seen_at=base + timedelta(seconds=20))

    page = repo.list_leads(limit=1, offset=1)

    assert [r.domain for r in page] == ["beta.com"]


def test_save_sets_pending_approval_status_for_new_qualified_lead():
    repo, session_factory = _repository()
    repo.save(_lead("acme.com"))

    with session_factory() as session:
        record = session.query(LeadRecord).filter_by(domain="acme.com").one()
        assert record.approval_status == "pending"


def test_save_never_sets_approval_status_for_disqualified_lead():
    repo, session_factory = _repository()
    lead = Lead(
        research=ResearchBrief(company_name="Acme", domain="acme.com", summary="A company."),
        qualification=Qualification(score=10, reasoning="not a fit"),
        status="disqualified",
    )
    repo.save(lead)

    with session_factory() as session:
        record = session.query(LeadRecord).filter_by(domain="acme.com").one()
        assert record.approval_status is None


def test_save_does_not_overwrite_existing_approval_status_on_resave():
    repo, session_factory = _repository()
    repo.save(_lead("acme.com"))
    repo.set_approval_status("acme.com", "approved")

    repo.save(_lead("acme.com", company_name="Acme Renamed"))  # re-saved, e.g. re-researched

    with session_factory() as session:
        record = session.query(LeadRecord).filter_by(domain="acme.com").one()
        assert record.company_name == "Acme Renamed"
        assert record.approval_status == "approved"  # untouched by the re-save


def test_list_leads_filters_by_approval_status():
    repo, session_factory = _repository()
    base = datetime.now(timezone.utc)
    _insert_record(session_factory, "acme.com", last_seen_at=base)
    _insert_record(session_factory, "beta.com", company_name="Beta", last_seen_at=base + timedelta(seconds=10))
    with session_factory() as session:
        session.query(LeadRecord).filter_by(domain="acme.com").one().approval_status = "pending"
        session.query(LeadRecord).filter_by(domain="beta.com").one().approval_status = "approved"
        session.commit()

    pending = repo.list_leads(approval_status="pending")
    approved = repo.list_leads(approval_status="approved")

    assert [r.domain for r in pending] == ["acme.com"]
    assert [r.domain for r in approved] == ["beta.com"]


def test_set_approval_status_updates_and_returns_the_record():
    repo, session_factory = _repository()
    repo.save(_lead("acme.com"))

    updated = repo.set_approval_status("acme.com", "approved")

    assert updated is not None
    assert updated.approval_status == "approved"


def test_set_approval_status_returns_none_for_unknown_domain():
    repo, _ = _repository()
    assert repo.set_approval_status("nonexistent.com", "approved") is None


def test_get_by_domain_returns_the_matching_record():
    repo, session_factory = _repository()
    _insert_record(session_factory, "acme.com", company_name="Acme")

    record = repo.get_by_domain("acme.com")

    assert record is not None
    assert record.company_name == "Acme"


def test_get_by_domain_returns_none_when_not_found():
    repo, _ = _repository()
    assert repo.get_by_domain("nonexistent.com") is None


def test_build_lead_repository_reuses_one_engine_per_database_url():
    _get_engine.cache_clear()
    settings = Settings(_env_file=None, database_url="sqlite:///:memory:")

    build_lead_repository(settings)
    build_lead_repository(settings)

    assert _get_engine.cache_info().hits == 1


def test_build_lead_repository_uses_a_different_engine_for_a_different_url():
    _get_engine.cache_clear()

    build_lead_repository(Settings(_env_file=None, database_url="sqlite:///:memory:"))
    engine_a = _get_engine("sqlite:///:memory:")
    build_lead_repository(Settings(_env_file=None, database_url="sqlite:///other.db"))
    engine_b = _get_engine("sqlite:///other.db")

    assert engine_a is not engine_b
