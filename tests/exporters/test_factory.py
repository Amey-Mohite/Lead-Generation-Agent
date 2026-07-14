from app.config import Settings
from app.exporters.excel import ExcelExporter
from app.exporters.factory import build_exporters


def test_build_exporters_returns_excel_exporter_by_default():
    s = Settings(_env_file=None)
    exporters = build_exporters(s)
    assert len(exporters) == 1
    assert isinstance(exporters[0], ExcelExporter)


def test_build_exporters_ignores_unknown_names():
    s = Settings(_env_file=None, exporters="excel,slack,unknown")
    exporters = build_exporters(s)
    assert len(exporters) == 1
    assert isinstance(exporters[0], ExcelExporter)


def test_build_exporters_empty_string_returns_empty_list():
    s = Settings(_env_file=None, exporters="")
    exporters = build_exporters(s)
    assert exporters == []
