from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class LeadRecord(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    company_name: Mapped[str] = mapped_column(String(255))
    industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    score: Mapped[int] = mapped_column(Integer)
    reasoning: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    key_facts: Mapped[list] = mapped_column(JSON, default=list)
    contacts: Mapped[list] = mapped_column(JSON, default=list)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    outreach_subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    outreach_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
