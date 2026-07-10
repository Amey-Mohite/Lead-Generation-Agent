from pydantic import BaseModel


class Contact(BaseModel):
    name: str
    role: str | None = None
    email: str | None = None


class ResearchBrief(BaseModel):
    company_name: str
    domain: str | None = None
    industry: str | None = None
    summary: str
    key_facts: list[str] = []
    contacts: list[Contact] = []
    sources: list[str] = []
