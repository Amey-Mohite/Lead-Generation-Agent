import pytest
from pydantic import ValidationError

from app.schemas.lead import Lead, OutreachDraft, Qualification
from app.schemas.research import ResearchBrief


def _brief() -> ResearchBrief:
    return ResearchBrief(company_name="Acme", summary="Makes widgets.")


def test_qualification_score_bounds():
    Qualification(score=0, reasoning="ok")
    Qualification(score=100, reasoning="ok")
    with pytest.raises(ValidationError):
        Qualification(score=101, reasoning="too high")
    with pytest.raises(ValidationError):
        Qualification(score=-1, reasoning="too low")


def test_lead_disqualified_has_no_outreach():
    lead = Lead(
        research=_brief(),
        qualification=Qualification(score=20, reasoning="Not a fit."),
        status="disqualified",
    )
    assert lead.outreach is None


def test_lead_qualified_with_outreach():
    lead = Lead(
        research=_brief(),
        qualification=Qualification(score=90, reasoning="Great fit."),
        outreach=OutreachDraft(subject="Hi", body="Noticed you make widgets..."),
        status="qualified",
    )
    assert lead.outreach.subject == "Hi"


def test_lead_status_rejects_invalid_value():
    with pytest.raises(ValidationError):
        Lead(
            research=_brief(),
            qualification=Qualification(score=50, reasoning="ok"),
            status="maybe",
        )
