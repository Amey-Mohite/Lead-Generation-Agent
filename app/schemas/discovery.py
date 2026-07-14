from pydantic import BaseModel


class Candidate(BaseModel):
    name: str
    domain: str


class CandidateList(BaseModel):
    candidates: list[Candidate]
