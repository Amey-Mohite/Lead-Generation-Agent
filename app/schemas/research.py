import re

from pydantic import BaseModel, field_validator

_DOMAIN_RE = re.compile(
    r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
)


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

    @field_validator("domain")
    @classmethod
    def domain_must_be_a_bare_hostname(cls, v: str | None) -> str | None:
        if v is not None and not _DOMAIN_RE.match(v):
            raise ValueError(
                f"domain must be a bare hostname (e.g. 'acme.com'), not prose or a markdown "
                f"link -- got {v!r}"
            )
        return v
