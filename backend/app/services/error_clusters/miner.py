"""Log-Muster mit Drain3 — der verbreitete Standard für Template-Mining (logpai/Drain3).

Drain3 ordnet jede Zeile einem Muster zu ("ERROR - …ChangelogReader - Failed to read
change-log: HTTP Status '<*> Unauthorized'") und lernt dabei laufend dazu.

Zwei Festlegungen, die über die Bibliothek hinausgehen:

* **Der Schlüssel eines Musters ist md5(Muster)[:16], nicht die Drain-ID.** Die ID ist
  nur im gespeicherten Zustand gültig; ginge der verloren, bekäme jedes bekannte Muster
  eine neue ID und wäre scheinbar "neu" — eine Flut von Meldungen. Über den Text findet
  sich ein Muster auch nach einem Zustandsverlust wieder.
* **Gespeichert wird einmal pro Lauf**, nicht bei jedem neuen Muster (Drain3s Vorgabe):
  bei Tausenden Zeilen pro Lauf wäre das ein vollständiges Serialisieren je Neuzugang.

HTTP-Statuscodes bleiben Teil des Musters (401 und 500 sind verschiedene Fehler); alle
anderen Zahlen, IDs, Adressen und Zeitstempel werden maskiert.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from drain3 import TemplateMiner
from drain3.masking import MaskingInstruction
from drain3.persistence_handler import PersistenceHandler
from drain3.template_miner_config import TemplateMinerConfig

from app.services.connectors.graylog import strip_timestamps

#: Länge, bis zu der eine Zeile ausgewertet wird. Längere Zeilen tragen hinten fast nur
#: Daten (JSON, Stacktraces), die das Muster nicht schärfen, aber verlangsamen.
MAX_LINE = 500

# "HTTP Status '401", "status=503", "code: 404" → "HTTP401": Buchstaben direkt vor der
# Zahl, damit die Zahlenmaske unten den Code stehen lässt.
_HTTP_CODE = re.compile(
    r"(?i)\b(?:http(?:\s+status)?|status(?:\s+code)?|code)[\s:='\"]*([1-5]\d\d)\b"
)

#: Reihenfolge zählt: spezifische Formen vor der allgemeinen Zahl.
MASKS: list[tuple[str, str]] = [
    (r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?", "TS"),
    (r"\b\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\b", "TS"),
    (r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", "UUID"),
    (r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d{1,5})?\b", "IP"),
    (r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", "EMAIL"),
    (r"\?[^\s\"']+", "QS"),
    (r"\b0x[0-9a-fA-F]+\b", "HEX"),
    (r"\b[0-9a-fA-F]{12,}\b", "HEX"),
    # Lookbehind/-ahead schließen auch Ziffern aus: sonst fände die Maske in "HTTP401"
    # ab der zweiten Ziffer eine Zahl ("HTTP4<NUM>").
    (r"(?<![A-Za-z0-9_])[-+]?\d+(?:\.\d+)?(?![A-Za-z0-9_])", "NUM"),
]

#: Drain3-Parameter (Recherche: sim_th 0.4–0.5, depth 4; Grenze gegen Speicherwachstum).
SIM_TH = 0.45
DEPTH = 4
MAX_CHILDREN = 100
MAX_CLUSTERS = 10_000


def normalize(message: str) -> str:
    """Erste Zeile, ohne führenden Zeitstempel, gekürzt, mit geschützten HTTP-Codes."""
    first = (message or "").split("\n", 1)[0]
    first = strip_timestamps(first)[:MAX_LINE]
    return _HTTP_CODE.sub(lambda m: f"HTTP{m.group(1)}", first)


def template_key(template: str) -> str:
    """Stabiler Schlüssel eines Musters — unabhängig vom Drain-Zustand."""
    return hashlib.md5(template.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Mined:
    cluster_id: int
    template: str
    key: str
    #: "cluster_created" | "cluster_template_changed" | "none"
    change: str


class _BytesHandler(PersistenceHandler):
    """Hält den Zustand als Bytes — gespeichert wird er vom Aufrufer (Redis)."""

    def __init__(self, state: bytes | None = None) -> None:
        self.state = state

    def save_state(self, state) -> None:
        self.state = state

    def load_state(self):
        return self.state


def _config() -> TemplateMinerConfig:
    cfg = TemplateMinerConfig()
    cfg.drain_sim_th = SIM_TH
    cfg.drain_depth = DEPTH
    cfg.drain_max_children = MAX_CHILDREN
    cfg.drain_max_clusters = MAX_CLUSTERS
    cfg.masking_instructions = [MaskingInstruction(p, m) for p, m in MASKS]
    # Aus: Zahlen maskiert MASKS selbst. Drain3s Option machte jedes Token mit einer
    # Ziffer zum Platzhalter — auch "HTTP401", und 401 und 500 fielen zusammen.
    cfg.parametrize_numeric_tokens = False
    cfg.snapshot_compress_state = True
    return cfg


class Miner:
    """Drain3 mit Laden/Speichern als Bytes und stabilen Musterschlüsseln."""

    def __init__(self, state: bytes | None = None) -> None:
        self._tm = TemplateMiner(None, _config())
        if state:
            handler = _BytesHandler(state)
            self._tm.persistence_handler = handler
            try:
                self._tm.load_state()
            finally:
                # Ohne Handler speichert Drain3 nicht bei jedem neuen Muster.
                self._tm.persistence_handler = None

    def add(self, message: str) -> Mined | None:
        """Ordnet eine Zeile einem Muster zu (und lernt). None für leere Zeilen."""
        line = normalize(message)
        if not line.strip():
            return None
        r = self._tm.add_log_message(line)
        template = r["template_mined"]
        return Mined(cluster_id=int(r["cluster_id"]), template=template,
                     key=template_key(template), change=r["change_type"])

    def match(self, message: str) -> Mined | None:
        """Nur zuordnen, nichts lernen — für das Markieren bestehender Feed-Einträge."""
        line = normalize(message)
        cluster = self._tm.match(line) if line.strip() else None
        if cluster is None:
            return None
        template = cluster.get_template()
        return Mined(cluster.cluster_id, template, template_key(template), "none")

    def state(self) -> bytes:
        handler = _BytesHandler()
        self._tm.persistence_handler = handler
        try:
            self._tm.save_state("run")
        finally:
            self._tm.persistence_handler = None
        return handler.state

    @property
    def cluster_count(self) -> int:
        return len(self._tm.drain.clusters)
