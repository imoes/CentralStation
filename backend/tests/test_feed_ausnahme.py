"""Feed-Ausnahme: Namenslänge vorher prüfen (F7 der externen Analyse 12.11.2026)."""
import ast
import pathlib

from app.services.feed_ausnahme import NAME_MAX, eingabe_fehler


def test_name_laenge():
    assert eingabe_fehler("x" * NAME_MAX) is None and eingabe_fehler("  kurz  ") is None
    f = eingabe_fehler("y" * (NAME_MAX + 1))
    assert f and "101 Zeichen" in f and "100" in f


def test_spaltenlaenge_stimmt_mit_dem_modell():
    q = pathlib.Path("app/models/workflow.py").read_text()
    assert "name: Mapped[str] = mapped_column(String(100))" in q and NAME_MAX == 100


def test_werkzeug_prueft_und_gibt_keine_rohen_db_fehler():
    quelle = pathlib.Path("app/api/mcp_server.py").read_text()
    fn = next(n for n in ast.parse(quelle).body if isinstance(n, ast.AsyncFunctionDef) and n.name == "create_feed_exclusion")
    text = ast.unparse(fn)
    assert "eingabe_fehler(name)" in text and "Details im Server-Log" in text
