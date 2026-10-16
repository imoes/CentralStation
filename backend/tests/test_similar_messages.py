"""Recherche "ähnliche Meldungen" (16.10.2026): statt jedem Container mit erhöhter Fehlerrate
nur andere Muster mit ähnlichem Text im selben Fenster — Nutzer: "nur Zusammenhänge, kein
Bruteforce"."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from app.services.error_clusters.engine import (
    BUCKET, BucketRec, MemoryStore, TemplateInfo, similar_patterns, similarity, template_words,
)

B = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)


def _store(templates: dict[str, tuple[str, int, dict[str, int]]]) -> MemoryStore:
    st = MemoryStore(B - timedelta(days=3))
    for key, (tpl, count, ents) in templates.items():
        st.templates[key] = TemplateInfo(key, tpl, B - timedelta(days=2), B)
        st.buckets[key] = {B: BucketRec(count, len(ents), Counter(ents))}
    return st


def test_words_ignore_placeholders_and_level_words():
    w = template_words("ERROR [<NUM>] com.escenic.sse.PayloadTransformer: Transforming payload <*> failed")
    assert "error" not in w and "num" not in w
    assert {"com.escenic.sse.payloadtransformer", "transforming", "payload", "failed"} <= w


def test_same_error_on_other_containers_is_similar_unrelated_error_is_not():
    st = _store({
        "own": ("ERROR - cue.zipline - Failed to read change-log: HTTP Status '401 Unauthorized' from <*>",
                900, {"cue-zipline-product-1": 900}),
        "same": ("ERROR - cue.zipline.dam - Failed to read change-log: HTTP Status '401 Unauthorized'",
                 120, {"cue-zipline-dam": 120}),
        # gleichzeitig laut, aber ein ganz anderer Fehler — gehört NICHT dazu
        "other": ("dockerd[<NUM>]: time=<TS> level=error msg=copy stream failed error=reading from a closed fifo",
                  2285, {"cue0112.example.com": 2285}),
        "quiet": ("ERROR - cue.zipline - Failed to read change-log: HTTP Status '401 Unauthorized' from x",
                  0, {}),
    })
    st.buckets["quiet"] = {B - timedelta(hours=3): BucketRec(5, 1, None)}   # nicht in diesem Fenster
    hits = similar_patterns(st, "own", B)
    assert [h["template"][:40] for h in hits] == ["ERROR - cue.zipline.dam - Failed to read"]
    assert hits[0]["containers"] == [{"name": "cue-zipline-dam", "count": 120}]
    assert hits[0]["similarity"] >= 0.5


def test_too_few_shared_words_is_not_similar():
    share, shared = similarity(template_words("connection refused"), template_words("connection refused"))
    assert share == 1.0 and shared == 2          # unter SIMILAR_MIN_SHARED (3) — zu dünn
    st = _store({"a": ("connection refused", 10, {"x": 10}), "b": ("connection refused", 10, {"y": 10})})
    assert similar_patterns(st, "a", B) == []
