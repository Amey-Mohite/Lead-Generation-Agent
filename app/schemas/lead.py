from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.research import ResearchBrief


class Qualification(BaseModel):
    score: int = Field(ge=0, le=100)
    reasoning: str


class OutreachDraft(BaseModel):
    subject: str
    body: str


class Lead(BaseModel):
    research: ResearchBrief
    qualification: Qualification
    outreach: OutreachDraft | None = None
    status: Literal["qualified", "disqualified"]
