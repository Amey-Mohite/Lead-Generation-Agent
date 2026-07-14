from app.schemas.discovery import Candidate, CandidateList


def test_candidate_list_holds_candidates():
    cl = CandidateList(candidates=[{"name": "Acme", "domain": "acme.com"}])
    assert cl.candidates[0].name == "Acme"
    assert isinstance(cl.candidates[0], Candidate)


def test_candidate_list_can_be_empty():
    cl = CandidateList(candidates=[])
    assert cl.candidates == []
