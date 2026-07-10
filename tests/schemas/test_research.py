import pytest
from pydantic import ValidationError

from app.schemas.research import Contact, ResearchBrief


def test_minimal_brief_defaults():
    b = ResearchBrief(company_name="Acme", summary="A widgets company.")
    assert b.key_facts == []
    assert b.contacts == []
    assert b.sources == []
    assert b.domain is None


def test_full_brief_with_contacts():
    b = ResearchBrief(
        company_name="Acme",
        domain="acme.com",
        industry="Manufacturing",
        summary="Makes widgets.",
        key_facts=["Founded 1990"],
        contacts=[{"name": "Jane Doe", "role": "CTO", "email": "jane@acme.com"}],
        sources=["https://acme.com"],
    )
    assert b.contacts[0].name == "Jane Doe"
    assert isinstance(b.contacts[0], Contact)


def test_summary_required():
    with pytest.raises(ValidationError):
        ResearchBrief(company_name="Acme")
