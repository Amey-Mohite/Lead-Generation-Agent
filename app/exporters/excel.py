import os
import time
from datetime import datetime, timezone

import openpyxl

from app.schemas.lead import Lead

_HEADER = [
    "Company Name", "Domain", "Industry", "Status", "Score",
    "Qualification Reasoning", "Summary", "Key Facts", "Contacts",
    "Sources", "Outreach Subject", "Outreach Body", "Generated At",
]


def _format_contacts(lead: Lead) -> str:
    parts = []
    for contact in lead.research.contacts:
        piece = contact.name
        if contact.role:
            piece += f" ({contact.role})"
        if contact.email:
            piece += f" <{contact.email}>"
        parts.append(piece)
    return "; ".join(parts)


class ExcelExporter:
    """Writes a list of Leads to a timestamped .xlsx file, one row per Lead."""

    def __init__(self, export_dir: str) -> None:
        self._export_dir = export_dir

    def export(self, leads: list[Lead]) -> str:
        os.makedirs(self._export_dir, exist_ok=True)
        generated_at = datetime.now(timezone.utc).isoformat()

        workbook = openpyxl.Workbook()
        worksheet = workbook.active
        worksheet.title = "Leads"
        worksheet.append(_HEADER)

        for lead in leads:
            worksheet.append(
                [
                    lead.research.company_name,
                    lead.research.domain,
                    lead.research.industry,
                    lead.status,
                    lead.qualification.score,
                    lead.qualification.reasoning,
                    lead.research.summary,
                    "; ".join(lead.research.key_facts),
                    _format_contacts(lead),
                    "; ".join(lead.research.sources),
                    lead.outreach.subject if lead.outreach else None,
                    lead.outreach.body if lead.outreach else None,
                    generated_at,
                ]
            )

        filename = f"leads_{time.time_ns()}.xlsx"
        path = os.path.join(self._export_dir, filename)
        workbook.save(path)
        return path
