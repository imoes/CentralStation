"""Jeder AuditLog(...)-Aufruf benutzt nur Spalten, die es gibt — 12.11.2026 hatten drei neue Endpunkte `details=`
(keine Spalte) und wären beim ersten Aufruf abgestürzt; Unit-Tests rufen die Endpunkte nicht auf."""
import ast
import pathlib


def _spalten() -> set[str]:
    baum = ast.parse(pathlib.Path("app/models/audit.py").read_text())
    klasse = next(n for n in baum.body if isinstance(n, ast.ClassDef) and n.name == "AuditLog")
    return {n.target.id for n in klasse.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}


def test_auditlog_nur_mit_echten_spalten():
    spalten = _spalten()
    assert {"action", "new_value", "user_id"} <= spalten
    falsch = []
    for datei in pathlib.Path("app").rglob("*.py"):
        for n in ast.walk(ast.parse(datei.read_text())):
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "AuditLog":
                falsch += [f"{datei}:{n.lineno} {k.arg}" for k in n.keywords if k.arg and k.arg not in spalten]
    assert falsch == []
