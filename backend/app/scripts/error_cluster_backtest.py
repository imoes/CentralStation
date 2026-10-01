"""Backtest der Fehler-Cluster-Erkennung über echte Graylog-Daten.

Spielt 5-min-Fenster von alt nach neu durch denselben Collector, Miner und dieselbe
Engine wie der Betrieb — mit In-Memory-Store, ohne LLM, ohne Mail, ohne DB-Schreiben.
Rohdaten landen im Cache (gzip je Fenster), damit Schwellen ohne erneute
Graylog-Abfragen abgestimmt werden können.

    docker compose run --rm --no-deps -v <cache>:/bt backend \\
        python -m app.scripts.error_cluster_backtest --until 2026-10-01T12:00Z --hours 72

Bestanden, wenn die ChangelogReader-401-Episode im ersten Fenster beider Wellen meldet
(30.09. 11:30–11:40, 01.10. 08:00–08:10 UTC) und dam.stage still bleibt.
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import decrypt_credentials
from app.models.connector import ConnectorConfig
from app.services.connectors.graylog import GraylogConnector
from app.services.error_clusters.collector import Line, fetch_window, tally
from app.services.error_clusters.engine import BUCKET, MemoryStore, process
from app.services.error_clusters.miner import Miner

EXPECT = [
    ("ChangelogReader", "HTTP401", datetime(2026, 9, 30, 11, 30, tzinfo=timezone.utc)),
    ("ChangelogReader", "HTTP401", datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)),
]


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


async def _svc():
    async with AsyncSessionLocal() as db:
        c = (await db.execute(select(ConnectorConfig).where(
            ConnectorConfig.type == "graylog", ConnectorConfig.enabled.is_(True)))).scalars().first()
    return GraylogConnector(base_url=c.base_url, credentials=decrypt_credentials(c.encrypted_credentials))


async def _window(svc, b: datetime, cache: str, throttle: float):
    path = os.path.join(cache, b.strftime("%Y%m%dT%H%M.jsonl.gz"))
    if os.path.exists(path):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            head = json.loads(fh.readline())
            lines = [Line(**json.loads(x)) for x in fh]
        return lines, head["total"], head["complete"]
    fetched = await fetch_window(svc, b, b + BUCKET)
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"total": fetched.total, "complete": fetched.complete}) + "\n")
        for ln in fetched.lines:
            fh.write(json.dumps(ln.__dict__, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    await asyncio.sleep(throttle)
    return fetched.lines, fetched.total, fetched.complete


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--until", required=True)
    ap.add_argument("--hours", type=float, default=72)
    ap.add_argument("--cache-dir", default="/bt")
    ap.add_argument("--throttle", type=float, default=0.5)
    a = ap.parse_args()
    until = _parse(a.until)
    start = until - timedelta(hours=a.hours)
    os.makedirs(a.cache_dir, exist_ok=True)

    svc = await _svc()
    store, miner = MemoryStore(start), Miner()
    events, incomplete, lines_total = [], 0, 0
    t0 = time.time()
    b = start
    n = int((until - start) / BUCKET)
    for i in range(n):
        lines, total, complete = await _window(svc, b, a.cache_dir, a.throttle)
        incomplete += 0 if complete else 1
        lines_total += len(lines)
        events.extend(process(store, tally(lines, miner, b, total, complete)))
        b += BUCKET
        if i % 36 == 0:
            print(f"  {b:%m-%d %H:%M}  {i}/{n}  Zeilen={lines_total}  Muster={miner.cluster_count}"
                  f"  Ereignisse={len(events)}  {time.time() - t0:.0f}s", flush=True)

    mails = [e for e in events if e.mail]
    print(f"\n== {n} Fenster, {lines_total} Zeilen, {miner.cluster_count} Muster, "
          f"{incomplete} Fenster unvollständig, {len(mails)} Sofort-Mails, "
          f"{sum(1 for e in events if e.action == 'open_digest')} Tagesübersicht-Einträge")
    per_day = Counter(e.bucket_start.strftime("%m-%d") for e in mails)
    print("Sofort-Mails je Tag:", dict(sorted(per_day.items())))
    print("\n== Sofort-Mails")
    for e in mails:
        top = ", ".join(f"{k.split('.')[0]}={v}" for k, v in e.episode.entities.most_common(3))
        print(f"{e.bucket_start:%m-%d %H:%M} {e.action:8} {'+'.join(sorted(e.verdict.rules)):17} "
              f"n15={e.verdict.count15:>6} ent={e.entities15:>3} | {e.template.template[:90]} | {top}")

    print("\n== Prüfung")
    ok = True
    for needle, code, at in EXPECT:
        hit = [e for e in mails if needle in e.template.template and code in e.template.template
               and at <= e.bucket_start <= at + timedelta(minutes=10)]
        print(f"  {'OK  ' if hit else 'FEHLT'} {needle} {code} ab {at:%m-%d %H:%M}"
              + (f" → {hit[0].bucket_start:%H:%M} {sorted(hit[0].verdict.rules)}" if hit else ""))
        ok &= bool(hit)
    dam = [e for e in mails if any("dam.cue.stage" in k for k in e.episode.entities)]
    dam_only = [e for e in dam if len(e.episode.entities) == 1]
    print(f"  {'OK  ' if not dam_only else 'LAUT '} dam.stage allein: {len(dam_only)} Mails")
    ok &= not dam_only
    by_template = defaultdict(int)
    for e in mails:
        by_template[e.template.template[:80]] += 1
    print("\nHäufigste Mail-Muster:")
    for t, c in sorted(by_template.items(), key=lambda x: -x[1])[:10]:
        print(f"  {c:3}× {t}")
    print("\nERGEBNIS:", "BESTANDEN" if ok else "NICHT BESTANDEN")


if __name__ == "__main__":
    asyncio.run(main())
