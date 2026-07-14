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


def test_domain_rejects_a_prose_sentence_instead_of_a_hostname():
    with pytest.raises(ValidationError):
        ResearchBrief(
            company_name="Acme",
            summary="A widgets company.",
            domain="The queried domain acme.co does not resolve; the real site is acme.com.",
        )


def test_domain_rejects_a_markdown_link():
    with pytest.raises(ValidationError):
        ResearchBrief(
            company_name="Acme",
            summary="A widgets company.",
            domain="[acme.com](https://acme.com)",
        )


def test_domain_accepts_a_bare_hostname_with_subdomain():
    b = ResearchBrief(company_name="Acme", summary="A widgets company.", domain="www.acme.co.uk")
    assert b.domain == "www.acme.co.uk"
