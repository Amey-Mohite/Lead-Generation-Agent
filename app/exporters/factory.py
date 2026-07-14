from app.exporters.base import Exporter
from app.exporters.excel import ExcelExporter


def build_exporters(settings) -> list[Exporter]:
    names = [n.strip().lower() for n in settings.exporters.split(",") if n.strip()]
    exporters: list[Exporter] = []
    for name in names:
        if name == "excel":
            exporters.append(ExcelExporter(export_dir=settings.export_dir))
        # slack / email / gmail: reserved for future phases
    return exporters
