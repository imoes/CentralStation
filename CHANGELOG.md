# Changelog

What changed in the *product* — new abilities, new screens, changed contracts,
things removed. Not a commit log: commits explain changes to code, this file
explains changes to what people can do.

Every entry names its evidence. An entry without proof is an intention, not a change.

This file starts on 2026-09-17. Earlier history lives in the commit log only.

---

## 2026-11-07 — Log watch, part 34: incidents follow the rule system (definition v7); one mail path

Steps 3 and 4 of the incident refactor you asked for (2026-11-07: "der Incident muss nach dem Logik-Update
(Regel-System) refactort werden"; "mache alle Schritte und teste alle neuen Funktionen auch den
Mail-Versand von Incidents").

### What is new

- **Third action `incident`** in the one rule table (rule = filter + action). An incident rule has:
  - a group: `muster`, `dienst` or `host`;
  - a window in minutes;
  - minimum values: messages, hosts, sources;
  - the kind of incident it opens.

  The per-message verdict (`bewerte`) skips these rules, so relevance is unchanged.
- **The four current ways are standard rules, in test mode** (definition v7):

  | Rule | Filter | Group / window / minimum | Old path |
  |---|---|---|---|
  | I-AUSBRUCH | `quelle:graylog signal:dringend !signal:schaden` | muster / 30 min / 1 message | log-cluster immediate |
  | I-FLOTTE | `quelle:checkmk zustand:crit` | dienst / 45 min / 5 hosts | fleet |
  | I-KRITISCH | `quelle:graylog signal:schaden` | host / 360 min / 3 messages | critical |
  | I-HOST | `relevanz>=3` | host / 30 min / 2 messages | host incident |
- **Shadow mode** (`incident_regeln.py`, every 5 min, `raster_lauf` "incident-regeln"). For each kind,
  in the window of its own rule, it compares what the rules would open with what the old thresholds
  opened, in three counts: both, only new, only old.
- **Switching is a rule in mode `aktiv`**, as a new definition version, not a second switch:
  - for that kind only the rule system opens and mails;
  - the old paths (`runner`, `fleet`, `critical`, `process_alerts`, and their mails) step aside, but
    keep feeding the raster signals.
- **Active rule incidents:**
  - one incident per key, updated while the key stays a candidate;
  - mailed when new, through the existing `notify.deliver` (AI triage, recipients by host scope,
    recipe) — one mail path;
  - closed after a full window below the minimum values, so they do not hang in limbo.
- **Readable mail subject.** Drain placeholders (`<NUM> <TS> <IP>` …) are removed, also on the old path.
  Rule mails name rule, filter and values, and link the incident in the error network graph.

### Evidence (2026-11-07, live data, new code in a one-off container)

- **Shadow run:** 9 candidates (I-AUSBRUCH 2, I-KRITISCH 2, I-HOST 5); a run takes 5 s.

  | Kind | both | only new | only old |
  |---|---|---|---|
  | log_cluster | 2 | 0 | 1 |
  | critical | 0 | 2 (jump012, mon02) | 0 |
  | host | 1 | 4 | 15 |

  The 15 host incidents only the old path opened come from routine syslog lines (`dockerd`,
  `salt-minion`) that the rule system judges not relevant. For docker0160 and docker0209 the history
  holds 0 messages in that window. The old path opens incidents from noise; the rules do not.
- **Measurement corrected on the way.** The first comparison counted the old side over the largest
  window (6 h) for every kind and showed 11 and 46 "only old". The windows are now per kind.
- **Mail, new path, end to end:** a rule mail for the real candidate I-KRITISCH on jump012 was sent to
  thomas.kluge@example.org and accepted by the relay.
  - Subject: "[CentralStation] Incident: Kritische Kernelmeldung gehäuft: jump012.example.com · 1 Hosts
    · 28 Meldungen".
  - Reason: "Regel I-KRITISCH … quelle:graylog signal:schaden — 28 nachrichten (mindestens 3) in
    360 min".
  - 3 examples from the history.
- **Tests:** `test_log_watch_incident_regeln.py` (6) and `test_log_watch_regelwerk.py`; full suite 842
  passed.

### What is still missing

- **Not deployed.** The restart guard stopped because a user console is running. Definition v7 is
  already in the database; the running code keeps its v6 rules until the deploy.
- **Not switched.** All incident rules are in test mode. Switching follows when the shadow mode has
  produced several hours of comparison numbers; they will be shown before switching.
- **The fleet rule has no "≥ 3× the 7-day median"** as the old one has. A daily recurring job could
  open an incident that the old path held back.
- **The critical rule counts messages, not five-minute windows.** The history stores several lines
  per window.

---

## 2026-11-07 — Log watch, part 33: an incident is an object of the search; an open ticket means handled

You asked (2026-11-07): an incident must be an object of search and filter, and shown in the network graph;
and incidents must follow the logic of the rule system. These are steps 1 and 2 of that refactor; steps 3
and 4 (incident rules in the rule system, one mail path) follow.

### What is new

- **An incident is a filter** in the one filter language (`incident_objekt.py`):

  | Kind | Filter |
  |---|---|
  | `log_cluster` | `muster:` of its Drain patterns, across all hosts |
  | `fleet` | `dienst:"<service>"` on its hosts |
  | `critical` | `host:<host> signal:schaden` |
  | `host` | `host:<host>` |

  Messages in its window (from 15 min before it opened) carry `incident:<id>`, the same name as on
  console sessions. A job every 5 minutes marks active incidents.
- **Incidents are nodes of the error network graph:** a triangle with title, kind and status, and a
  button that opens the incident with its timeline.
- **An open ticket on an incident means handled (N2).** Messages its filter matches get
  `signal:behandelt` with the reason "Ticket IMIT-… am Incident …". This is fed to the raster per run as
  current state, not as part of the versioned definition. Before, N2 knew only CheckMK acknowledgements
  and downtimes.

### Evidence (live, 2026-11-07)

- **First run:** 329 active incidents in 11.3 s; 2 170 messages marked; 0 without a filter, 0 errors.
- **The error graph shows 4 incidents** with titles, e.g. "Fehlerausbruch: … postfix/main.cf … ·
  29 Hosts" (log cluster, 75 messages) and "mu-img-atlas01.example.com: 5 Alerts" (host).
- **Mail path of incidents, end to end:** a test mail for the newest real immediate outbreak (incident
  `7613e91a…`) was rendered with the live code and sent through the SMTP connector "Mail Proxy"
  (`mailrelay.example.com`). The relay accepted it (1 sent); the incident link is in the HTML. The only
  recipient with immediate mails switched on is thomas.kluge@example.org.
- Tests: `test_log_watch_incident_objekt.py` (6): filter per kind, the filter hits exactly its messages,
  window, node choice, handled from a ticket; full suite 835 passed.

### What is still missing

- **Lifecycle:** 1 285 host incidents are "open", only 146 were updated in 6 h. They hang in limbo,
  because `resolve_inactive_host_incidents` does not set `updated_at`.
- **No incident has a ticket today** (0 in the database), so "handled" has no live effect yet. A test
  ticket in Jira was not created (an outward action).
- **`mailed_at` is set even when 0 mails went out** (`notify.py`). It is not proof of delivery.
- **The mail subject shows the raw placeholders** of the pattern (`<NUM> <TS> … <IP>`) and links only to
  the feed.
- **Steps 3 and 4:** incident rules in the rule system (test mode against the old thresholds), then one
  mail path.

---

## 2026-11-07 — Log watch, part 32: the infrastructure map becomes an error network graph

You asked (2026-11-07): the infrastructure map is not up to date; the goal is an error network graph like
the research tool's, where you see at a glance where the problems are, and every node is an object
(host or tag).

### What is new

- **`/topology` opens the Fehler-Netzplan.** The NetBox view remains as the switch "Infrastruktur
  (NetBox)" and loads only when it is shown.
- **One search over the history** (`GET /api/log-watch/netzplan?sicht=fehler`). Nodes are problem
  objects: hosts, AI findings, topics, services, rules, MITRE techniques, apps, remote hosts.
  - They are ordered by "urgent and not yet analysed", then by urgent, then by volume.
  - Context tags (source, network, criticality, location) stay filters.
- **Colour = problem state, shape = kind of object:**
  - red: urgent (relevance 3) and on no finding;
  - orange: urgent, analysed;
  - yellow: relevant;
  - a host is a circle, an AI finding a diamond (with its title), a tag a rectangle.
- **One sentence above the graph** says where the problems are, e.g. "8420 relevante Nachrichten in
  24 h · dringend und ohne Befund: cue0149 (16), cue0111 (15), cue0148 (13)."
- **A click selects an object** (as in the research tool). On the right you see:
  - its stage and numbers;
  - its AI findings and linked objects;
  - its messages;
  - buttons *Filtern*, *Ausschließen*, *Cockpit*.

  Without a selection the right column lists the hot spots, red first. The search is in the URL
  (`?modus=fehler&filter=&stunden=`).

### Fixed

- **"Handled" had two meanings.**
  - The AI check (`trichter.offene`) treated everything an analysis' start filter matched as seen.
  - Only the 2–3 cited messages carried `befund:<id>`.

  Now everything the analysis saw carries the finding's hashtag (`bekannt.markiere_gesehen`, same
  definition). The backfill covered 1 609 messages from 388 findings; 6 findings had no stored start
  filter and were skipped.
- **Node choice by volume** filled every node with context tags (`netz:intern` 6 075,
  `kritikalitaet:prod` 5 872 …) and showed no host.

### Evidence (live, 2026-11-07, logged in as a real user)

- `/topology` renders the graph and the sentence; the right column lists the hot spots
  (`regel:533`, `cue0148`, `nsa383` …).
- **Stages before and after the backfill** (same 24 h):
  - 22 red and 0 orange → 18 red and 9 orange;
  - unhandled urgent on cue0148: 87 → 13; on cue0111: 67 → 15;
  - findings shown as nodes: 0 → 4.
- Graph query 0.15 s.
- Tests: `test_log_watch_suche.py` (problem state, sentence, choice of problem objects),
  `test_log_watch_bekannt.py`, `test_log_watch_trichter.py`; full suite 830 passed.

### What is still missing

- ~~Backend not yet redeployed~~ — correction: deployed 2026-11-07 11:07 UTC after you approved stopping
  the user container; new findings now mark what they saw.
- **Incidents are not yet objects** of the graph and search (see the design proposal).
- No suggestions while typing in the graph's filter input (the feed has them).

---

## 2026-11-07 — Log watch, part 31: a finding you can read in seconds

You said (2026-11-07): the output of the findings must be formatted better; "with this output I can't
do anything". The example in the cockpit began with five raw finding IDs, followed by one paragraph mixing
the statement, measurements, quotes and a Wazuh side note.

### What has changed

- **The AI files a finding in a fixed form**, and the code checks it like the quotes:
  - `kern`: one sentence of at most 300 characters: what, where, since when, how bad;
  - `fakten`: at most 8 short points, each a value with its time;
  - `folge_von`: earlier findings about the same situation. Finding IDs in the text are moved there
    automatically.
- **The AI tile shows it that way:** core sentence in bold, facts as a list, "Folgebefund · 5 frühere"
  as a small marker with the IDs in the tooltip. Hypotheses show at most two lines, and evidence plus
  the analysis path are folded away.
- **Older findings** are split as far as possible: references out, first sentence as the core, the rest
  under "Details".
- The KPI row of the AI fleet tile has six columns. The new figure "ohne KI (bekannt)" had wrapped and
  pushed the "Gelernt" line out of the tile.

### Evidence

- Rendered with the real example (mon03, "OMD mue performance") against API mocks:
  - the core sentence is in bold;
  - the marker shows 5 earlier findings;
  - there are 4 facts with times;
  - the old LayoutChampApp finding shows its first sentence as the core and the rest under "Details".
- Tests:
  - `test_log_watch_befund.py` (14), e.g. "Folgebefund zu ki:cc5562691e, ki:9ead2d2e92, …: der
    Check …" becomes "der Check …" plus three references;
  - Node checks of `befund-form.ts`: "z. B." and "100.00" do not end a sentence;
  - full backend suite 829 passed.

### What is still missing

- Live proof that the model keeps the form: shown after the deploy by the first new findings.

---

## 2026-11-07 — Feed search like Graylog: add or exclude a value, top values, suggestions

You asked (2026-11-07): search in the feed like in Graylog, right-click a field to add it to the search
or exclude it; and suggestions while typing, like the research tool.

### What is new

- **Field menu** on the feed: right-click source, severity, host or location in a card header, or
  click any line under the new **Felder** list (`source`, `tags`, every `metadata.*` field).
  - *Zur Suche hinzufügen* (`feld:wert`, AND) and *Aus Ergebnis ausschließen* (`NOT feld:wert`) as in
    Graylog 5.2. The rules are ported from Graylog's source (`QueryHelper.escape`, `addToQuery`,
    `AddToQueryHandler`, `ExcludeFromQueryHandler`, branch 5.2).
  - *Neue Suche nur mit diesem Wert*, *Top-Werte dieses Felds*, *Wert kopieren*.
- **Top values** of a field within the current search, with "other values" and "field missing".
- **Suggestions while typing**: up to 9 complete queries with the hit count of that exact query.
- **The search is in the URL** (`?q=&range=`).

### What has changed

- A query OpenSearch cannot read is now an error with its reason, shown under the search bar. Before,
  it was "0 hits".
- The banner shows the total number of hits, and says when only the newest 200 are listed.

### Fixed

- **Syntax errors hidden by the time range.** With a time range, OpenSearch answered `source:(` with
  HTTP 200, 0 hits and 4 of 7 shards failed; this now counts as an error.
- **Empty value ≠ missing field.** Graylog's escaping turns an empty value into `feld:`; here it is
  `feld:""`.

### Evidence (live, read-only, 2026-11-07, last 24 h)

- `source:graylog`: 17 667 hits in 0.01 s; `source:(` → "Failed to parse query [source:(]".
- Top values of `metadata.container_name` within `source:graylog`: empty value 16 379, field missing
  114, other values 602.
  - Cross-check of the right-click terms: `metadata.container_name.keyword:""` finds 16 379, and
    `NOT _exists_:metadata.container_name.keyword` finds 114. Both are the same numbers as the top
    values.
- Suggestions, 10–40 ms each: `source:` → graylog 17 667, checkmk 1 392, wazuh 813, coroot 27;
  `severity:crit` → `severity:critical` 599; `source:graylog AND cue` → `tags:cue` 5 129, then hosts
  `cue0222` 269, `cue0115` 215 …
- A terms aggregation with a regex include on `source` failed on `cs-feed-checkmk` and
  `cs-feed-coroot` (`index_out_of_bounds_exception`); with `execution_hint: map` no shard fails and the
  counts are unchanged.
- Tests: `test_feed_suche_graylog.py` (10), Node checks of `feed-query.ts` (escaping, add/exclude,
  empty vs missing, fields of an item); full backend suite 825 passed, 142 skipped.

### What is still missing

- **Two search languages:** the feed uses Lucene (as Graylog does), the log-watch history its own
  hashtag filter language. A right-click in the cockpit graph does not exist yet.
- **No "did you mean …?"** for typos, and suggestions are ordered by count, not by AI relevance as in
  the research tool.
- **Paging** of search results: the newest 200 are listed, the total is named.

---

## 2026-11-07 — Log watch, part 30: the funnel learns — a known error no longer goes to the AI

You asked (2026-11-07): "Wenn die KI bereits ein Problem untersucht hat und der selbe Fehler wieder
auftaucht, dann muss die KI nichts mehr machen." And: every object is the result document of a search,
with fields like host and tags.

### What is new

- **Known errors** (`bekannt.py`, OpenSearch index `cs-log-bekannt`).
  - After every finding, each error the analysis saw points to that finding. "Saw" means the case's
    situation plus the cited evidence; messages already attached to another finding are not taken
    from the situation.
  - **Same error** = same error key, built from the group key the raster already writes on every
    message: `muster:<drain>` across all hosts (Graylog, switch), and per host `regel:` (Wazuh),
    `dienst:` (CheckMK), `bedrohung:` (firewall).
  - A repeat is attached to the finding by the agent, without AI: hashtag `befund:<id>`, the finding's
    `ai_relevanz`, `ai_provenance.bekannt`. The filter `befund:<id>` now shows evidence and repeats.
  - **Exception:** a repeat with a criterion R1–R8 or `signal:dringend` that the error did not have at
    its finding goes to the AI, named `neu_seit_befund`. This is the plan's N2 rule ("relevant again
    only when an R appears that was not there when it was handled").
- **A known error is an object:** its `filter` in the filter language (`muster:x` or
  `host:h dienst:"Filesystem /"`), `host`, `hashtags`, finding, relevance, status, repeats, hosts.
- **Human correction:** `GET /api/log-watch/bekannt`; `POST /api/log-watch/bekannt/{id}/vergessen`
  with a reason (admin, audited) sets the status `widerlegt`, and the AI analyses the next occurrence.
- **Cockpit:** the AI tile shows "ohne KI (bekannt)" and the number of active and refuted known errors.

### Evidence

- **The group key is always a hashtag:** measured 2026-11-07 on all 7 677 messages of the history
  (wazuh 5 209, switch 1 040, graylog 769, checkmk 426, firewall 233): 0 without a key, 0 whose key is
  not also a hashtag. A test checks for ten keys (with spaces, parentheses, a trailing `*`) that the
  object's filter hits exactly the messages with the same error key.
- **Backtest over the 388 findings of the last 7 days**, in time order, counting only cited evidence
  (the situation of old analyses is not stored, so this is a lower bound):

  | | Findings |
  |---|---|
  | all errors already known from an earlier finding (no AI run needed) | 193 (50 %) |
  | partly known | 96 |
  | new | 99 |

  Only 20 cited messages would have gone back to the AI because of a new criterion.
- **Tests:** `test_log_watch_bekannt.py` (7), funnel and cockpit tests extended; full suite 815 passed,
  142 skipped.

### What is still missing

- **A screen for known errors** (list, open the filter as a search, "forget" with a reason). Today only
  the API and the counters in the AI tile.
- **Similar but not identical errors** (vector, "similar to …") still go to the AI unmarked.
- **Rule suggestions from filter chains** (plan: a chain that led to a confirmed finding becomes a test
  rule) are not built.

---

## 2026-11-07 — Log watch, part 29: the funnel reads CheckMK, tickets and the knowledge base

You approved (2026-11-07, "Ja mache das") a small read-only set beyond the history.

### What is new

- Three more tools for the AI funnel, with the MCP server's names and the same code:
  - `get_checkmk_host`: live CheckMK state; in the funnel only services not OK, the number OK, and
    CPU/RAM/disk of the last 2 h.
  - `search_tickets`: Jira and ServiceDesk by free text; an open ticket means N2 (handled).
  - `search_knowledge_base`: quick search in the knowledge base; no deep search (measured ~280 s).
- Each call waits at most 30 s; a timeout or an outage is a named error, never an empty result.
- Their results are context; evidence for a finding stays history messages only.
- A tool that cannot work is not offered, with the reason.

### What changed

- The MCP tools `get_checkmk_host` and `search_knowledge_base` (quick search) now call the shared
  functions `checkmk_metrics.host_status` and `aikb.search_kb`, so funnel and MCP have one
  implementation.

### Evidence

- **Live, read-only (2026-11-07, docker0218.example.com):**
  - `get_checkmk_host`: 1.9 s, 36 services OK, 0 not OK, 6 vitals; 450 characters in the funnel.
  - `search_knowledge_base`: 25.6 s, 3 articles (first: "[KB] docker0218.example.com").
  - `search_tickets`: searched nowhere. Both Jira connectors (Jira, ServiceDesk) are personal
    (`owner_user_id` set); there is no global one.
- **Tests:** `test_log_watch_werkzeuge_extern.py` (6).

### What is still missing

- **Ticket search in the funnel is off** until there is a global Jira/ServiceDesk connector. The funnel
  runs without a user and does not use anyone's personal credentials. The tool is therefore not
  offered, and the reason is named.

---

## 2026-11-07 — Log watch, part 28: tool answers in the view of the situation — smaller and complete

Approved 2026-11-07 ("Ja") after the strata measurement (part 26): fewer new prompt tokens per step are
the only lever for more cases per hour.

### Fixed

- **The AI received truncated tool answers and, in most searches, no facets at all.**
  - Measured on 40 real `lw_suche` calls from the last hours: median 10 246 characters raw.
  - 29 of 40 were longer than 6 000 characters and were cut blind at that limit. The JSON broke off in
    the middle of a hit.
  - The facets come after the hits in the answer, so they were lost in every cut answer.

### What has changed

- **Tool answers in the funnel use the same view as the worker's situation**
  (`hostobjekt.kuerze_antwort`):
  - `lw_suche`: at most 6 hits. Each hit has ID, time to the second, source, **host** (new, also in the
    situation), relevance, at most 2 reasons, at most 5 hashtags, and at most 300 characters of text.
  - The shortened text is a prefix of the original, so a quote from it still passes the quote check.
  - The answer names what was left out: `gesamt` (all matches), `gezeigt`, and a note that
    `lw_nachricht(id)` returns the full message.
  - Facets: top 5 values per kind, always complete.
  - `lw_facetten`: top 5 values per kind, for all kinds.
  - `lw_gespraech`: the same view as the host object's memory (human, AI and finding turns, 300
    characters each, origin and reference).
- The 6 000-character limit stays only as a last safeguard, and it still names the cut.
- The MCP tools for the console are unchanged and still answer in full.

### Evidence

- **Same real calls, old (capped at 6 000) → new:**

  | Tool | Old | New | Saving |
  |---|---|---|---|
  | `lw_suche` | 190 622 characters | 120 479 characters | −37 %; largest answer 5 316 characters, so nothing is cut |
  | `lw_facetten` | 156 966 | 40 030 | −74 % |
  | `lw_gespraech` | 216 167 | 120 636 | −44 % |

- **Per hit before:** text 213, reasons 106, hashtags 91, ID 41, time 30 characters on average.
- **Tests:** at most 6 hits, host present, text quotable, facets never cut, cut named; a 50 000-character
  answer stays below the safeguard.
- **Backend suite:** 802 passed, 142 skipped.

### What is still missing

- **Measured after the rollout (07:57–08:57 UTC, 30 cases) against 05:34–07:34 (67 cases): more
  complete, not faster.** My estimate of "a quarter to a third more capacity" was wrong.

  | | Before | After |
  |---|---|---|
  | Cut tool answers | 137 `lw_suche` (plus 31 others) | 0 `lw_suche`; 7 others (`lw_nachricht` 4, `lw_netzplan` 2, `lw_gespraech` 1) |
  | `lw_suche` per call | 5 024 characters | 3 003 characters |
  | Steps per case | 3.7 AI turns, 2.9 searches, 0.2 full messages | 5.3 AI turns, 3.8 searches, 0.5 full messages |
  | Characters per case | about 33 800 | about 33 100 |
  | Cases per hour | 33.5 | 30 |
  | Findings per hour | 21–33 | 29 |
  | strata tokens read per hour | 754 000 | 590 000, all clients |

  - The AI makes up for the shorter answers with more calls, so the volume per case stays the same.
  - The lower strata load comes from fewer cases, not from smaller cases.
  - Limits of this comparison: one hour against two, a different time of day, a different mix of
    cases.
  - What remains is the quality gain: the AI now sees complete answers with facets instead of JSON cut
    off mid-hit.

---

## 2026-11-07 — Log watch, part 27: console sessions with a reference become memory

### What is new

- **Console sessions with a reference are written to the conversation protocol** (`cs-gespraech-*`),
  by decision of 2026-11-07: "Nur die Konsolen mit Bezug."
  - A reference is an incident (`incident:<uuid>`), an alert (`alert:<external_id>`) or a ticket
    (`ticket:<key>`). Sessions without one stay in the user container and are not stored.
  - The source is the same history the console shows (`/sessions/{sid}/history` in the user
    container). It is not a second copy of the SSE stream.
  - After every console answer the whole session is written again. The document ID `session:step`
    makes this idempotent.
  - Only what the human and the console AI wrote is stored: roles `mensch` and `ki`. Tool output and
    system prompts are not stored.
- **Hosts of a session:**
  - Incident: the primary host plus the hosts of its error cluster.
  - Alert or ticket: the session name, but only if the host directory knows it.
  - Names are canonical, the same as `host:` in the history.
- **Searchable like every conversation:** `quelle:konsole`, the reference tag and `host:` per host.
  `lw_gespraech` and the host object's `gedaechtnis` therefore also return what people clarified in
  the console.
- **The funnel AI sees the origin:**
  - Each conversation in `gedaechtnis` carries `herkunft` ("konsole" or "ki-analyse") and `bezug`.
  - The system prompt says to use console conversations as context, never as quoted evidence. The
    quote check accepts only stored messages anyway.

### Evidence

- **Tests** (`test_log_watch_konsole.py`): no reference means the container is never asked; only human
  and AI text is kept; the hashtags carry origin, reference and host; the host object names the origin.
- **Backend suite:** 800 passed, 142 skipped.

### What is still missing

- **Existing sessions are not backfilled.** 14 of 53 console sessions have a reference. Their history
  lives in the users' containers, and those containers are stopped. A session is written the next time
  someone continues it.

---

## 2026-11-07 — Log watch, part 26: why the fleet waits in front of strata (measurement)

No code change. A read-only look at strata on llm-host03 (docker0220), approved 2026-11-07. Window:
05:34–07:34 UTC.

### Evidence

- **strata configuration:**
  - `PARALLEL=4`, `CONTEXT=128k`, engine flags `--batch 4 --kv-resident 32768 --expert-cache 9000
    --resident-budget-gib 45 --spec 4`.
  - Model: `Qwen3.8-Flash-Next-UD-IQ4_XS`.
- **Two logs, not one:**
  - The `print_timing` lines in `docker logs strata-27b` are a translation by `/data/perflog.sh`
    (awk) of the real engine log `/data/state/strata/launch/aikb.engine.log`.
  - The translation covers prompt reading and single-slot decode only. It leaves out batched decode
    (`strata batch: N windows, avg R rows, … ms/window`).
- **Engine log over the last 431 prompts (about 2 h):**
  - Prompt reading takes 2 545 s for 1.51 million new tokens, at 593 tokens/s.
  - Prompt reading is **serial**: no two prompt reads overlap.
  - 65 % of all prompt tokens were reused from the cache.
  - Decode: 585 s single and up to 2 854 s batched, with 3.0 rows on average.
  - Together up to about 6 000 s of work in 7 176 s of wall time. One batch phase may have started
    before the window, so this is an upper bound.
- **Clients** (nginx, `/qwen27b`, 2 h):

  | Client | Requests | Response time |
  |---|---|---|
  | CentralStation (10.0.254.10) | 253 | 13 943 s total, 55 s average |
  | aikb.test (10.0.28.119) | 115 | short classifications, 99 s of prompt reading in total |
  | ansible01.example.net | 10 | – |

- **Who reads the prompts:**
  - Prompts of 500–30 000 tokens (the tool loops) account for 2 446 s of the 2 545 s.
  - Our 67 sessions in the same 2 h wrote about 2.26 million characters (about 1.2 million tokens):
    - worker prompts 787 000 characters (11 400 per case);
    - `lw_suche` results 971 000 (195 calls, 2.9 per case, 5 000 characters each).
- **The extra searches are not repeats.** They deepen the case: the same service over 3 days,
  neighbouring switches, `apt-daily-upgrade` over 14 days.

### Corrections

- **Part 23 said "strata computed only 587 s in 1 200 s; the waiting is unexplained (hypothesis: a free
  slot)". That was wrong.** The 587 s came from the translated log, which does not contain batched
  decode.
- **The cause of the waiting:** strata is close to saturated, and prompt reading is serial. A new step
  waits until the prompt in front of it has been read (5–20 s per prompt).
- **More agents therefore bring no throughput.** The lever is fewer new prompt tokens per step.

---

## 2026-11-07 — Log watch, part 25: one oversized text no longer blocks the vectors of others

### Fixed

- **6 history documents had no vector, and the catch-up failed on every run.**
  - The embedding server (`llama-embed`) logged: "input (2164 tokens) is too large to process …
    (current batch size: 2048)".
  - One Graylog document from mon-beta has 4 702 characters; cut to 4 000 that is still 2 164 tokens
    (1.85 characters per token for log text).
  - The server rejects the whole batch, so 5 short documents from cue0111 (125–132 characters) failed
    with it. The catch-up retried the same batch every run.
  - The comment "bge-m3 kann 8 192 Token" was right about the model but wrong as the limit: the server
    accepts at most its batch size of 2 048 tokens per input.
- **Fix:**
  - The vector input is cut to 3 000 characters (about 1 620 tokens); the full text is still stored.
  - If a batch fails, the catch-up embeds one by one. What still fails is counted as
    `nicht_einbettbar` with its ID.

### Evidence

- **Embedding requests from our backend in 70 minutes** (nginx log on llm-host03): 85 × 200, 4 × 500.
- **Tests:** a "poison" document no longer stops the other two, and the cut stays below the server batch
  size.
- **Backend suite:** 937 passed.
- **Queue order (part 24), live:** since the rollout, 1 of 51 trigger messages left the window
  unanalysed, against 10 of 87 before. That one was urgent.

---

## 2026-11-07 — Log watch, part 24: urgent cases first — what expired was the most important

### Fixed

- **Exactly the most urgent messages expired.**
  - Measured 00:33 UTC: of 87 trigger messages that left the 2 h window, 10 were never analysed.
  - All 10 were **CRIT on production hosts** (R1, `signal:dringend`): mssql014, mssql02, the domain
    controllers vm-dhwad1/vm-dhwad2, switch nsa352, among others.
  - They were single-message cases, crowded out by loud cases (dmzdockerp01 with 26 messages), because
    the queue was ordered by size.
  - **New order:**
    1. urgent first (`signal:dringend`);
    2. then the oldest message first, the one closest to expiring;
    3. then size.

    Regression test included.

### Evidence

- **Corpora, last hour:** 0 errors. Everything written: Wazuh 597/597, Graylog 108/108, switch 77/77,
  CheckMK 36/36, firewall 28/28.
- **AI:** 32 findings in the last hour.
- **Queue** (UTC):

  | Time | Messages | Cases | Expired |
  |---|---|---|---|
  | 23:40 | 116 | 41 | 1 |
  | 23:50 | 104 | 39 | 1 |
  | 00:00 | 106 | 40 | 1 |
  | 00:10 | 73 | 32 | 5 |
  | 00:20 | 58 | 34 | 2 |
  | 00:30 | 116 | 37 | 0 |

  17 of 40 cases had a single message.
- **Backend suite:** 935 passed. Rolled out together with the hub filter (part 22).

---

## 2026-11-07 — Log watch, part 23: where the fleet's time goes (measurement)

No code change. A measurement of steady-state operation (2026-11-06 23:44 to 2026-11-07 00:04 UTC, with
no rollout in between) that corrects earlier statements.

### Evidence

- **Corpora, last hour:** 12 runs per source, 0 errors. Everything written: Wazuh 701/701, switch
  119/119, Graylog 112/112, CheckMK 68/68, firewall 18/18. No document without a vector.
- **Fleet:** 10 findings in 19 minutes (about 32/h), 3 agents throughout.
- **Queue:** 116 → 104 → 106 messages; 41 → 39 → 40 cases; about 1 expired message per 10 minutes.
  Inflow and processing roughly balance, but the backlog does not shrink.
- **Model time per case:** median 239 s and 7.2 steps (n = 10).
  - The final step (filing the finding) takes median 140 s, 60 % of a case.
  - The other steps take median 18 s.

### Corrections

- **"131 s per case without `lw_wissen`" (part 21)** came from only 3 cases. In steady state it is 239 s.
- **The time is not spent computing.**
  - strata's longest generation in the period: 34 s total, 725 tokens at 40–56 tokens/s.
  - The prompt cache works: on average 12 178 of 16 724 tokens cached, reading the prompt takes 7.6 s.
  - strata computes in parallel (66 of 81 requests overlap) and computed only 587 s in 1 200 s, for all
    clients together.
  - nginx measures our 65 requests at an average of 49.6 s and at most 216 s, together about 3 220 s.
    At most a fifth of that is computation.
  - The rest is waiting between nginx and the start of computation in strata. strata does not log it,
    so the cause is open (hypothesis: waiting for a free slot).
- **Consequence:** more agents bring no throughput at present. The fleet stays at 3 until the waiting
  time is explained.

---

## 2026-11-07 — Log watch, part 22: the real cockpit shows the core; hub nodes hidden

### Fixed

- **The cockpit core was missing graph and AI fleet whenever the LLM picked `lw_befunde` itself.**
  - Found on the real cockpit at 23:40 UTC: findings, criteria, error clusters, incidents, top hosts —
    no graph, no AI fleet.
  - Cause: both were nested behind the check for `lw_befunde`. Every core tile is now ensured on its
    own, with a regression test for exactly that case.
- **A hub node dominated the graph.** `netz:intern` sat on almost every message and therefore at the
  centre, connected to everything.
  - Hashtags on more than 80 % of a result's messages carry no information about connections. They are
    now hidden, except when they are in the filter.
  - The tile names how many were hidden (`ausgeblendet_naben`).

### Evidence (live, 23:43 UTC)

- **Real cockpit after the fix** (dashboard `62e00e26…`):

  | Position | Tile |
  |---|---|
  | (0,0) | findings |
  | (6,0) | graph of hot spots |
  | (0,5) | AI fleet |
  | (6,5) | criteria |
  | (0,8) | error clusters |

- **Browser check with the real data:**
  - Five findings at relevance 3, for example "orderserver-Instanzen ORD1–ORD5 auf aomp05-v (prod) alle
    0 Prozesse" and "vpp0218: /data/backup vollläuft in ~1 T 11 h".
  - One shows the memory at work: "Core-Dump-Muster … neu nur auf cue0112, cue0111 bereits gedeckt".
  - AI fleet: 53 findings, 57 cases, 4 without finding, 3 expired, 202 s per case. Queue 116 messages /
    41 cases: "die KI kommt nicht nach".
- **Cause of the queue growth (hypothesis):** three drain rollouts slowed the fleet for about 15 minutes
  in total. Rollouts are now bundled so steady-state operation can be measured.
- **Backend suite:** 934 passed. The hub filter is not rolled out yet (bundled with the next rollout).

---

## 2026-11-07 — Log watch, part 21: the AI cockpit gets a fixed core

### What changed

- **Fixed core of the AI cockpit.** Whenever there is a finding or a message waiting for one, two rows
  come first, enforced by code:
  - row 1: **findings** next to the **graph of hot spots** (`relevanz>=3`);
  - row 2: **AI fleet** ("keeping up?") next to **verdicts per criterion**.

  Running error clusters, corpus health and the AI's own picks follow.
- **Area budget 110 → 150 cells, now a named constant** (`AREA_BUDGET`). The core takes 96 cells and
  running error clusters 48. With 110, the core partly dropped out during every outbreak, and `lw_ki`
  was not in the ranking at all (last place).

### Evidence

- **Test:** with an outbreak and 5 more AI picks, the core still sits at (0,0) / (6,0) / (0,5) / (6,5),
  followed by the error clusters.
- **Fleet without `lw_wissen` (first 3 cases after 23:27 UTC):** 131 s model time and 5.3 steps per
  case, against 247 s and 7.7 before.
  - The first steps now go deeper (`lw_nachricht`, `lw_gespraech`) instead of into the situation already
    supplied.
  - 3 cases is a small sample.
- **Queue:**

  | Time (UTC) | Messages | Cases | Expired |
  |---|---|---|---|
  | 23:10 | 80 | 40 | 2 |
  | 23:20 | 75 | 38 | 0 |
  | 23:30 | 67 | 35 | 0 |

- **Backend suite:** 932 passed.

---

## 2026-11-06 — Log watch, part 20: the fleet in operation; correction to part 18

### Correction to part 18

Part 18 justified the host object as the prompt with a saving: "19.9 s of 62.2 s model time spent
gathering the situation". **Measured, that saving did not happen:**

- **First run with host objects (22:53):**
  - 256–294 s and 7–12 steps per case; before, 140–254 s.
  - Prompts were 6 000–20 000 characters.
  - 3 of 4 cases started with `muster:`, while the situation in the object was searched per host. The
    model therefore still called `lw_wissen`/`lw_suche`/`lw_facetten` with the pattern.
- **Fix:** the situation now sits once on the case, from its start filter (`hostobjekt.fall_lage`).
  The hosts carry only fields, neighbourhood and memory. Prompts are now mostly 3 100–4 100 characters.
- **Even after that, all 6 measured sessions started with `lw_wissen`.** Memory in the prompt plus a
  tool for the same memory are two ways to one result, and the model took both. The funnel no longer
  offers `lw_wissen`; the console keeps it via MCP, and `lw_gespraech` stays for reading further back.
- **The real bottleneck is the last step, writing the finding:** 104–161 s, about 60 % of a case's
  model time. Three agents share strata's compute.

### Evidence for the fleet (live)

- **3 agents at once on disjoint cases**, each with its own lease (e.g. 23:22: vpp0242, mon03,
  `muster:16016f9a…`).
- **Throughput:** 6 analyses in 11 minutes after the rollout (about 33/h). Before: 18 findings in
  46 minutes (about 23/h) with sequential or pairwise runs.
- **Queue:** 23:10 80 messages / 40 cases, 2 expired (named for the first time); 23:20 75 / 38,
  0 expired.
- **Drain before a rollout:** with the switch off, agents finish their case. At 23:06:23 3 leases were
  open; at 23:13:57 none. Rolled out 23:14:16, fleet back on. No analysis was cut off.
- **Last 2 h** (`lw_ki`, live): 39 cases analysed, 35 findings, 0 errors.
- **Backend suite:** 931 passed.

---

## 2026-11-06 — Log watch, part 19: the AI works as a fleet of agents

### What's new

- **Agent fleet** (your decision 2026-11-06: at most 3 agents, only the configured model).
  - Because the context lives in the database, any agent can take any case.
  - Every minute one agent may start, at most 3 at once.
  - Each agent takes the next free case until the queue is empty or its 480 s budget is used up.
- **Leases in Postgres** (`ki_lease`, migration 0061). A case leases all its hosts or none. The work is
  serial per conversation (host) and parallel across conversations. A crashed agent's lease ends after
  900 s.
- **Queue measured separately**, every 10 minutes (row `ki-pruefung`: open, cases, expired). The curve
  has points even when no agent works, and `verfallen` counts once per cycle instead of once per agent.

### Fixed

- **Deferred cases counted as "without finding".** In the parallel run of 22:43 two cases were never
  started because of the time budget, yet counted as `ohne_befund`. An agent now only takes a case it
  still has budget for.

### Evidence

- **Parallel run of 22:43** (2 at once, before the fleet):
  - 6 findings in 463 s, against 4 in 355–379 s with 1 at a time.
  - 140–254 s per case, against 62–161 s, because the parallel requests share strata's compute.
  - Queue 77 messages / 37 cases.
- **Backend suite:** 930 passed. Live check after the rollout.

---

## 2026-11-06 — Log watch, part 18: the agentic mode gets a conversation history; its prompt is a search result

### What's new

- **Conversation protocol per host** (your design, 2026-11-06).
  - Every step of an analysis is stored in `cs-gespraech-*`: the worker's prompt, AI turns with
    model seconds, tool calls and results, and the finding.
  - Loading the protocol is a search, `host:<name>`. It returns every conversation about the host, for
    any problem.
  - The steps carry the host's fields as hashtags (`ort:`, `kritikalitaet:`, `netz:`, `hg:`) plus the
    case and `befund:<id>`.
  - The protocol is written even when an analysis fails.
- **The worker searches first; the model reads the result as its prompt.** It gets one host object per
  affected host:
  - master data from CheckMK;
  - the situation: relevant messages with ID and quotable text, plus facets;
  - graph edges;
  - memory: earlier findings and conversations.

  Tools are only for going deeper.
- **New tool `lw_gespraech`** in the funnel registry and on the MCP server.

### Evidence

- **Why the worker searches first** (measured on the 20 findings so far): every analysis began with
  `lw_wissen`, followed by `lw_suche` (14×) or `lw_facetten` (6×). That cost 19.9 s of 62.2 s model time
  before the finding step.
- **Tests:**
  - The protocol of an analysis has the steps worker → ki → werkzeug → ki → befund, in one session,
    numbered in order.
  - The prompt contains the host object.
  - The steps carry `host:`, `muster:`, `ort:`, `kritikalitaet:`, `netz:`, `hg:` and `befund:`.
  - When the model fails, the worker's step is still stored.
  - Unknown roles are rejected.
- **Backend suite:** 930 passed.

---

## 2026-11-06 — Log watch, part 17: AI analysis statistics in the cockpit; nothing expires silently

### What's new

- **Cockpit tile `lw_ki`, "KI-Analyse: kommt sie nach?"** It shows, per funnel run:
  - the queue (open messages and cases),
  - cases analysed, findings, cases without a finding, errors,
  - **expired** messages,
  - model seconds per case,
  - the trend of the queue in plain words ("wächst" / "kommt nach").

  It always comes with `lw_befunde`, because whoever sees findings must also see whether the AI keeps up.

### Fixed

- **Unanalysed messages vanished silently.**
  - The check looks back 2 h; a case not analysed within that time simply dropped out of the queue.
  - Each run now counts `verfallen`: trigger messages that left the window in the last cycle without
    being seen.
- **The AI did not keep up.**
  - Measured 21:25–22:05 UTC: the queue went from 20 to 58 messages (23 cases) at 2 cases per 10 min.
  - Model time per case was 56–101 s, 88.8 s on average over 10 cases.
  - Now 4 cases per run, with a 480 s time budget so runs never overlap.

### Evidence

- **`lw_ki` computed with the new code over the real runs of the last 6 h:** 5 runs, 10 cases analysed,
  10 findings, 0 without a finding, 0 errors, queue +9.
  - "verfallen" is 0 because the older runs did not have the field yet.
- **Browser check** (Playwright, API mocked with that data): 5 KPIs without overflow; the queue is
  marked "wächst".
- **Backend suite:** 926 passed.
- **Rolled out 22:13:52 UTC** (commit `7991cd9`) after the Werkbank container was stopped. Live:
  `lw_ki` shows 5 runs, 10 findings, queue trend +9.

---

## 2026-11-06 — Log watch, part 16: the log-watch tools on the MCP server

### What's new

- **Six read-only MCP tools:** `lw_suche`, `lw_facetten`, `lw_netzplan`, `lw_nachricht`, `lw_wissen` and
  `lw_definition`. They call the same functions as the AI funnel, so there is one path.
  - The console AI and external agents now search the tagged history, its facets and the graph, and read
    earlier findings.
  - `lw_befund` is deliberately not an MCP tool. Findings are written only by the funnel, which checks
    every quote; the console AI stays read-only.
- **The console agents' prompt** (`userenv/main.py`) lists the new tools. They apply to new Workbench
  sessions; running ones keep their old image.

### Evidence

- **Test:** every read-only registry tool is an MCP tool, and `lw_befund` is not (found via the AST of
  `mcp_server.py`).
- **Backend suite:** 925 passed.
- **Live (22:14 UTC):** the MCP server lists `lw_definition`, `lw_facetten`, `lw_nachricht`,
  `lw_netzplan`, `lw_suche` and `lw_wissen`.

---

## 2026-11-06 — Log watch, part 15: CheckMK criticality and location actually arrive; network zone and Wazuh tags as hashtags

### Fixed

- **The host directory had no criticality and no location for any host.**
  - `get_all_hosts` read `attributes.tag_groups`. The REST API delivers tags as `tag_<group>`, and
    inherited tags only under `effective_attributes`.
  - Measured 2026-11-06 over all 3 057 CheckMK hosts:
    - criticality is set directly on 1 199 hosts and inherited by 1 858 (1 823 of them `prod`);
    - location is set directly on 427 hosts and inherited by 2 630;
    - in our directory both were empty for all 3 057.
  - Consequence: `kritikalitaet:`/`ort:` reached only CheckMK messages, never Graylog, Wazuh, firewall
    or switch messages. N3 ("not production") could never apply to them.
  - The connector now reads the effective attributes (`host_aus_config`). This also affects the other
    callers of `get_all_hosts` (`alert_aggregator`, `probe_client`), which now get criticality and
    location for the first time.
- **Wazuh rule groups were cut to 3.** In 24 h this dropped, among others, `recon` (7 106 alerts),
  `syscheck_file` (3 831), `sql_injection` (98) and `attack` (6). Measured over 93 rules: `web_scan`, the
  basis of N4, was never cut, so N4 was not affected.

### What's new

- **Network zone from the CheckMK folder** as hashtags `netz:dmz|intern` and `netzbereich:<segment>`
  (e.g. `dmz-ii`, `web-dmz`), on messages from every source.
  - 167 hosts sit in folders with "dmz" in the path. The tag group `networking` has `dmz` on only 4 of
    them; the rest are on the default `lan`.
  - The folder agrees with Wazuh: the only Wazuh label `host.group=dmz` is on haproxy01.example.net in
    `/kassel/dmz-i`.
  - No change in CheckMK or Wazuh was needed.
- **Wazuh features as hashtags:** all `regelgruppe:`, `mitre-technik:` (T-ID), `label:` (agent labels,
  e.g. `host.group=dmz`), `decoder:`, `logquelle:`.
- **New graph node types:** `netz`, `netzbereich`, `mitre-technik`, `label`.

### What's missing

- **Rolled out 22:13:52 UTC.** The new restart guard had blocked it while the Werkbank container ran; the
  container was stopped at your request.
- **Host directory, live:** 3 057 entries.
  - Criticality: prod 2 142, offline 656, test 122, staging 87, dev 30, critical 18, install 2.
  - Location empty: 0 (before: 3 057).
  - Network zone: intern 2 890, dmz 167.
  - Definition v6 active.
- **Fresh history documents (last 15 min at 22:16 UTC):** 96 documents. These hashtag types appear on
  them:

  | Hashtag type | Documents |
  |---|---|
  | `regelgruppe:` | 151 occurrences |
  | `kritikalitaet:` | 54 |
  | `decoder:` | 51 |
  | `logquelle:` | 51 |
  | `ort:` | 48 |
  | `netz:` | 48 |
  | `mitre-technik:` | 28 |
  | `netzbereich:` | 9 |
  | `label:` | 0 (that window had no alert from the one labelled agent) |

  Part of these documents was written before 22:14 with the old code, so the counts are a lower bound.
- **Criticality `install` (2 hosts)** arrives as a hashtag but no criterion names it.

### Definition v6 (decided 2026-11-06)

- **Not-production (N3) now includes criticality `offline`** (656 hosts): object
  `Nicht-Produktion = kritikalitaet:(test OR dev OR offline)`.
- **Damage (R5) and security (R6) still take precedence over N3.** Test: offline + new pattern → N3;
  offline + damage → R5; install + new pattern → R2.
- **Takeover:** the latest stored version (v5) came from `system`, so v6 is taken over on the next load.

---

## 2026-11-06 — Log watch, part 14: the AI funnel works on cases, and nothing is analysed twice

### What changed

- **The AI funnel works on cases, not on hosts.** Messages with the same pattern (`muster:`) form one case
  across all hosts, so one problem gets one finding. Wazuh and CheckMK messages, which carry no pattern,
  form one case per host.
- **Every finding stores its start filter** (`ai_provenance.start`). A waiting message counts as seen when
  a later finding has a start filter the message carries. The rule is "an analysis has seen everything its
  start filter matched up to its time".
- **The model asks earlier knowledge** with the start filter and with each affected host.

### Fixed

- **Already analysed messages stood in the queue again.**
  - Measured 21:34 UTC: cue0174 (6), cue0149 (4) and cue222 (4) waited again. Every one of these messages
    was older than its host's finding; they only lacked a quote in it.
  - Each run would have analysed the same problem anew.
- **One problem, two findings.**
  - The same ExifTool error in the digicol image pipeline appeared on cue0174 and cue222 within the same
    5 s (22:19:05–09 local time) with the same two patterns (`muster:99e826e2…`, `muster:c30f48fb…`).
  - It produced `ki:934bdfd296` and `ki:e470308875`, because the funnel worked per host and asked earlier
    knowledge only by host.

### Evidence (live)

- **Queue:** 27 messages before the fix, 14 after it (21:37 UTC). The re-listed hosts are gone.
  mon03 (4) stays: those messages are newer than its finding from 21:00.
- **Funnel runs:**

  | Run | Model endpoint | Findings |
  |---|---|---|
  | 21:23 | `/qwen35b` | cue0149, srv032 |
  | 21:34 | `/qwen27b` (strata) | cue0174, cue222 |

  All quotes were checked and none was rejected.
- **Wazuh after the fetch-by-ID fix:** 216 requested, 216 written (4 runs).
- **Backend suite:** 918 passed.

---

## 2026-11-06 — Log watch, part 13: network graph in the cockpit; R7 reaches stored messages; one name for the finding link

### What's new

- **Network graph tile `lw_netzplan`.**
  - Nodes are the hashtags of the relevant messages, at most 5 per type: host, finding, topic, service,
    rule, MITRE and others.
  - An edge means "on the same message", with the count, never mere closeness in time.
  - A click on a node adds it to the filter chain; a chip removes it again. The list on the right is
    the search result for the same filter.
- **Search and graph API:** `GET /api/log-watch/suche` and `GET /api/log-watch/netzplan`.
- **New AI tool `lw_netzplan`.**

### What changed

- **R7 is written into the history.** If a host is relevant in two sources within 20 minutes, its stored
  relevant messages get R7 and relevance 3. The reason says "gemeinsames Auftreten, keine Ursache
  belegt". Before, only the dry-run count knew about R7.
- **One name for the link between a finding and its evidence: the hashtag `befund:<id>`.** The field
  `befund_ids` is gone; the 3 existing documents were migrated. The filter `befund:<id>` now finds the
  finding together with its evidence, and the graph draws that edge.
- **Log-watch tiles do not navigate on click.**

### Evidence (live, 2026-11-06)

- **Wazuh fix confirmed:** the raster runs after 21:10 UTC requested 64, found 64 and wrote 64 (Wazuh)
  and 9/9/9 (CheckMK), with no errors and no missing vector.
- **R7 script:** tested against the real OpenSearch on a throwaway index (`cs-r7-probe`, deleted
  afterwards).
  - Of 5 documents exactly the two relevant web01 messages from Graylog and Wazuh got R7, relevance 3
    and the reason naming the other source.
  - Untouched: the finding, the other host and the message judged not relevant.
  - A second run changed 0 documents.
- **Graph on the real history:** 96 relevant messages in 24 h gave 15 nodes and 28 edges in 132 ms
  (4 ms from cache). Strongest edge: `quelle:wazuh — mitre:Credential Access`, 41 messages.
- **Finding link:** the filter `befund:4408ae1d26` returns 3 documents (the finding and 2 CheckMK
  evidence messages). Edge `befund:4408ae1d26 — host:mssql05.example.com`: 3.
- **Browser check** (Playwright, API mocked with real `suche`/`netzplan` output, no login token):
  - Click on `cue0122.example.com`: chain "host:cue0122.example.com ✕", graph 6 nodes, list
    12 messages; the page stays on `/dashboard`; no console errors besides the mocked WebSocket.
  - Found and fixed: with layout gravity 0.08 the unconnected CheckMK part drifted under the list; now
    0.25. The focus ring was invisible in the light theme.
- **Backend suite:** 916 passed.

### What's missing

- **The AI funnel is still off.** 9 messages on 5 hosts wait for a finding (srv032 4, cue0110 2,
  mssql05 1, scv02-v 1, srv030 1, at 21:30 UTC).
- **Node labels show only the value.** A rule number like "11252" is a rule only by its colour and
  tooltip.
- **The `lw_*` tools are not on the MCP server yet.**
- **Still missing:** pattern memory, console knowledge, corpus search page, rule editor, reports.

---

## 2026-11-06 — Log watch, part 12: the history is written, and a problem appears as the object of an AI analysis

### What's new

- **The history is active.** Since 20:44 UTC every relevant hit writes the messages the flood brake
  allows, in full with all original fields and a bge-m3 vector. A run that loses its embedding writes
  anyway, and `nachbetten()` adds the vector in a later run.
- **AI funnel** (`trichter.py`). A script check decides when the AI works: relevance 3 or a new pattern,
  without a finding yet. The model then works through read-only tools to one finding per host.
- **AI findings are documents** `quelle:ki` in the same history. The code checks every quote against
  the stored message before writing, and links the evidence (`befund_ids`).
- **Cockpit tile `lw_befunde`.** It shows the finding as an object: relevance, title, host and time,
  and unfolded the text, hypotheses with status, checked quotes, the path of the analysis and the
  hashtags. With findings present it stands first.
- **One search over the history** (`suche.py`) and **one tool registry** (`werkzeuge.py`) for the funnel
  and, later, the MCP server.

### What changed

- Wazuh evidence: the aggregation now returns the alert IDs per rule × agent, and the history fetches
  exactly those. Before, the fetcher guessed the agent from the canonical host name.

### Fixed during the live check

- **Topic `kernel-io` matched application exceptions.**
  - Of 384 Graylog lines with "I/O error" in 24 h (Graylog MCP search, 2026-11-06 ~21:00 UTC), exactly
    one was a device error (`Buffer I/O error on dev dm-8`, veeam-proxy01).
  - The rest were Spring HTTP errors and Jersey response aborts. The very first history document was
    such a Jersey line, judged R5 "damage".
  - The pattern now needs the device. That document was deleted (the only one, `graylog:e1b3db81-…`).
    The case is a regression test.
- **Wazuh wrote fewer messages than it counted:** window 20:40, 53 requested, 18 found. Cause: the
  fetcher asked `size=n` per rule and filtered the agent afterwards. It now fetches by ID.
- **A second quote of the same document disappeared silently.** Each (document, quote) pair is now
  checked, and a failed one is listed as rejected.
- **The model's tool-call syntax ended up in a finding** (`…</parameter>\n<parameter=relevanz>\n3`).
  Such text is now rejected and goes back to the model. The affected finding was deleted with its links
  and produced again through the checked path (`ki:4408ae1d26`).
- **Clicking a log-watch tile opened the unfiltered feed,** so unfolding a finding left the cockpit.
  `lw_*` tiles no longer navigate.
- **The `schreibe()` docstring promised a catch-up for missing vectors that did not exist.** It exists
  now (`nachbetten`).

### Evidence (live, 2026-11-06)

- **Raster runs after the switch** (table `raster_lauf`, column `historie`):

  | Run | Requested | Written | Vector missing |
  |---|---|---|---|
  | Graylog window 20:40 | 1 | 1 | 0 |
  | CheckMK 20:50 | 3 | 3 | 0 |
  | Wazuh 20:40 | 53 | 18 | 0 |

  The Wazuh gap was caused by the old fetcher, fixed above. At 20:54 the history held 20 documents:
  17 Wazuh at relevance 2, 3 CheckMK at relevance 3.
- **First funnel runs, started by hand** (switch `log_watch.ki_aktiv` stays off, sample first):
  - **mon03.example.com → `ki:340fd11830`.**
    - Steps: wissen 0 → suche 1 → befund. Checked quote: "Active check helper usage: 97.46%
      (warn/crit at 90.00%/95.00%)(!!)".
    - The text still contains interpretation without a quote ("Livestatus-Prozess", "akuter
      Handlungsbedarf"). It was written before the prompt rule "the finding only claims what a quote
      carries", and is kept as the before example.
  - **mssql05.example.com → `ki:4408ae1d26`.**
    - Steps: wissen 0 (5.6 s) → suche 2 (4.4 s) → befund (33.5 s), 45.6 s in total.
    - Two checked quotes: page life expectancy "2 minutes 26 seconds (warn/crit below 5 minutes 50
      seconds/5 minutes 0 seconds)", state since 20:45:16 UTC (`last_state_change` 1793997916, checked).
  - **The first attempt for mssql05 ended in `ReadTimeout`** after 300 s on one model step. The next
    run needed 6–36 s per step. That the endpoint was busy is a hypothesis; step durations are now
    stored (`ki_s`).
- **Browser check** (Playwright against `https://hal.example.com/dashboard`; API mocked with the real
  `cockpit.daten` output, no login token):
  - two findings, mssql05 first, relevance 3;
  - unfolded: the text, 2 open hypotheses, 2 checked quotes, path "wissen · 0 → suche · 1 → Befund";
  - no horizontal overflow; the page stays on `/dashboard` after the click.
- **The real cockpit** (dashboard `62e00e26…`, recomposed by `design_dashboard`) has `lw_befunde` at
  the top.
- **Backend suite:** 912 passed.

### What's missing

- **The AI funnel runs only by hand.** `log_watch.ki_aktiv` waits for your review of the two findings.
- **R7 (two sources at once) is not written into stored documents.** The cross-source pass runs after
  writing.
- **No MCP wiring yet.** The registry exists; the MCP server does not offer `lw_*` tools.
- **Titles are not machine-checked.** The rule is in the prompt only. Example: "(20:45, 2h)" in a
  mssql05 title before the rule.
- **Still missing:** network graph `lw_netzplan`, pattern memory, console knowledge as
  `quelle:konsole`, corpus search UI, rule editor, reports.

---

## 2026-11-06 — Log watch, part 11: the AI cockpit shows the log watch

### What's new

- **Four cockpit widgets drawn in the frontend** (`frontend/.../dashboard/lw-widget.component.ts`,
  theme-aware LCARS/Holo/Classic, CSS instead of a chart library):

  | Widget | Shows |
  |---|---|
  | **Why** | relevant and not relevant per criterion, each group with its own scale and total |
  | **Volume per source** | read / relevant / trend of relevant / windows seen against expected |
  | **Hashtag trends** | with the comparison state |
  | **Corpus health** | run state per source, stage 0 "dropped at the source", unmatched hosts, definition version, history switch |

- **AI findings and the network graph** show the named state "history not active yet (dry run)", not an
  empty tile.

### What changed

- **Names:** the dashboard header reads "KI-Cockpit" / "AI Cockpit"; the host page reads "Host-Cockpit"
  (was "Server Cockpit"), including the tooltip in the news feed and the README.
- **Hashtag trend, the comparison is named:**
  - `faktor` when both periods were measured;
  - `neu` when the earlier period was measured and the tag did not occur in it;
  - `kein_vergleich` when the earlier period has no measurement of that source.

### Fixed during the visual and logic check

- **Every hashtag showed "neu".** The comparison period held no windows of the current raster version.
  Claiming "new" without a comparison is false; it now reads "—" with the reason.
- **Routine pushed every reason for relevance to zero.** "Why" had one scale for everything:
  N1 routine at 1.27 million made the relevant bars (hundreds) invisible. Each group now has its own
  scale.
  - Live: relevant 4 689 (R6 security largest, then R3 spread and R2 new pattern), not relevant
    1 440 105.

### Evidence

- **Playwright on hal.example.com with real cockpit data from the backend as API mock** (no real
  login): all four widgets render. "Findings" shows the reason. Corpus health shows all five sources as
  "läuft", stage 0 "1.086 Tsd./h verworfen", "Hosts unbekannt: 44", "Definition v5" and "Historie
  Trockenlauf".
- **Tests:** the backend suite has 883 passed. `ng build` succeeds.

### What's missing

- **Click from a widget into the search.** It comes with the corpus search.
- **No AI findings yet** (they need the history and the AI funnel).

---

## 2026-11-06 — Log watch, part 10: stage 0 (Graylog drop rules) visible and counted; topics per line for Graylog

### What's new

- **Stage 0 of the firewall:** Graylog's own pipeline rules with `drop_message()` are read (read-only),
  each with source text, pipeline, stage, streams and the fields it checks.
  - What they drop is counted every 15 minutes, summed over all Graylog nodes. The load balancer spreads
    requests at random, so the code asks until every node has answered.
  - The cockpit shows "verworfen an der Quelle".
- **Contradiction check stage 0 ↔ our topics:** every topic now has a typical example line. The check
  reads the keep words straight from the Graylog rule text and reports every topic whose line stage 0
  drops from container logs.
- **MCP skill `graylog-api`** (skills repo `ac050c9`):
  - new `graylog_drop_rules` (stage 0 with counts over all nodes);
  - `graylog_get_pipeline_rules` with `title` (source text) and `nur_verwerfen`;
  - **`graylog_system_status` repaired**: it read the node list as a dict, but Graylog 5.2 returns a
    list.

### Fixed

- **Graylog topics per line instead of per pattern.** Drain merges lines with and without a topic (e.g.
  "upstream answered" / "upstream timed out") into one pattern. Before, the topic came from the pattern
  and its first example only. The error-cluster count now groups by (host, topics of the line). The
  error-cluster detection itself is unchanged.
  - Measured: 12 µs per line, about 0.7 s per window.

### Evidence (live, 2026-11-06 19:45 UTC)

- **Stage 0, complete over 3 of 3 nodes:** 1 085 898 messages/h dropped at the source.

  | Rule | Per hour |
  |---|---|
  | `drop_http_success` | 919 470 |
  | `drop_container_noise` | 163 348 |
  | `drop_cue_zipline_xml` | 3 080 |

- **Contradiction found:** `drop_container_noise` removes, from container logs, **15 of our 20 topics**
  whenever the line has no error word: oom, disk-full, fs-readonly, absturz, cert, tls, dns, timeout,
  conn-refused, conn-reset, auth-fail, zugriff, replikation, drosselung, warteschlange.
  - Example: "dial tcp …: connect: connection refused" in a container log never reaches us.
  - **Decision is open (production change in Graylog).**
- **Graylog with topics per line:** window 19:40 had 54 680 lines in 618 groups; the run took 19 s,
  12 s before.
- **Tests:** 6 new, among them "every topic recognises its own example" (the vocabulary is consistent),
  the contradiction check against the live rule text, and topics per line within one Drain pattern. The
  backend suite has 881 passed.

### What's missing

- **Decision on the stage 0 contradiction.** Options: extend Graylog's keep list with our topic words,
  or accept that container logs reach us only with an error word.
- **Stage 0 is not in the rule editor yet** (no UI).

---

## 2026-11-06 — Log watch, part 9: one filter language; objects are saved searches, rules are searches with an action

### What's new

- **One filter language** for the search bar, objects, rules, the network graph and MCP. It covers
  `art:wert`, `art:(a OR b)`, `art:wert*`, `!`, `OR`, brackets, `level>=7`, `@Objekt` and free text
  (search only).
- **One parse tree, two translations:** the rule condition (in memory, per verdict) and the OpenSearch
  query (search, graph, hit count). Both hit the same messages.
- **Objects are named filters, as on a Palo Alto firewall:** `Produktion`, `Nicht-Produktion`,
  `Rauschen`, `Selbst-erzeugt`, `Routine-Quellen`.
  - The standard rules are filter texts over these objects, e.g. R1 = `zustand:crit @Produktion`.
  - Objects may reference objects; cycles and unknown objects are rejected with a reason.
- **Definition version 5:** the parameter lists that used to feed the rules are now objects, so there is
  one source of truth. The API returns and accepts objects and checks every filter.
- **Graylog's own pipeline filters are planned as stage 0** of the firewall. Example: `[proc]routing`
  drops Docker logs that are not errors at the source. They will be shown read-only, what they drop will
  be counted, and contradictions with our rules will be reported.

### Fixed during the logic check

- **Search and rule saw different fields.** The rule `level>=7` checks a field of the message, but the
  index mapping of `cs-logs-*` had no `level`. The search would never have matched where the rule
  fires. `level` is now mapped and written into the document.

### Evidence (live, 2026-11-06)

- **Random test:** 400 filters × 300 messages, rule condition = OpenSearch query, 0 differences.
- **Live probe against the real OpenSearch:** 150 filters × 200 messages, 0 differences. The probe index
  was deleted afterwards.
- **Equivalence of the object-based standard rules with the agreed precedence:** all 8 192
  combinations hold.
- **Under version 5 (19:15–19:25 UTC) the picture is unchanged:**

  | Source | Result per window |
  |---|---|
  | Wazuh | N4 3 089, R6 148, N0 3 811 |
  | CheckMK | R1 5 |
  | Switches | N1 1 334 |
  | Firewall | **17 relevant: sinkholes for `alpinbilder.de` from `emil.example.com` (9) and 10.0.249.6 (8), both via 8.8.8.8** |

  The firewall finding is relevant and urgent. "emil is infected" does **not** follow from it: two ADMIN_DMZ
  servers querying Google DNS in bulk are probably DNS forwarders, and the infected client sits behind
  them. Checking this is the job of the AI funnel.
- **Tests:** 12 new. The backend suite has 875 passed.

### What's missing

- **No rule editor UI yet.** The Graylog stage 0 is not read yet.
- **The firewall cannot see which client sits behind a DNS forwarder.** The affected host is then the
  forwarder, which is named as a hypothesis, not as evidence.

---

## 2026-11-06 — Log watch, part 8: switch logs (nsa/nss/nsc) are analysed

### What's new

- **Switches get their own pattern learner** (Drain, state in `netz_zustand`, known patterns in
  `netz_muster`).
  - It reads all lines with level ≤ 4: about 1 300–1 400 per 5 minutes, complete.
  - Switches are judged by the same rules and hashtags as every other source.
  - The error-cluster detection stays unchanged; it excludes switches on purpose.
- **"New" (R2) only after a 24-hour learning phase.** Until then the verdict says "Lernphase bis …"; the
  learning phase is never hidden.
- **Switch vocabulary:** link down, err-disable, spanning tree, loop, CRC, PoE, hardware. Power supply,
  fan or temperature failures count as damage (R5); syslog level ≤ 2 is a critical state.
- **Definition version 4:** a known switch pattern without a signal is routine (N1), like Graylog.

### Fixed during the live check (all three found on the real Juniper EX messages)

- **The vocabulary did not know the Juniper wording.** Juniper writes "ex_bcm_linkscan_handler: Link 12
  DOWN" and "SNMP_TRAP_LINK_DOWN … ifOperStatus down". Both are now recognised, as is Cisco "changed
  state to down".
- **The device name in the message gave every switch its own pattern.** For example "nsa429 fpc1 … Link
  DOWN". An outage across many switches would have looked like many single cases.
  - The leading device name is now removed before learning.
  - The learner tables, filled 20 minutes earlier with these wrong patterns, were reset. The learning
    phase starts again.
- **UP and DOWN fell into one pattern.** Drain generalises "Link 7 UP" and "Link 7 DOWN" to
  "Link <NUM> <*>". Topics are now determined per line and are part of the group key, so DOWN lines are
  counted and judged apart from UP lines.

### Evidence (live, 2026-11-06)

| Window | Lines | Patterns |
|---|---|---|
| 18:55 (before the fix) | 1 365, complete | 34, host-specific |
| 19:00 (after the fix) | 1 325, complete | 26, shared by all switches |

- In both windows everything was routine (N1), as expected in the learning phase.
- **Tests:** 17 new, among them the Juniper and Cisco wordings, one pattern for three switches, UP/DOWN
  in one pattern, and the learning phase. The backend suite has 863 passed.

### What's missing

- **The same UP/DOWN effect can occur in the Graylog part.** Topics there also come from pattern and
  first example. Planned: topics per line in the error-cluster count.
- **Spikes per switch pattern need a baseline** (hourly counts), which is not built yet.
- **The old network agent still runs in parallel**; it will be replaced after a shadow run.

---

## 2026-11-06 — Log watch, part 7: firewall logs (Palo Alto) are analysed

### What's new

- **Firewall threats are read one by one and judged like every other source.** These are spyware, virus,
  vulnerability, wildfire, file, scan, flood and data, without "informational". They are grouped by
  threat × affected host × action.
  - Relevant (R6) for a sinkhole or severity medium and above; urgent for a sinkhole or high/critical.
  - A low-severity threat the firewall already blocked is not relevant, with that reason.
- **Firewall traffic is only counted** per 5-minute window (per action, zone pair, destination country).
  It is about 2.7 million messages per hour, so reading it line by line would make no sense.
- **Relevance definition versions 2 and 3** (taken over automatically, each with a reason): R6 covers
  firewall threats, and the external zones are a parameter.
  - A version from the code is only taken over if the last version came from the system.
  - An admin's version is never overwritten; the difference is named (`code_neuer`).

### Fixed during the logic check (all three found live)

- **The threat query also hit all traffic.** `gim_event_type:palo_threat` is set on every Palo Alto
  message. The first window hit 188 000 messages per 5 minutes and was cut off at 10 000, so rare
  sinkholes could have been lost.
  - Threats are now selected by threat type: about 600 per window, complete.
- **An attack from outside was attributed to the attacker.** "React Server Components RCE", critical,
  UNTRUST → WEB_DMZ, was filed under the external source IP.
  - The affected host is now the side in an internal zone; the other side gets the tag `gegenstelle:`.
- **Sinkholes would have been judged twice.** They have level 4, so they also run through the
  error-cluster pass (19 per hour).
  - Palo Alto messages now belong to the firewall source. The error-cluster detection still counts them
    unchanged; the raster no longer rates them a second time as Graylog.

### Evidence (live, 2026-11-06 18:35–18:50 UTC)

| Window | Result |
|---|---|
| 18:35 | 642 threats, complete; 1 relevant: the critical RCE attempt into WEB_DMZ (rule R6-FW, urgent) |
| 18:40 | 723 threats, complete; none relevant (no sinkhole, nothing medium or above) |

- Definition: versions 1, 2 and 3 are in the database, each with a reason.
- **Tests:** 8 new, among them the live cases sinkhole, low-severity drop and RCE from outside, and the
  rule that threats never come from traffic. The backend suite has 846 passed.

### What's missing

- **No baseline for firewall traffic yet.** Spikes in deny/drop per zone pair or country can be detected
  once there are 7 days of counts.
- **Switch logs** are next.

---

## 2026-11-06 — Log watch, part 6: the relevance decision is a firewall ruleset

### What's new

- **Ordered rules, first match decides FORWARD (relevant) or BLOCK; default policy BLOCK (N0).**
  - Every source sets what it observes as a hashtag, e.g. `signal:neu`, `signal:schaden` or
    `signal:behandelt`. A rule checks only hashtags and a few fields, so it filters exactly what a person
    filters in the search.
- **Firewall counters per window:**
  - decisions per rule;
  - displaced matches (a rule matched, but an earlier one decided);
  - test-mode hits.

  Rules in test mode never decide. This is where AI proposals go before an admin activates them.
- **Rules are part of the versioned definition** and can be read and replaced through the API. A new
  rule set is checked for IDs, actions, modes, criteria and well-formed conditions.

### What changed

- **The precedence function is gone from the product code.** The rule order is the precedence.
  - The original precedence lives on as the test oracle only, so there is one way to a verdict.
- **A verdict cites the deciding rule first,** then further matching criteria with the same action.
  For example, CheckMK CRIT on a full filesystem on prod is now "R5, R1"; before it was sorted "R1, R5".

### Evidence

- **Equivalence:** all 8 192 combinations of the criteria give the same verdict under the rules as
  under the original precedence, and the deciding rule is always one of the criteria the precedence
  named.
- **Live, 2026-11-06 18:20–18:30 UTC, same picture as before the switch:**

  | Source (per 5 min) | Decisions per rule |
  |---|---|
  | Wazuh | N4 2 888, R6 148, default 3 473; R6 displaced 11 times (noise at level ≥ 7 decided by N4, as intended) |
  | CheckMK | R1 2, default 9 |
  | Graylog | N1 54 370 |

- **Performance:** 5.2 µs per verdict, about 5.5 ms per window. Compiling the rules to C++ (like
  Varnish) was considered and deferred, with this measurement as the trigger to reconsider.
- **Tests:** the backend suite has 838 passed. Among them are the equivalence test, the conditions,
  test mode, displacement and the rule-set check.
- **Fixed during the switch:** the arguments of a verdict were in the wrong order, so the evidence IDs
  landed in the `regel` field. The tests caught it before deployment.

### What's missing

- **Firewall logs (Palo Alto, about 2.9 million/h) and switch logs (about 241 000/h)** are not read yet.
  They are planned: firewall by field counts plus single threats, switches in their own pattern pass.
- **No UI for rules**, the hit list, or AI proposals yet.

---

## 2026-11-06 — Log watch, part 5: the dashboard becomes the AI cockpit (backend)

### What's new

- **Six new widget types** that the AI places on the generative dashboard, which is now called **"🪄
  KI-Cockpit"**:

  | Widget | Shows |
  |---|---|
  | `lw_kriterien` | why: verdicts per criterion and source |
  | `lw_volumen` | messages per source and hour: read, relevant, would be stored |
  | `lw_hashtags` | most frequent hashtags of relevant messages, with trend |
  | `lw_korpus` | corpus health: fields, unmatched hosts, last run per source |
  | `lw_befunde` | AI findings as objects |
  | `lw_netzplan` | the network graph |

  - `lw_befunde` and `lw_netzplan` say "history not active yet (dry run)" until the history is filled;
    they never show an empty tile.
- **The cockpit always shows why.** If the AI leaves out `lw_kriterien`, code adds it. If a source is
  not running, `lw_korpus` comes first.
- **Numbers carry unit, period and origin.** Host names appear only in the admin API; the widgets show
  counts.

### What changed

- **The generative dashboard is renamed** from "🪄 KI-Lagebild" to "🪄 KI-Cockpit". The name is the key
  the dashboard is found by, so the existing rows are renamed by migration 0057. Changing only the
  constant would have created a second, empty dashboard.

### Fixed during the live check

- **Rows from before the definition mixed with new ones.** In the hour window, Wazuh showed 731 hashtag
  hits for 268 relevant messages, and "would be stored" (426) was larger than "relevant".
  - The aggregation now counts only rows of the current raster version.
  - Older rows are listed as `alte_fassung`, not silently dropped.
  - After the fix: 268 relevant, top hashtag 209, stored 94.

### Evidence (live, 2026-11-06 ~18:15 UTC)

- All six widget types return data, or the named "history not active" state.
- `lw_kriterien` over one hour: R2 103 (Graylog), R6 268 (Wazuh), N0 7 276, N1 Graylog routine.
- Migration 0057: 3 dashboards renamed, no duplicate.
- **Tests:** 8 new (cockpit aggregation, designer whitelist and clamping, mandatory "why" widget,
  version mixing). The backend suite has 832 passed.

### What's missing

- **The frontend does not render the `lw_*` widgets yet.**
- **The host page is still labelled "Server-Cockpit"**; the rename to "Host-Cockpit" is in the frontend
  step.
- **Findings, the network graph and the MCP tools** need the history and the tool registry.

---

## 2026-11-06 — Log watch, part 4: what "relevant" means is defined, and every verdict cites it

### What's new

- **A versioned relevance definition**, agreed with the user: eight relevant criteria (R1–R8) and
  seven not-relevant ones (N0–N6), with a fixed precedence.
- **Every verdict cites its criteria**, whether it comes from the raster, the AI or a human, together
  with the version of the definition.
- An admin changes the definition by adding a new version with a reason; nothing is overwritten.
- **The raster now judges by the definition instead of fixed thresholds.** The dry run counts verdicts
  per criterion, so it shows which rule drives the picture.
- **CheckMK reports downtime** (`in_downtime`), which counts as handled (N2) like an acknowledgement.

### Fixed during the logic check

- **Noise could never apply.** In the first draft, security (R6) beat noise (N4). N4 only covers Wazuh
  events from level 7 up, so every web-scanner hit would have stayed relevant. N4 now beats R6.
- **Acknowledged damage stayed relevant.** The first draft let R5/R6 beat "handled" (N2). That
  contradicted the decision that acknowledged means handled. N2 now beats every R.
- **CheckMK lost its baseline on every restart.** A change during a deploy was never seen, and every run
  after a restart was a "first comparison". The last state is now kept in `raster_lauf` and reloaded if
  it is younger than 2 hours.

### Evidence (live, 2026-11-06 ~18:05 UTC)

- Definition version 1 was created on first use (author `system`).
- **Wazuh, one 5-minute window:** 6 699 alerts, of which 141 relevant (all R6), 3 014 noise (N4, PAM
  sessions) and 3 544 with no criterion (N0).
  - Under the old fixed threshold, 162 were relevant. The difference is web-scanner hits now
    correctly counted as noise.
- **Tests:** 13 new (824 instead of 811), and the raster tests rewritten around the criteria. They
  include a precedence table where every combination of criteria gives exactly one verdict, the
  definition check, and the CheckMK state across a restart. The backend suite has 824 passed.

### What's missing

- **N2 does not yet cover** "open ticket" or "already reported as an AI finding".
- **No UI for the definition yet.**

---

## 2026-11-06 — Log watch, part 3: history index with vector search (prepared)

### What's new

- **Index template `cs-logs`** for the history of relevant messages:
  - original fields as `flat_object`;
  - hashtags, reasons and the text;
  - the selectors `urteil` (`relevant` | `nicht_relevant`) and `herkunft` (`regel` | `ki` |
    `mensch`);
  - a bge-m3 vector for search by meaning.
- **Embedding client** for the llama.cpp endpoint (bge-m3, 1 024 dimensions).
- **The error-cluster pass now remembers the Graylog IDs** of the first five messages per pattern ×
  host. The history fetches exactly those messages in full instead of copying everything.

### Evidence (live, 2026-11-06)

- Graylog returns `_id` even with a reduced field list. Fetching one message by ID gave 37 fields.
- **Probe index** with three real messages and real vectors, deleted afterwards:
  - the template applies: `knn_vector`/1 024 and `flat_object`;
  - search by meaning works across languages: the German "Speicher voll, Prozess vom Kernel beendet"
    finds the English OOM message first (0.902 against 0.861 and 0.839);
  - a field inside `roh` is searchable.
- **Tests:** 6 new, for the document (fits the strict mapping, stable ID, no relevance below 2), the
  checked embedding response, the exhaustive selector, and IDs per host up to the quota. The backend
  suite has 811 passed.

### What's missing

- **Writing is switched off** until the dry run has measured the volume per day.
- **What "relevant" means in our network is not defined yet.** The rules today are a proposal; the
  definition comes first, and every verdict will cite it.

---

## 2026-11-06 — Log watch, part 2: the raster tags the stream (dry run)

### What's new

- **The raster rates and tags every message group of Graylog, Wazuh and CheckMK by script, without
  AI.** Each group gets a relevance (0 routine, 1 normal, 2 conspicuous, 3 urgent), the reasons for it,
  and hashtags in a single form, `art:wert`.
  - Examples: `host:`, `ort:`, `thema:oom`, `muster:…`, `regel:…`, `mitre:…`, `dienst:…`.
  - A hashtag is at the same time the filter, the facet and later the network-graph node.
- **Graylog adds no load.** It is rated inside the existing 5-minute pass of the error-cluster
  detection; Wazuh and CheckMK run in their own 5-minute job.
- **One name per host across all three sources.** The canonical name is the CheckMK host. The raster
  matches spelling variants, IP addresses and glued-on domains to it.
  - A name it cannot match is named, not guessed: `hostliste:unbekannt`, `hostliste:mehrdeutig` or
    `hostliste:container-id`.
- **Across sources:** a host relevant in two sources within 15 minutes rises to urgent. The reason
  states "common occurrence, no cause established".
- **Dry run first:** nothing is stored yet; every window is only counted. `GET
  /api/log-watch/raster/trockenlauf` projects, per source, how many messages and MB per day the
  history would take.

### Evidence (live, first windows 2026-11-06 ~17:30–17:45 UTC)

| Source | One 5-minute window | Would be stored |
|---|---|---|
| Graylog | 56 962 error lines in 757 groups; 755 normal, 2 conspicuous (theme `absturz` on cue0148) | 2 |
| Wazuh | 8 073 alerts; 7 155 routine, 738 normal, 180 conspicuous | 69 (flood brake) |
| CheckMK | 322 problems on the first comparison, counted as the existing state, not as new | — |

- **CheckMK criticalities actually present:** `prod` 208, `staging` 55, `test` 46, `dev` 12,
  `critical` 1. CRIT on `prod` or `critical` is urgent.
- **Host matching:** 2 846 CheckMK hosts. In the first Graylog window, 40 names could not be matched.
  After matching by address and glued-on domain, 22 remain unknown and 7 are container IDs.
- **Tests:** 38 new tests. They cover the vocabulary including false hits ("rescue", "timeouts",
  "502ms"), the live spellings of host names, every rule per source, the flood brake, and the
  projection. The backend suite has 805 passed.

### What's missing

- **24 hours of dry run** are needed before the history (`cs-logs-*`) is filled. The volume decides
  whether the flood brake and thresholds hold.
- **22 Graylog sources CheckMK does not know** (service names such as `cueapi`, SD-WAN devices). They
  are visible as `hostliste:unbekannt`; no alias table for them yet.
- **Pattern memory** (AI verdicts per pattern) is still missing, as are the vector index, the AI
  funnel, findings as messages, and the network graph.

---

## 2026-11-06 — Log watch, part 1: field catalog for Graylog, Wazuh and CheckMK

### What's new

- **A catalog of every field in all three sources**, with type, coverage, cardinality, example values
  and a role. The role decides how a field is used later: entity (becomes a `#host:` hashtag and a
  graph node), category, identifier, measurement, free text or ignored.
  - This is the base for the log watch: scripts tag the stream, only relevant messages are kept as
    history, and the AI filters that history.
- **Measured in the source, nothing copied.** Graylog counts by pivot, Wazuh on its indexer, and
  CheckMK over the current problems.
- **Admin API** to read the catalog, measure again, and set or reset a field's role. A daily job runs
  at 03:45.

### Evidence (live, 2026-11-06)

| Source | Fields | Active in the window | Window |
|---|---|---|---|
| Graylog | 1 191 | 488 | 15 min ≈ 1.9 million messages |
| Wazuh | 673 | 161 | 24 h ≈ 2.0 million alerts |
| CheckMK | 64 | 64 | 310 current problems |

- 0 fields unmeasured. All 597 hashtag candidates have example values; 312 of them come from a sample
  because Graylog refuses their value list.
- The run takes 145 s.
- Tests: 52 new tests for roles, all three adapters and the update logic. The backend suite has 767
  passed.

### What was fixed during the live run

- **Text fields broke whole blocks.** Three pure text fields (`message`, `full_message`, `PeerNameCN`)
  made Graylog reject blocks of 50 fields each, leaving 150 fields unmeasured.
  - Text fields are now counted via `_exists_`.
  - A rejected block is split in half until the culprit stands alone.
- **Too many values became candidate hashtags.** Ports (62 000 values), bytes, durations and numbered
  timestamps were classed as identifiers.
  - They are now measurements or ignored.
  - Anything with more than 5 000 values is not a hashtag.
- **`(Empty Value)` showed up as a value.** That is Graylog's marker for "missing" and is now dropped.

### What's missing

- **Host names are spelled differently per source** (`WEC01`/`wec01`, `aim012`/`aim12`, short name vs
  FQDN). The common `#host:` hashtag needs normalisation against the CheckMK host list; that is part of
  the raster, the next step.
- **The Graylog field `servicename` is partly mis-parsed** (values such as "Starting", "2026]"). The
  catalog shows it; fixing it belongs in the Graylog extractor, not here.
- **No UI for the catalog yet**, only the API.
- **Still to come:** the raster (tagging, relevance, and history in `cs-logs-*`), the pattern memory,
  the AI funnel, and the network graph.

---

## 2026-11-06 — Incident view: "Prüfschritte" button removed, header no longer cramped

### What was removed

- The **"Prüfschritte (experimentell)"** box, with its "Prüfschritte ausführen" button, is
  gone from the incident view. The investigation runs through the **"Computer, untersuche
  das"** button alone, so there are no longer two ways to start the same thing.
  - The backend endpoint `run-checks` stays, but nothing calls it.
  - Results of earlier runs are still stored in the incident and passed to the Console
    hand-off.

### What was fixed

- The incident panel is 560 px wide instead of 480. Header buttons no longer shrink. A host
  name too long for the space is cut off with "…", and the full name appears on hover.
- Before, in the 480 px panel, the robot icon in "Computer, untersuche das" was squeezed into
  the text, and "Ticket erstellen" broke onto two lines.
- The tooltip of the hand-off button no longer promises "Hermes … kann per SSH eingreifen".
  The console agent is not always Hermes, and it examines without changing anything.
- **Evidence** (Playwright on hal.example.com, mocked incident that has `planned_checks` and
  `check_runs`):
  - The panel contains no "Prüfschritte" text and no run button.
  - The header shows "Computer, untersuche das", "Ticket erstellen" and close, each on one
    line. The header is 76 px high.
  - On a 400 px screen the header is also 76 px high.

### What's missing

- Next to the long button, only about 115 px are left for the host name at 560 px:
  `docker0218.example.com` is cut off, and the full name appears on hover.

---

## 2026-11-06 — Open tickets in Jira / Service Desk with one click

### What's new

- **My Tickets:** every list row and board card has a ↗ icon that opens the ticket in its own
  instance (Jira or Service Desk) in a new tab. Clicking the row still opens the work dialog,
  as before.
- **Console:** a session that belongs to a ticket shows a **↗ KEY** pill in the action rail.
  The ticket key in the "Neue Aktivität" banner is clickable.

### What changed

- **Contract:** `/api/jira-view/my-tickets` and `/api/jira-view/issue/{key}` deliver
  `_centralstation.url`. `/api/computer/sessions` (and session creation, ticket activity)
  delivers `ticket_ref.url`. When the instance is unknown, the value is `null`, never a
  broken link.
- **Link rule in one place:** `app/core/jira_links.browse_url()`. Before, six places built
  the link by hand, some with and some without stripping the trailing `/`.
- **Evidence:**
  - Unit tests for `browse_url`: trailing slash, missing parts → `null`, key encoded. The
    backend suite has 715 passed.
  - Live in the backend against real data: 49 sessions, 7 with a ticket. The links point to
    `jira.example.org` and `servicedesk.example.com` respectively. My Tickets delivers
    `https://jira.example.org/browse/JCUE-6364`, among others.
  - Playwright on hal.example.com with mocked data:
    - the click opens a new tab at `servicedesk.example.com/…/browse/IMIT-136367`;
    - the work dialog does not open as well;
    - board links are present;
    - the Console pill "↗ IMIT-136367" has the same width as the other pills.

### What's missing

- **Ticket keys inside chat text** (e.g. "see IMIT-1234" in an agent answer) are not linked.
  The same key can exist in more than one instance, and from the text alone it is not clear
  which one is meant.

---

## 2026-11-05 — Console: live search across all sessions

### What's new

- **A search field above the session list** filters the list while you type, across
  session names and complete histories.
  - **What counts as a hit:** a message containing all search words, or a name containing
    them.
  - **Each matching session shows:** the number of matching messages and an excerpt with the
    words marked.
  - **Clicking a hit** opens the session at the first matching message. Clicking again jumps
    to the next one. Matching messages are marked, and the words are highlighted.
  - **Status line:** shows "N / M SITZUNGEN", "KEINE TREFFER" or "mind. 2 Zeichen".
    Sessions whose history could not be loaded are counted as "nur Name" instead of
    silently missing.
  - **Evidence:**
    - Unit checks of the search logic (hit rule, markdown stripped from the excerpt,
      overlapping marks merged).
    - Playwright against the deployed frontend (hal.example.com) with mocked API data:
      3 sessions → "1 / 3 SITZUNGEN · 1 nur Name"; 3 matching messages marked; 6 words
      highlighted, including inside agent messages and code; repeated clicks jump to
      hits 0 → 1 → 2 → 0; Esc restores all 3 sessions.

### What's missing

- **Codex sessions** keep their history only in memory. After a container restart only
  their name is searchable (same as what the Console can show).
- **Search runs in the browser** over the histories the Console loads. There is no
  server-side index, so very large histories cost time on every opening, as they already do
  today.

---

## 2026-11-03 — Outbreak mails: only signals with proven relation, hosts named as hosts

### What changed

- **CheckMK alerts appear in the research only when they are statistically related.**
  - **Why:** in the rhsmcertd mail (JSON parsing errors on 13 RHEL hosts), "File group
    PrintPdfExport/ePaperExport" on **one** of the hosts was listed as a related signal.
    The AI concluded from it that configuration files were missing — an inference from
    mere coincidence. Before, every alert of the affected hosts was listed, from 15 min
    before the start and with no end to the window.
  - **New rule:** a CheckMK service counts as related only when it occurs **significantly
    more often** on the affected hosts than on all other hosts in the same time window.
    - Test: one-sided exact Fisher test, p < 0.01, on at least 2 affected hosts. For a
      single-host outbreak, p < 0.01 alone, i.e. an alert that hardly occurs anywhere else.
    - The idea follows Luo et al., KDD 2014 ("Correlating Events with Time Series for
      Incident Diagnosis"): compare against a random reference sample, not co-occurrence.
      Here the other hosts are the reference sample.
    - The time window is closed (start − 15 min until delivery).
  - **Shown:** each related alert with its reason ("auf 9 von 13 betroffenen Hosts, sonst
    auf 2 von 3080 Hosts (p = …)"). The rest is named as a count ("2 gleichzeitige
    Alert(s) auf einzelnen Hosts ohne belegten Zusammenhang ausgeblendet"). Nothing
    disappears silently.
  - **AI assessment:** it receives only the related alerts with their reason. The prompt
    forbids naming as related, or deriving a cause from, signals that merely occur at the
    same time.
- **Hosts are called hosts.** Filebeat/syslog lines come from servers, not containers. Mail,
  subject, incident title and AI input now say "Hosts", "Container" or "Container und
  Hosts". The rule reason says "auf N Systemen gleichzeitig" instead of
  "N Container/Quellen".
- Verified:
  - The real rhsmcertd outbreak of 03.11., re-rendered without sending: "17 Hosts";
    CheckMK related: none, "2 … ausgeblendet". Before: "13 Container" plus both File Group
    alerts as signals.
  - Tests: 6 new — Fisher tail; 1 of 13 is not related; 9 of 13 is; an alert present
    everywhere is not; single-host case; host/container detection. Full suite: 711 passed.

### What is missing

- Two hosts send misspelled names: `cue0156.example.mediia` and `cue0107REDACTED-CREDENTIAL.example.com`.
  They come from the Filebeat configuration of those hosts. CheckMK does not know them, so
  they cannot be matched in the research.

## 2026-11-03 — "Computer, untersuche das" opens its own session again

### What was fixed

- **The handoff from an incident wrote into the last open console session.**
  - **Cause 1:** the session name is the incident title. Titles longer than 100 characters
    broke the session creation ("value too long for type character varying(100)", HTTP
    500).
    - In the last 30 days that was **517 of 8,569** incidents (up to 172 characters).
    - Example: "Kritisches Ereignis: OOM-Kill … auf mu-img-docker19.example.com · 3× in 6 h" has
      101 characters.
  - **Cause 2:** when the creation failed, the console took the open tab without a word
    and sent the incident task there.
  - **Fix:**
    - Session names are trimmed to the column width (100, with "…") — on creation and on
      rename. Rename used to trim to 120, which was also too long.
    - If a session still cannot be created, the console says so ("Neue Konsolen-Sitzung
      konnte nicht angelegt werden — Auftrag nicht gesendet.") and does not send the task
      anywhere. The same applies to the ticket handoff.
  - Verified: creating a session through the real endpoint with exactly that 101-character
    title → 201, name 100 characters ending in "…" (test session deleted afterwards). Full
    suite: 705 passed.

## 2026-11-02 — The console browser (Playwright) reaches the internet

### What was fixed

- **Playwright (browser automation) for the console agents got no internet page.**
  - Playwright was already set up as an MCP server for all three agents: Hermes (25 tools
    loaded), Claude (`claude mcp add`) and Codex (`[mcp_servers.playwright]`).
  - But every page outside the network ended in `ERR_TIMED_OUT`. Chromium does not take
    `HTTP(S)_PROXY` from the environment; curl in the same container answered via the
    proxy in 0.02 s.
  - **Fix:** `playwright-mcp` now gets `--proxy-server` and `--proxy-bypass` from the
    backend's proxy settings.
    - The bypass list is the same as `NO_PROXY` in the container (`no_proxy_entries()`,
      one source).
    - Internal names (`.example.com`, backend, VibeMK) keep bypassing the proxy.
  - Verified:
    - Hermes session: playwright.dev opened in 8 s, with title and H1. Before: two
      timeouts.
    - netbox.example.com still opens directly (6 s).
    - Tests: 2 new; full suite: 702 passed.
  - **Gap:** containers that are already running pick this up for Hermes only after a
    restart. Claude and Codex get it with their next session.
  - Verified for all three agents (Hermes 8 s, Claude 10 s, Codex 15 s): playwright.dev
    opened, title and H1 read.

- **Codex in the console was broken entirely — every request was rejected.**
  - **Cause:** the model `gpt-5.5` was hard-coded in five places (Codex `config.toml`, the
    backend defaults, the fallback list, the frontend). Since then the Codex service no
    longer accepts it for ChatGPT accounts ("The 'gpt-5.5' model is not supported when
    using Codex with a ChatGPT account"); `gpt-5.4` fails the same way.
    - The console showed only the harmless stderr line "Reading additional input from
      stdin…". The real error was in the JSON stream.
  - **Fix:**
    - **One default:** `app/core/codex_defaults.CODEX_DEFAULT_MODEL = "gpt-6.1-sol"`. The
      models the account offers (`…/codex/models`): gpt-6.1-sol/-terra, gpt-6-astra/-sol/-luna
      and gpt-5.6-sol/-terra/-luna.
    - **The selected model wins:** the model chosen in the Codex connector now goes into
      `config.toml`; before, the config always said gpt-5.5.
    - **Validation:** model names are checked before they enter the TOML line.
    - **The user's connector** was changed from gpt-5.5 to gpt-6.1-sol, as requested. The
      console agent setting stays on Claude.
  - Verified:
    - Manually: `codex exec --model gpt-6.1-sol` → "ok", while gpt-5.5, gpt-5.4 and
      gpt-5.3/5.4-codex are rejected.
    - Codex session with Playwright works (see above).
    - Tests: 3 new; full suite: 705 passed.

## 2026-10-25 — The console can look up master data in the ID-Generator; console image builds reproducibly

### What is new

- **ID-Generator tools for all console agents (Hermes, Claude, Codex), read-only.**
  - **Why:** the console had no ID-Generator tool. The ID-Generator skill under `~/skills`
    only runs locally over stdio, and the console cannot reach it. When asked for the
    location of vpp0221, Hermes improvised for 202 s: SSH to idgen01, attempts to read
    database passwords from the containers (5 blocked by the write lock), and in the end
    an answer from Confluence.
  - **New:** `lookup_idgen_host` (location for host, IP address or switch),
    `list_idgen_records` (rows of a table with a filter) and `query_idgen_sql` (one
    SELECT). They use the existing ID-Generator connector; the agent instructions have a
    section "STAMMDATEN".
  - **Safety:**
    - Host names and IP addresses are validated against a pattern; table names allow only
      `[a-z0-9_]`.
    - SQL accepts exactly one SELECT, without `;` and without file or sleep functions.
    - The write lock recognises all three as read-only (names start with lookup/list/query).
  - Verified:
    - Against the real ID-Generator: vpp0221 and 10.0.188.48 → MUE-0 München (ID 27);
      locations, companies and a SELECT over virt_servers ⨝ locations return data.
    - SQL injection in the host name, `../admin`, `DELETE` and `SELECT 1; DROP …` are
      rejected.
    - Hermes session after the container restart: `lookup_idgen_host` called, the right
      answer in 11 s.
    - Full suite: 693 passed.
  - **Gap:** containers that were already running see the tools only after a restart
    (Hermes loads its tool list at startup).
  - **Not included:** the new ID-Generator API (`/ng/api`, idgen-ng 1.8.10) cannot be used
    headless. It logs in only via Keycloak in the browser; bearer tokens and username/
    password login are not enabled for the client. The tools use the old `/api/v2`, which
    still works.

### What was fixed

- **Console tool steps showed fragments of the tool name, e.g. "sql: SELECT …".**
  - A greedy expression (`^mcp_[a-z0-9_]+_`) was meant to cut off the prefix
    `mcp__<server>__`. It cut up to the *last* underscore instead.
    - `query_idgen_sql` became "sql", `lookup_idgen_host` became "host",
      `search_knowledge_base` became "base".
    - Every MCP tool with underscores in its name was affected, not only the new ones.
  - Now only the server prefix is removed: "query idgen sql: SELECT …".
  - Verified: 7 new tests; full suite: 700 passed.
  - Takes effect once the console container restarts, because it is part of the console
    image.

- **The console image could no longer be built.**
  - Hermes was cloned unpinned from the main branch. After a `docker system prune` (around
    16.10.) the build cache was gone, so the build fetched the latest Hermes. Its pinned
    versions conflict (Pillow==13.0.0 vs browser-harness==0.1.13).
  - Hermes is now pinned to the commit of the last working image (`userenv/HERMES_REF`,
    6206f893). Updating Hermes means changing that file.
  - Verified: the image builds; the console starts with "hermes_available: true".

## 2026-10-16 — Mails show similar messages instead of every noisy container

### What changed

- **The "Recherche" section in outbreak mails, the incident view and the console handoff
  now lists only similar messages.**
  - **Why:** "Gleichzeitig auffällige Container" listed every container whose error rate
    was ≥ 3× its 48-hour median in the same 15 minutes, whatever the error. Since the
    Filebeat hosts were added, that is almost always someone: cue0112 with 2,285 lines,
    stylo07, nfw062, dispocloud02 … The user asked: "nur ähnliche Meldungen, kein
    Bruteforce" (only similar messages, no brute force).
  - **New rule:** another pattern counts as similar when it shares at least 50 % of its
    content words with the reported one (Jaccard, at least 3 shared words). Placeholders
    (`<NUM>`, `<*>`, …) and level words (ERROR, WARN, …) do not count. The pattern must
    also be active in the same 15-minute window.
  - **Shown:** the pattern text, its containers, its rate, and the reason ("90 % gleiche
    Wörter").
  - **Empty findings are named:** "keine — die Meldung tritt nur in diesem Muster auf"
    (none — the message occurs only in this pattern).
  - The AI assessment gets the same data, under the field `aehnliche_meldungen`.
  - Incidents created before 16.10. still show their old list ("Gleichzeitig auffällig").
  - Verified:
    - Read-only on the live state, the six loudest patterns. dockerd "copy stream failed"
      → finds "stream copy error: reading from a closed fifo" (90 %) and the same message
      in the old line format on docker0209 and others. "Error running exec … OCI runtime"
      → only its variants (65–74 %). The Digicol file error, Go heap profiling and the
      linmap warning → none, where before every container with a raised rate would have
      been listed.
    - Tests: 3 new, 2 adapted. Full suite: 693 passed.
    - The first live run after the deploy completed without errors.

## 2026-10-14 — Nightly jobs are no longer incidents; repeated kernel failures (OOM) are reported; false-mail flood after the Filebeat rollout stopped

### What is new

- **Critical kernel events: OOM kill, kernel panic, hung task, CPU lockup, I/O,
  filesystem and hardware errors.**
  - **Why:** docker0209 had an OOM every hour for 25+ hours: Paperless/celeryd, 14.9 GB,
    global OOM, the last one before the reboot on 13.10. Nothing was reported. One OOM is
    1–3 kernel lines on one host, invisible to the volume-based rules (NEW ≥ 20 lines,
    SPIKE ≥ 200, PERSISTENT ≥ 100 in 2 h).
  - **Rule:** a fixed list of signatures, no LLM. Only kernel lines count (program
    "kernel"); container logs do not. A sudo log of a `grep 'invoked oom-killer'` is not an
    OOM.
    - **One event** = one host × one kind × one 5-minute window.
    - **First event:** a daily overview entry (its own section "Kritische
      Kernelereignisse").
    - **3 events within 6 h on the same host:** incident (kind `critical`, "Kritisches
      Ereignis (wiederholt)") and mail. Subject example: "Kritisches Ereignis: OOM-Kill
      (Arbeitsspeicher erschöpft) auf docker0209.example.com · 3× in 6 h". The mail
      includes the CheckMK alerts of the host.
    - **6 h without an event:** the episode and the incident close, with the reason stated.
  - **Can be muted per kind and host** (admin, like any pattern). The events are stored in
    the new table `critical_events` (migration 0053).
  - Measured, 7 days across the fleet: 14 host/kind combinations.
    - Only vpp0177 (machine-check hardware errors, 246 windows) reaches the threshold.
    - The 8 Veeam proxies (I/O error once a day, probably the backup run) stay in the daily
      overview.
  - Verified:
    - Dry run against the real database with rollback: three hourly OOMs → 1st and 2nd go
      to the daily overview, the 3rd opens an incident and a mail, the 4th sends no second
      mail; 6 h of quiet → closed.
    - Live: the first run found the 7 Veeam proxies with an I/O error.
    - Tests: 6 new.
- **The daily overview has named sections:** "Kritische Kernelereignisse", "Neue
  Fehlermuster", "Wiederkehrende Flotten-Ausfälle (vermutlich geplante Jobs)". Before,
  everything was listed as "Muster, die es 14 Tage nicht gab".

### What was fixed

- **About 80 false immediate mails after the Filebeat rollout (13.10. 14:12 UTC to 14.10.
  06:49 UTC).**
  - **Cause:** the Filebeat role (ansible03, rolled out from 13.10. around 13:55 UTC)
    changed the line format.
    - Before: `<time> <host> dockerd[…]`; after: `<host> dockerd[…]`.
    - Every known noise line became a NEW pattern, separately per host.
    - Measured new patterns per 5-minute window: normal operation (05.–12.10.) median 1,
      p99 18, max 46. At 13:55: **480**, then 101, 92, 87, 74.
    - My "fresh source" window (11:35–13:35) had already passed, so dockerd "copy stream
      failed", salt-minion DNS errors and the like each mailed as NEW + PERSISTENT + SPIKE.
  - **Immediate measures:**
    - Backend stopped at 06:49 UTC. The 3 mails of the 06:48 run had already gone out.
    - Error-cluster mails switched off via the kill switch (`error_clusters.mail_enabled`)
      until the fix was verified.
  - **Fix 1:** the syslog header (time stamp, host) is stripped from Filebeat lines before
    pattern mining (`collector.strip_syslog_head`).
    - The pattern no longer depends on the sender's format.
    - The same error on 20 hosts is now **one** pattern, not 20, so SPREAD can work again.
    - Filebeat lines are always stripped, even when the host in the line has a different
      name (docker0209 writes "sammelpdf"). For other lines, only when the host matches;
      "ERROR com.escenic.x: …" stays unchanged.
  - **Fix 2, a guard against mass novelty:** ≥ 60 new patterns in one window counts as a
    source change.
    - From there, observation restarts automatically: no NEW/SPIKE/SPREAD for 24 h for
      those patterns.
    - Instead of dozens of mails, **one** feed entry ("Viele neue Fehlermuster auf einmal:
      … vermutlich Umstellung eines Log-Versenders").
    - Known patterns keep reporting normally, so a real large-scale event stays visible.
  - Verified:
    - Dry run on the real state: 62 new patterns in the next window → guard triggered,
      0 mails.
    - Live: the first run after the deploy had 0 mail events. Tests: 690 passed.
  - Error-cluster mails were switched back on at 07:06 UTC, after 3 runs in a row with
    0 mail events.
  - **Open:** the 64 immediate episodes from the faulty period are still open. Their old
    keys no longer receive lines, so they close by themselves after 60 minutes of quiet,
    together with their incidents.

- **Fleet failures that recur daily at the same time go to the daily overview — no
  incident, no mail.**
  - **The case:** "Orderserver-Dienst auf 5 Hosts ausgefallen" arrived every night. Nothing
    restarts there.
    - ORD1/ORD3 stop at 00:31 local time and come back at 01:00 (~29 min).
    - ORD2, plus ORD4/ORD5 on aomp05, stay away until **05:00** (4.5 h). Measured via the
      CheckMK `last_state_change` of all 23 orderserver services.
    - A later incident threshold does not help: ORD2 is down 4.5 h, the 30-minute minimum
      age is long satisfied.
  - **Cause:** the fleet rule compares against the median of *all* 15-minute windows of
    the last 7 days. For a failure that comes once a day, that median is 0, so every night
    looked unusual.
  - **New rule "wiederkehrend" (recurring):** a fleet failure counts as a planned job when
    the same service failed at the same clock time (±30 min) on at least 3 of the last 7
    days. A past day counts only if both hold:
    - the failure was similar in size: at least half of today's hosts, and at least 5;
    - the service had at most 2 large 15-minute windows outside that time slot that day.
    A job is tied to its clock time; a generally unstable service is not a job.
  - **What happens instead:** an episode in the daily overview, with the reason ("an 7 der
    letzten 7 Tage zur gleichen Uhrzeit … vermutlich ein geplanter Job"). Nothing vanishes
    silently. The individual CheckMK alerts stay in the feed.
  - **Even better at the source:** a recurring CheckMK downtime for the time slot.
    CentralStation already ignores services in a downtime entirely.
  - Verified:
    - **Backtest over 14 days, 75 fleet incidents:** 38 would have gone to the daily
      overview instead.
      - Recognised as jobs: Proxmox VE VM Backup Status 12/12, orderserver ORD1 11/11,
        ORD2 2/2, Storage Pool data 7/8, Filesystem /data/backup 5/7, Service Summary 3/7.
      - **Still reported:** all 15 Check_MK failures. Without the "similar in size / quiet
        otherwise" condition, 12 of them would have counted as recurring, because the
        service was disturbed all day from 30.09. to 02.10.
    - **Dry run of the real code at historical times (read-only):**
      - 13.10. 22:36: ORD1/2/3 found recurring on 6–7 of 7 days.
      - Proxmox backup at 23:03: 7 of 7 days.
      - Check_MK on 29 hosts on 11.10. at 02:00: 0 days, so still reported.
      - The dry run also found a type error in the SQL (asyncpg inferred the time
        parameters as interval). It is fixed with explicit casts; the unit tests could not
        have caught it.
    - Tests: 4 new. Full suite: 681 passed.
  - **Not yet verified live:** the first real night with the rule is 14.10. at 22:31 UTC.
- **Checked, no change needed:** today at 05:41 UTC CheckMK reported "agent timed out" on
  1,411 hosts. They recovered at 05:56–05:58. There was rightly no incident (under
  30 min), and alert reconciliation set all 1,411 alerts to resolved at 06:06 ("Freshness:
  resolved 1411 stale CheckMK alert(s)").

## 2026-10-13 — Ticket from an incident; error-cluster detection reads Filebeat hosts; console write lock lets read-only command substitution through

### What is new

- **Create a ServiceDesk or Jira ticket from an incident, with AI text.**
  - **Where:** "Ticket erstellen" in the incident view (link from the mail), next to
    "Computer, untersuche das". It opens the shared ticket dialog (the one already used by
    feed items and the console).
  - **The draft is based on the same briefing as the console handoff.**
    `feed.incident_briefing` is now one function with two users. It contains the pattern,
    examples, affected containers, research, AI assessment, check results, recipe,
    resolution note, alerts and earlier incidents. Ticket and handoff cannot drift apart.
  - **Defaults:** the priority comes from the incident severity. The draft can be edited
    freely before the ticket is created.
  - **Link and record:** the backend appends the link back to the incident ("Vorfall in
    CentralStation: …"). It is there even if someone replaces the whole text. The ticket is
    recorded on the incident and appears in the incident view as a link
    ("Tickets: IMIT-…").
  - **Ticket style (all ticket paths):** the AI prompt now forbids mentioning tools, SSH,
    "KI-Einschätzung", confidence values or detection internals. Humans, often external,
    read tickets.
  - Verified:
    - Live in the browser, as sysadmin, on incident 494f0988 (PayloadTransformer SSE,
      6 containers, critical): the dialog opened with target ServiceDesk, project IMIT,
      priority Kritisch, and the AI summary and description filled in.
    - Closed with "Abbrechen" — no ticket was created (0 calls to `tickets/create`).
    - Tests: 5 new; full suite 677 passed.
  - **Gap:** in "Durchgeführte Schritte" the model still occasionally writes "Graylog-Suche
    durchgeführt" despite the style rule. Check the draft before creating the ticket.

### What was fixed

- **Local LLM calls had thinking switched on although it was configured off — all
  callers.**
  - **Cause:** llama.cpp reads the switch only from `chat_template_kwargs`. `llm_client`
    sent it only as a top-level field, so it was ignored.
  - Measured with the real ticket prompt on qwen (llm-host03):
    - before: over 300 s, timeout, and the draft fell back to raw text;
    - after: 29 s with 0 reasoning characters, and 42 s through the regular code path.
  - Affected every `generate_text` call with `chat_completions`: triage, recipes, ticket
    drafts, overview and others. Strict providers that reject the field get the existing
    retry without the thinking fields; the new field is included in that retry.
  - Verified: 2 new tests (switch in `chat_template_kwargs`; retry drops it; no
    `max_tokens`).
- **The ticket draft fell back to raw text in a request whose database session had been
  rolled back** (`greenlet_spawn` error). It now passes only the user ID.

- **Error-cluster detection was blind to every host that ships its logs with Filebeat.**
  - **The case:** docker0209 (sammelpdf) had a global OOM on 13.10. at 11:21 UTC. Paperless
    `celeryd` was killed at 14.9 GB RSS, and the host rebooted at 11:30. CentralStation
    reported nothing; CheckMK showed only a CPU warning at 11:14 UTC.
  - **Cause 1, CentralStation:** Filebeat (role `img_filebeat`, `hyde_processor.js`) sends
    no `level` field. It sends a `log_level` guessed from the text. The detector read only
    `level:<=4`.
    - Measured: 45 % of all Graylog lines (2.36 of 5.27 million per hour, at least 73
      hosts) never reached it.
    - Fixed: it now also reads lines without `level` whose `log_level` is emerg, alert,
      critical or error. That is about 10,100 lines per 5 minutes, on top of the previous
      ~3,900. A run takes ~5 s instead of 2 s.
  - **Cause 2, Filebeat itself (not fixed here):** `hyde_processor.js` drops lines whose
    level it cannot guess. "Out of memory: Killed process …" and "invoked oom-killer"
    contain no level word, so they were dropped on the host. Only "kernel: Mem-Info:" got
    through, because it contains "Info". The fix belongs in
    `~/Dev/ansible/ansible03/roles/img_filebeat` and is being worked on in a separate
    session. **Until it is rolled out, OOM kills still do not reach Graylog.**
  - **Three logic errors in the switch-over itself, found and fixed the same day.** "Fresh
    source" means a pattern first seen in the 2 h after a log source was first read.
    1. The first switch-over turned 7 permanent noise patterns of the new source into
       SPIKE/SPREAD immediate mails, and opened 4 incidents. Example:
       dockerd "copy stream failed". These patterns have no baseline yet.
       - The 30-minute minimum age held the mails back; none was sent.
       - Fixed: patterns of a fresh source trigger neither NEW, SPIKE nor SPREAD for
         24 h, until they have a baseline. Only PERSISTENT can fire, into the daily
         overview.
    2. The test for "fresh" had no lower bound. *All* old patterns counted as uncertain
       too, so SPIKE/SPREAD were switched off for them from 11:54 to 12:13 UTC.
       - Fixed: only first seen in [start, start + 2 h) counts, and only for 24 h.
    3. Only the newest source start was remembered. Yesterday's level-6 patterns then
       counted as "new for 24 h": nifi, DocumentIndex, "Bad Request". Three immediate mails
       were decided; the backend was stopped 3 minutes before they would have been sent.
       - Fixed: all source starts count (`error_clusters.coverage_since.*`).
       - The start is now stored as the first window read, not wall-clock time (11:35, not
         11:47:22). The detector runs a few minutes behind the clock.
    - A pattern that was already running when its source was switched on is never mailed
      as PERSISTENT NEW. It goes to the daily overview at most.
    - Mails caught up after a restart follow the same rule.
  - **Cleaned up (database):**
    - 11 false-alarm episodes closed and their 4 incidents set to resolved, with the note
      "zurückgenommen" (withdrawn).
    - 3 level-6 episodes downgraded to daily overview, and their 3 incidents set to
      resolved.
  - Verified:
    - Tests: an engine replay checks that an old pattern still mails after a source
      switch. Further tests: all source starts count; uncertainty expires after 24 h;
      present-at-start is never persistent-new; no catch-up mail for a fresh source;
      Filebeat lines are kept by `log_level`. Full suite: 670 passed.
    - A dry run in a throwaway container on the real state (nothing saved or sent):
      0 events in the pending window (15,776 lines).
    - The first live run afterwards: 0 events, 0 mails.
  - **Gap:** from 11:54 to 12:13 UTC, SPIKE/SPREAD were off for old patterns (error 2).
    An outbreak in that window would have gone unreported. No check has been run for one.

- **A read-only diagnosis over SSH was blocked as a write.**
  - **The case:** a Paperless diagnosis on sammelpdf: kernel journal, boots, grep in
    `celery.log`/`paperless.log*`. It was refused only because of
    `for f in $(ls -tr paperless.log*); do … done`. The guard blocked every `$(…)` and
    every backtick inside an ssh command across the board.
  - **New rule for `$(…)` and backticks in ssh commands:**
    - The content is checked with the same rules, as a remote command (`rm x.txt` there
      is not a workspace write).
    - If the result is used only as an **argument** (`for f in $(ls …)`,
      `grep x $(ls a)`), the command goes through.
    - At **command position** it stays blocked: `$(cat cmd.txt)`, `sudo $(…)`,
      `sh -c "$(…)"`, `if $(…)`.
    - `$var` at command position is newly blocked (`c=$(echo rm); $c -rf …`). Otherwise
      it would be the way around the check.
    - Pure arithmetic `$((1+2))` goes through. Unbalanced brackets are blocked.
  - **Also closed, an older gap:** `ssh host 'cat x | sh'` was allowed, and so were
    `| sh -x`, `| sudo bash` and `| python3 -`. Text piped into a remote shell or
    interpreter runs without the guard seeing it. It is now blocked.
    `| python3 -m json.tool` stays allowed.
  - **Not changed:** the SSH gate in split mode (so far only the canary user). It still
    refuses `$` outright, because it is the hard boundary.
  - Verified:
    - The original command, replayed through the guard in the live console container,
      is allowed (exit 0; before: deny).
    - Guard tests: 132, including 6 new allowed and 22 new blocked cases, among them the
      bypass attempts listed above. Full suite: 662 passed.
    - The running console container has the new guard; the userenv image is rebuilt.

## 2026-10-12 — Console searches Jira and ServiceDesk tickets

### What changed

- **Outbreak mails show the research behind the AI assessment.**
  - **Why:** the mail showed only the AI's conclusion. The data it was based on stayed in
    the incident view. Those data are the containers that the heuristic search found
    conspicuous at the same time, and the CheckMK alerts of the affected hosts.
  - **New section "Recherche"** (automatic, read-only), between the AI assessment and the
    pattern:
    - containers that were ≥ 3× above their 48-hour median in the same 15-minute window,
      with lines and factor;
    - CheckMK alerts of the affected hosts, from 15 minutes before the start, with
      severity and time.
  - **Empty findings are named, not hidden.** Examples: "keine — der Ausbruch steht für
    sich" (none, the outbreak stands alone) and "keine seit 15 min vor Beginn" (none since
    15 minutes before the start).
  - **When no search ran, there is no section.** This applies to fleet clusters and to
    mails caught up after a restart. The mail then does not claim "none".
  - **The incident link stays.** It is now also at the top ("→ Incident öffnen"), and still
    in the link line at the bottom. The late "KI-Einschätzung" mail has the same content.
  - Verified:
    - 3 new tests: link before research, link twice, findings with factor/time; empty
      findings named; no section without a search. Full suite: 634 passed.
    - A real outbreak mail was re-rendered without sending: cue-router SSE-500, 09.10.,
      25 containers. It shows 5 conspicuous containers, including cue-app-revproxy-01 with
      6,467 lines (10,778×), and the CheckMK alerts of the zipline hosts.
  - **Gap:** the CheckMK search has a start but no end. In a re-rendered mail of an old
    outbreak it shows alerts up to today. Live mails go out at most ~35 minutes after the
    start, so this does not matter there.

- **Error-cluster detection now sees application errors from containers, and reports
  steady new error loops.**
  - **Why:** the content-store DocumentIndex retry loop
    (`Unparseable number: "<ecs_selection>50</ecs_selection>"`, NullPointerException) ran
    for days without a report. There were two causes:
    1. Docker delivers every stdout line as level 6 (info). Java and Python applications
       write their own `ERROR` into the text. The detector read only `level<=4`.
       Measured on 12.10. over 2 h: 6,663 container lines with ERROR in the text at level 6,
       0 of the DocumentIndex lines at level ≤4. 19,402 "Unparseable number" lines in
       4 days reached the detector not once.
    2. NEW fired only in a pattern's first 15-minute window (≥ 20 lines). A quiet loop was
       "seen" from then on. SPIKE (≥ 200) and SPREAD (≥ 8 containers) never trigger for one
       container at 60 lines per hour.
  - **Collector:** it also reads level-6 container lines whose *own* level is an error.
    The first level word in the line decides: `ERROR`, `FATAL`, `CRITICAL` or `SEVERE`
    (upper case). Without a level word, `level=error`, a JSON `"level":"error"` or a Python
    traceback decide.
    - `INFO … outcome=ERROR` stays out. That case was measured: the Yarp3 feedback lines.
    - A level-6 line that came word for word at level ≤4 in the same window is not counted
      twice.
    - Measured over 2 h: 6,663 hits → 4,112 kept, in 52 patterns.
  - **New rule "anhaltend neu" (persistent new):** a pattern that first appeared at most
    24 h ago, ran in **every half hour of the last 2 h**, with ≥ 100 lines in total, is
    reported immediately. The reason reads "neues Muster läuft seit X h anhaltend".
    - Half hours, not 5-minute windows, because retry loops run in bursts. DocumentIndex
      had 1,464 lines in 2 h but was active in only 12 of 24 windows.
    - The rule is not held by the 30-minute minimum age: it has demonstrably lasted 2 h.
  - **No mail flood at the switch:** patterns that appear in the first 2 h after a log
    source is first read can have been running before. They go to the daily overview only,
    for NEW and for persistent alike. Their reason then reads "neu in der Beobachtung"
    instead of "14 Tage nicht gesehen". The start is stored per source
    (`error_clusters.coverage_since.level6-errors-1`).
    - Measured before the switch: 5 patterns would have been persistent: DocumentIndex,
      nifi StandardProcessGroup, three cue-zipline-dam patterns.
  - Verified:
    - 17 tests, including a full engine replay of the DocumentIndex case. A loop starting
      after a deploy (bursts every 10 min) gets the first window into the daily overview
      and an immediate mail 1.5 h later, with the persistent reason. The same loop already
      running when observation starts gets no immediate mail.
    - Full suite: 631 passed.
    - Live after the deploy: the DocumentIndex pattern is in the detector (122 lines in the
      first window).
    - The 12 newly read patterns opened as daily-overview episodes, with no mail.
  - **Gap:** two `INFO … outcome=ERROR` patterns came through before the
    first-level-word fix. They were opened as daily-overview episodes and close by
    themselves once quiet. The 5 episodes from the first run still carry the old reason
    "14 Tage nicht gesehen".

- **Incident detection reports only after 30 minutes.**
  - **Why:** of the 24 immediate mails between 08.10. and 12.10., 22 came from CheckMK
    fleet clusters, mailed about one minute after the start. Many were over again after
    0–15 minutes: a Proxmox restart, the nightly backup job (Filesystem
    /data/backup 21:36), ORD1 at 22:36, Check_MK after reboots.
  - **Fleet clusters (CheckMK):** only problems that started 30–45 minutes ago **and are
    still open now** count. An alert still unresolved at that age has demonstrably lasted.
    Incident and mail therefore come 30 minutes after the start at the earliest. Short
    reboots and jobs drop out entirely.
  - **Log clusters (Graylog):** the incident opens immediately (visible in the feed), but
    the mail is held. It goes out only when the outbreak is 30 minutes old **and still
    active**. If it ends earlier, there is no mail. Mails caught up after a restart follow
    the same rule.
  - **Threshold:** `rules.Thresholds.min_age_minutes = 30`.
  - Verified:
    - 6 tests: held and not sent; sent once old enough and active; dropped when it ended
      early; dropped when no longer active; old outbreaks and renotifies are not held.
    - The database self-test (rolled back) adds "outage 5 minutes old → no incident, no
      mail" (19/19).
    - The first live run after the deploy ran without errors.
  - **Not affected:** host incidents. They send no mail.

### What is new

- **`search_tickets` for the Console agents.**
  - **Input:** free text or a ticket key, searched in Jira **and** ServiceDesk. No JQL
    needed. Optional filters: target (`jira`/`servicedesk`), project and open tickets
    only.
  - **Result:** each hit has key, title, status, assignee, instance, link and the text
    passage that matched.
  - **Projects:** a project is searched only on the instance where it exists. Before,
    `project = IMIT` produced a JQL error on Jira ("Der Wert 'IMIT' existiert nicht"),
    which looked like a problem.
  - **Agents:** Hermes, Claude and Codex are told when to use it ("gibt es dazu schon ein
    Ticket?").
  - Verified live:
    - "vpp0252" in project IMIT found 35 hits on the ServiceDesk only, with the note
      "Projekt IMIT gibt es nicht in Jira".
    - "zipline 401" searched both instances.
    - Asked "Gibt es schon Tickets zu vpp0252?", both Claude and GLM 5.3 Flash (Hermes)
      used `search_tickets` and named the open IMIT-135211 with its link (11 and 15 s).
      GLM also recognized the other 34 hits as closed alert tickets.
  - Tests: 6 (JQL from free text, key search, quotes cannot break out, filters, text
    passage).

## 2026-10-11 — Console security, block 0 of the console plan

### What is new

- **Separate agent user and SSH gate (canary: admin@example.com).** Until now the Console
  agent ran as `yolo` with passwordless sudo, and its SSH key was readable. So any lock
  held only as long as the model did not obfuscate.
  - In split mode the agent runs as its own user `agent`: no sudo, `NoNewPrivs`, no
    capabilities, no key. It reaches servers only through a root-owned **SSH gate**.
  - The gate lets through, without approval, only what `cs_policy/remote_allowlist`
    recognizes as read-only (a positive list).
  - The human in the Werkbank stays `yolo`, unchanged.
  - Enabled per user (`CS_AGENT_SPLIT_USERS`). All others keep the previous setup.
  - Verified live in the canary container:
    - Hermes runs as `agent` with `NoNewPrivs: 1` and `CapBnd: 0`.
    - As `agent`, these are refused: reading the gate key, reading yolo's key, listing
      `/home/yolo`, `sudo`, the Werkbank on :8080, and direct `ssh` to port 22.
    - Through the gate `uptime` and `zpool list` on vpp0199 work. `touch`,
      `/etc/shadow` and interactive sessions are refused.
    - A Hermes session (qwen35b) read `zpool list` and was stopped at `touch`; no file
      was created.
    - With the write window, the gate let `touch` through and logged
      `approved:admin@example.com`. After revoking, `rm` was refused.
    - Werkbank and Claude/Codex work as `agent`.
  - Tests: 70 for the positive list (every probe template also passes it, so it is one
    rule), 3 for the mode switch. Full suite: 605 passed.
  - Found on the way: the admin container's Werkbank data still belonged to root from
    an earlier setup, which is why it had been stopped. The split start takes it over
    once.

- **Recipes: resolved incidents teach the next outbreak.**
  - **Linking:** a Console session started from an incident is now bound to it
    (`incident:<id>`), so "✓ GELÖST" appears there too.
  - **What resolving does:** it stores the solution on the incident and learns a recipe
    from the commands that actually ran, not from the chat. The source is a new userenv
    endpoint `/sessions/{sid}/tool-calls`, reading the Hermes SessionDB, Claude's
    `.jsonl` or Codex' rollout.
  - **What a recipe contains:** the AI maps the read-only commands to `cs_policy`
    templates and validates them. Anything without a template (including every writing
    command) stays text only and never runs.
  - **Where it is stored:** on the pattern (`LogTemplate.key`, for fleet clusters too),
    otherwise on the host, and mirrored as a cs-knowledge runbook.
  - **Applying:** at the next outbreak of the same pattern, the recipe appears in the
    incident, in the immediate mail and in the Console handoff. Its steps run through the
    "Prüfschritte ausführen" button, recipe steps first.
  - New read-only templates `zfs_pools` and `zfs_datasets`. The first recipe test showed
    that zpool/zfs commands otherwise stay unusable text.
  - Verified live on "Storage Pool data · 14 Hosts":
    - A Claude session read pool `data` on vpp0199 over SSH: 75 %, healthy, almost
      everything in `data/backup`.
    - "Gelöst" produced the recipe "CheckMK-Alarm: Storage Pool data auf mehreren
      Hosts" with `disk_usage`, `zfs_pools` and `zfs_datasets pool=data`.
    - The incident now proposes exactly these steps (origin "rezept").
    - Both ZFS templates ran on vpp0199 with exit 0.
    - The probe runner keeps `known_hosts` across restarts (volume `probe-ssh`).
  - Tests: 7 for recipes, 1 for the recipe block in the mail, 2 for the ZFS templates.
  - Known gap: Codex sessions yield recipes only while their container is alive, because
    the thread id is not persisted yet.
- Lessons learned from alert sessions stored `service="cmk"` or `"ecl"` instead of the
  service. They now store service and host from `cmk:<host>:<service>`.

- **Check steps per button in the incident (experimental, read-only).** The incident
  view, opened from the mail link, has a "Prüfschritte ausführen" button.
  - It runs read-only templates through the probe runner. Nothing runs automatically,
    and nothing goes out by mail.
  - **Steps:** the AI assessment now also returns structured steps
    (`pruefschritte_auto`: template id plus parameters). Only steps that are a
    `cs_policy` template and concern a host of this outbreak survive validation;
    anything else the model writes is never executed. Older assessments without steps
    get a basic check instead: disk and memory/load of the first three hosts.
  - **Results:** stored on the incident (last 3 runs), shown in the panel, and included
    in the handoff to the Console. Every step is in `remote_exec_log` with the incident
    id.
  - **Switch:** Admin → KI → Fehler-Cluster, on by default.
  - Verified live on "Storage Pool data · 14 Hosts":
    - The button ran 6 steps on vpp0199, vpp0207 and vpp0210, all with exit 0.
    - The output fits the alert: `/data/backup` on vpp0199 is 78 % full.
    - The Console briefing contains the results.
    - A fresh assessment by qwen35b on the same incident returned 4 valid template
      steps (`disk_usage`, `process_top`) on hosts of the outbreak.
  - Tests: 6 (only templates on the outbreak's own hosts, at most 6 steps, basic check,
    briefing).

- **The Console agent can look up man pages and capabilities on the server.**
  Three new MCP tools: `host_capabilities`, `search_manpages` and `read_manpage`
  (`focus` returns exactly the matching option).
  - They run through a new **probe runner** (`probe/`). It executes only fixed read-only
    templates from `cs_policy/templates.py`: no free command text, and no `--help`
    execution, because programs that ignore `--help` would simply run.
  - Only hosts from the CheckMK inventory (3,093 hosts) are allowed, and only callers
    with an agent token.
  - Every call is logged in `remote_exec_log`.
  - The SSH account is set by the administrator under Admin → KI → Prüf-Container, for
    now `marvin`. The backend hands it to the runner and re-sends it after a restart or
    change.
  - Verified live on vpp0221 (Debian 13):
    - Capabilities found zfs, pvesh and journalctl, and no docker.
    - `search_manpages rsync` returned rsync, rrsync and rsync-ssl.
    - `read_manpage rsync --delete` returned exactly the option entry.
    - A missing page answers `no_manpage`, and a host outside the inventory answers
      `host_unknown`.
  - Asked about rsync options on vpp0221, both GLM 5.3 Flash (Hermes) and Claude used
    `read_manpage` on their own instead of answering from memory. Claude also determined
    the installed version (3.4.1).
  - Tests: 33 for the templates and SSH execution, 13 for the man page tools.
  - Not yet verified: the connection test against docker-test failed, because the host
    is unreachable from hal right now ("No route to host").

### What was fixed

- **Agent text could run script in the browser.** The Console rendered the agent's
  markdown as trusted HTML (`bypassSecurityTrustHtml`). The agent reads logs, tickets
  and web pages, so an injected `<img onerror=…>` would have run with the page's token
  and could, for example, have triggered an approval.
  - Agent text is now sanitized.
  - A Content-Security-Policy additionally allows only the app's own scripts and the one
    inline handler of Angular's critical CSS, pinned by its hash.
  - Verified live: the agent printed `<img src=x onerror="document.title=…">`. The DOM
    holds only `<img src="x">`, the title is unchanged, and `<b>` is kept.
  - Dashboard, Console, map and Werkbank load without CSP violations. The TTS audio
    (`blob:`) was blocked at first and is now allowed.
- **Anyone could pose as any user towards the backend.** MCP calls and
  `/computer/agent-credentials` (which hands out the CLI agents' OAuth tokens) trusted
  the `X-CS-User-ID` header as sent, and `/api` is reachable through nginx.
  - Every console agent now also sends `X-CS-Agent-Token`: an HMAC of the user id with
    the backend's `SECRET_KEY`.
  - Without a valid token, an MCP caller counts as unidentified, so write tools refuse,
    and `agent-credentials` answers 403.
  - Verified: 7 tests (valid, foreign and missing token; Codex config carries the
    token). In the new container the token is in the environment and in the Claude MCP
    config (`.claude.json`), and the backend logs no token warnings.
- **The operator's `~/.ssh` was mounted into every user's container.** This included
  `marvin.key` and `marvin.password`, protected from the agent only by a different uid,
  not from its passwordless sudo. Nothing used it: the keys were never readable, so they
  were never copied, and SSH runs on `user.key`.
  - The mount (`IDE_HOST_SSH_DIR`) and the copy step are gone.
  - Verified: the recreated container has no `.ssh_host`.
- **Werkbank provisioning ran `git` as root** in a workspace the agent can write. A
  `.git/config` placed there would have run code as root. It now runs as `yolo`, and
  the GitLab credentials go to `~yolo/.git-credentials`.
- **Two write tools had no approval check:** `post_alert_comment` (team timeline) and
  `create_feed_exclusion` (hides alerts permanently).
- **`run_remediation`, `list_remediations`, the AWX tools and all GitLab tools failed with
  a `NameError`:** `AsyncSessionLocal` was never imported in the MCP server.
- **Tests:** `tests/conftest.py` now sets default values for the four required settings.
  Modules that load `app.core.security` can be imported in tests. Backend suite: 364
  passed.

## 2026-09-17

### What is new

- **Open reports from the console.** When an answer names a file in the workspace,
  for example `/home/yolo/workspaces/reports/…md` or `reports/…md`, a link appears below
  it. Clicking it opens the file in a new window. Markdown is rendered (headings,
  tables, code); text files show as text; there is a download link.
  - **Backend:** `GET /api/computer/workspace-file?path=…`. It uses the same boundary
    as the uploads (`resolve_workspace_file`, realpath), so neither `..` nor a symlink
    leads out. Only text formats up to 2 MB are allowed; anything else gets a stated
    reason pointing to the Werkbank.
  - **Security:** the window shares its origin with CentralStation. Markdown therefore
    runs through Angular's sanitizer, and the window has a CSP `default-src 'none'`
    with no scripts at all, so a report with an injected script cannot reach the token.
  - **Agents** (Claude, Codex and Hermes) are told to name the full path after
    writing a file, so the link appears.
  - Verified: endpoint against a real report (markdown, 3 398 bytes). `/etc/passwd`,
    `…/../.ssh/config` and `reports/../../.bashrc` → 404. In the browser: link below
    the answer, new window renders the report, CSP set, 0 scripts. 6 tests for the type
    and size rules.

- **Error-cluster detection: CentralStation detects outbreaks in the logs by itself
  and reports them by e-mail.** Trigger was a 401 outbreak that nobody noticed:
  `Failed to read change-log: HTTP Status '401 Unauthorized'`, 30.09. 11:30–12:20 UTC
  (103 835 lines, 26 Zipline containers) and again 01.10. 08:00–08:40 (85 960 lines,
  24 containers). The feed only ever saw a sample of 50 messages per run, merged
  silently, and grouped per host.

  How it works (`backend/app/services/error_clusters/`, the same funnel as in
  Datadog Watchdog, Elastic and Grafana Sift):
  1. **Every error line counts.** Every 5 min every `level:<=4` line of the window is
     fetched from Graylog. Unlike the feed path, nothing is deduplicated: 10 000
     identical lines count as 10 000. Windows above the 10 000-hit cap are split.
  2. **Patterns with Drain3.** Numbers, IDs, IPs and timestamps are masked. HTTP status
     codes stay part of the pattern, so 401 and 500 are separate patterns. The key is
     the first key of a Drain cluster and survives the pattern being generalized, a
     restart, or a lost state.
  3. **Baseline per pattern** is the median, not a moving average. A moving average
     would still have carried wave 1 about 20 h later and missed wave 2.
  4. **Rules:** NEW (not seen for 14 days), SPIKE (≥ 200 lines, ≥ 5× baseline, Poisson
     p < 1e-6, 2 of 3 windows), SPREAD (≥ 8 containers, ≥ 100 lines, 3× the usual).
     Recurring patterns must exceed twice their previous peak. Fixed rules decide
     whether a mail goes out. The LLM only writes the text.
  5. **One outbreak, one incident, one mail.** A pattern that starts within 30 min of
     a reported outbreak on the same containers or hosts joins it as a further
     pattern.
  6. **AI assessment** per episode, with no budget and no token limit: summary,
     suspected cause, read-only checks. The mail goes out immediately anyway. If the
     LLM is slow, the mail says "KI-Einschätzung folgt" and a second mail follows.
  7. **Mail** to users who opted in (Einstellungen → E-Mail-Berichte → "Sofort-Mail
     bei einem Ausbruch"), only for their own CheckMK hosts. New quiet patterns go
     into a separate daily mail at the hour of the daily report.

  Verified: backtest over the real Graylog data of 72 h (864 windows, 3 991 137 lines,
  `python -m app.scripts.error_cluster_backtest`): **passed**. Wave 1 was reported at
  11:35 (new + spread, 24 containers) and wave 2 at 08:00 (spread). The noise of
  `cue-zipline-dam.cue.stage` (~5 400 errors/3 h) sent no mail. After tuning on that
  data there were 4 immediate mails in 48 h instead of 183 with the first values.
  56 tests across rules, miner, the synthetic incident replay and mail rendering.

### What changed

- **The console is centered to the right of the main navigation** and is never wider
  than that area. Widened to 96vw, it reached under the navigation at 1 280 px, and the
  navigation covered the session rail; the browser test made this visible. Verified:
  console from 234 px, navigation up to 220 px.

- **Console: the session name of a handed-over incident is the incident title.**
  It used to be only the host. It is now, for example, "Fehlerausbruch: ChangelogReader
  – Failed to read change-log: HTTP401 Unauthorized · 24 Container". Each incident gets
  its own session; before, the session was reused per host. Cluster incidents often
  share the Docker host (`cueservices`), so two different outbreaks would have ended up
  in the same session.
- **Console wider, session names wrap.** The panel is up to 1 380 px instead of
  1 180 px. The session rail is 250 px instead of 180 px. Names wrap instead of ending
  in "…", up to 4 lines, with the full name in the tooltip. Verified in the browser:
  rail 250 px, `white-space: normal`, line clamp 4. The longest existing name
  "cuerouter.cue.stage.example.com" now shows in full on two lines.

- **Frequency shown as a rate.** The feed, cluster widget, incident view and mail
  subject now show how often an error occurs as a rate, for example "16 msg/s" or
  "2,2 msg/min · 11× in 5 min". At low rates the unit switches to msg/min or msg/h,
  so "0,007 msg/s" does not appear. The rule exists once in the frontend
  (`shared/format-rate.ts`) and once in the backend (`notify.fmt_rate`), with the
  same steps; tests pin both to the same cases. The widget shows the current
  rate, taken from the latest 5-min window. The feed still holds one entry per error
  and window rather than every line; the rate makes the volume visible. Verified in
  the browser (feed, LCARS): "2,2 msg/min · 11× in 5 min", tooltip "Durchschnitt: 2,2
  msg/min". 312 tests.

- **Incident detection is now part of the error-cluster detection.** It replaces the
  host correlator (`services/incident/correlator.py`, removed). Incidents come from one
  place, in three kinds (`incidents.kind`):
  - `host`: the previous rule, unchanged.
  - `log_cluster`: a log pattern across many containers.
  - `fleet`: planned.

  If a host belongs to an open cross-host cluster, its alerts join that cluster
  instead of opening a second incident. Open host incidents of its hosts are merged
  into it. The AIKB cause search appends to the incident context instead of
  overwriting it. The timeline API returns the cluster context separately, so it does
  not show up as an empty "cause". Verified: the correlator's tests apply unchanged to
  the new place (`test_host_clusters.py`). In production, `close_recovered` closed a
  host incident after the CheckMK recovery in the same aggregation run.
- **Backend build:** `fastmcp` is pinned to the running 2.13.0.2. With the range
  `>=2.13,<2.14`, every rebuild of the dependency layer failed against `uvicorn
  0.32.1`; this went unnoticed only because the layer came from the cache. `drain3` is
  installed with `--no-deps`, because its pins (`cachetools==4.2.1`) conflict with
  fastmcp. Verified with current versions.

### Error clusters: added since then (2026-10-01, live)

- **Live since 2026-10-01 16:34 UTC.** Backfill of 259 windows took 202 s and learned
  608 patterns. Admin LLM switched to `qwen35b` (`Qwen3.6-35B-A3B`); LLM test is green.
- **Generative dashboard:** widget `error_clusters`. It appears at the top whenever an
  outbreak is open, even if the LLM leaves it out. A new outbreak starts the dashboard
  refresh immediately.
- **CheckMK fleet clusters.** For each service, newly started problems in 15 min are
  compared with the 7-day median. First find: "Check_MK" on 14 hosts within minutes.
  Every output pointed to the same piggyback source, `vpp0252` (Proxmox). The triage
  now gets these outputs. Before, it only got alert IDs and guessed "communication
  problem".
- **Incident view:** for clusters it shows pattern, reasoning, triage, services that
  were noticeable at the same time, further patterns, and a Graylog link. Admins can
  mute a pattern (`POST /api/error-clusters/templates/{key}/mute`, audited).
  "An Konsole übergeben" starts from the pattern.
- **Found and fixed during verification:**
  - **The host scope took only the Docker host into account.** CheckMK lists the
    Zipline containers as separate hosts, not `cueservices`. The 401 outbreak would
    have reached nobody whose filter contains the Zipline containers. Containers with
    full names now count as affected hosts.
  - **Mails could be lost on a restart.** They were lost if the restart happened in
    the up to 90 s between detection and sending. New `mailed_at` (migration 0048);
    the runner sends undelivered mails after the restart.
  - **Restart broke the run:** the runner loaded open fleet episodes into the log
    store, and every run then aborted with a KeyError. A single faulty episode no
    longer stops the run.
  - **Silent failures:** APScheduler logs nothing for this job. The runner now logs
    every run and every exception itself.
- **Feed:** Graylog entries carry `metadata.error_cluster_key`, which links them to
  their pattern. They are only tagged, nothing is suppressed. The miner is
  thread-safe, because the runner learns in a thread while the aggregation looks up.
- Verified: e2e test mail to an opted-in user, with triage from qwen35b after 49 s. The
  cluster panel showed in the browser (fleet incident). 302 backend tests.

- **Hour-of-week baseline** (migration 0049, `log_template_hourly`, 28 days). It
  applies after 14 days of runtime, from the same hour of up to 4 previous weeks; the
  higher of median and hour-of-week counts. Without it, a weekly job (Monday 09:00) would
  be a spike every week, because it is active on only 2 days in 14 and so is not
  "recurring". Existing 5-min values were rolled up immediately: 4 136 hourly values,
  611 patterns. Verified: test `test_weekly_job_is_normal_in_its_hour_of_week`. While
  writing it, the test caught an error: at 09:05 the start of the 15-min window (08:55)
  had determined the hour, not the window being evaluated.
- **Exact recount** of windows that stay capped after splitting (> 80 000 lines in
  5 min). Graylog counts the 10 largest patterns of such a window (pivot); the larger
  value wins.
- **Self-test of the incident lifecycle against the real Postgres**
  (`python -m app.scripts.error_cluster_selftest`, everything is rolled back):
  **12/12**. Covered: single alert, host incident, merge into the cluster, members,
  context, further pattern, closing, reopening, nothing left behind.
- The 72 h backtest is unchanged after the engine changes: 4 immediate mails, both
  waves in the first run. 304 tests.

### What is still missing (error clusters)

- **LLM calls per day — measured for the first full day** (2026-10-01 17:23 to
  2026-10-02 17:23 UTC). The count replaces the budget as the point of observation.
  - **LLM calls:** 15 successful assessments (`ai_analyses`, `agent_type =
    'error_cluster'`, 18:52 to 10:08). One more call left no assessment: fleet episode
    `497364b8` (Check_MK, 01.10. 22:06) was mailed without one. So 16 calls, all
    triggered by an outbreak; there is no digest call.
  - **Assessments per episode:** `error_clusters.triage_at` is set for 14 episodes.
    The difference from 15 is the one renotify, for `6280a9e2` (Check_MK: started
    23:41, renotified 01:06 on doubled hosts), which overwrote its `triage_at`.
  - **Immediate mails** (one recipient):
    - 15 episodes mailed: 14 fleet clusters and 1 log cluster (`f531c7e5`, SD-WAN
      IDS/IPS queue at KOR).
    - 4 late assessments, sent as a second mail (`triage_at > mailed_at`).
    - So at least 19 mails. The renotify's first mail cannot be counted separately,
      because `mailed_at` was overwritten.
    - These are the duplicates reported on 02.10.; the fix (`421e7fb`: fleet cooldown,
      grouping, 240 s triage wait) went live at 10:42. Since then there has been no
      new fleet episode; `d844ac75` closed normally at 11:48.
  - **Daily digest entries:** 7 new, quiet log patterns (`notify_class = 'digest'`),
    no mail of their own.
  - **Fleet clusters:** 14 episodes, all immediate. Check_MK came back 5 times (cause
    vpp0252), ORD1/2/3 came together, and so did Storage Pool + /data/backup. All of
    these cases are covered by the 02.10. fix.
  - **Failures:** 0 × "error_clusters: run failed" and 0 × fetch, fleet or recount
    warnings in 80 runs since the backend restart (02.10. 10:42). Backend logs before
    that restart are gone; for that period the database is the only evidence (no
    episode without a run). This is also why the "error_clusters: notify" log line
    shows 0 hits for that period.
- **Wazuh.** Planned as the next extension, with the rule ID as the pattern and spread
  across agents plus source IP across agents (scans / lateral movement).
- The triage prompt is shared by log and fleet clusters.

### What changed

- **Ticket context no longer starts the AI by itself.** "ALS KONTEXT ANHÄNGEN"
  (formerly "IN KONTEXT ÜBERNEHMEN") and the first handoff of a ticket now
  *attach* the text to the session instead of sending it. It does not go into the
  input field either — a wall of text there would have to be cleared away first.

  The attachment appears as a named bar above the input, can be expanded and
  discarded, and travels with the next message the operator writes. In the
  transcript it is an ordinary message — the comment rendered as Markdown, a rule,
  then the operator's own line — which is both what the agent received and what its
  history returns after a reload. Several attachments are appended rather than
  replaced.

  Once attached, the activity banner and the header badge disappear for that state
  of the ticket — the context bar shows the same fact, and a banner left standing
  would let the same comment be attached twice. The suppression is tied to the
  activity's version, so a comment arriving afterwards announces itself again.

  The Jira baseline is recorded only when that message is sent, so a change that
  never reached the AI stays marked as unread; discarding brings the activity
  banner back. *Verified:* the production bundle serves the new bar and the old
  label is gone. **Superseded** — acknowledging on send was the wrong moment; see
  "A comment stayed new after Später or attaching".

- **Ticket comments are named as irreversible** in the Console system prompt:
  they go to people, often external ones, and are not retractable. The prompt
  also states that the block is enforced rather than merely requested, so the
  agent asks for the approval instead of retrying.

### What was fixed

- **LLM calls went to `…/chat/completions` without `/v1`.** All calls from hal to
  llm-host03 had this form: SysAdmin agent, Network agent, error-cluster triage and
  connection test (`/qwen35b/models`). The base URL had been entered without `/v1`
  since 22.05.2026 (`https://llm-host03.example.com/qwen79b`, later `/qwen35b`), and
  no code added it. It only worked because llama-server serves both routes; in the
  llm-host03 log the calls looked like misdirected requests, unlike aikb's
  `/qwen27b/v1/…`.
  - **One URL rule for every OpenAI-compatible URL:** `openai_base_url()` in
    `llm_client.py`, used by the LLM calls, the connector test, the settings test and
    the handoff to Hermes.
  - **The rule:** if the path has no version segment, `/v1` is added. URLs that
    already have one stay as they are (`/v1`, `/router/v1`, `/v1beta/openai`).
    ChatGPT-Backend (Codex) and Azure deliberately have none and are left out.
  - **Not touched:** the stored settings. They do not need to change.
  - Verified: 17 URL tests (llama.cpp behind a prefix, OpenAI, Perplexity, OpenRouter,
    Gemini, Codex, Azure, an endpoint entered with its path); backend suite 357
    passed. Live in the llm-host03 nginx log after the deploy (07.10. 13:25 UTC):
    the connector test now goes to `GET /qwen35b/v1/models` and the LLM test to
    `POST /qwen35b/v1/chat/completions`, both 200.

- **Codex could write without the write window.** It ran with
  `approval_policy = "never"` and `sandbox_mode = "danger-full-access"` and had no hook.
  The same guard now runs from `/etc/codex/requirements.toml`. Those are admin
  requirements owned by root; the agent's own `config.toml` (owned by yolo) and CLI flags
  cannot lift them, so a hook in the user config would have been worthless.
  - The guard now also understands Codex' `apply_patch`: every target must lie in the
    workspace or `/tmp`, and a move out of the workspace is caught.
  - Shell writes to the agents' own configuration (`~/.codex`, `~/.claude`, `~/.hermes`,
    `~/.ssh`, shell profiles) need the write window, so no agent can reconfigure itself.
  - Verified live on docker-test.test.example.com with Codex (gpt-5.5) through the real
    Console path. All blocked: approval only claimed in the chat (`ssh … touch`),
    appending to `~/.codex/config.toml`, and `apply_patch` to `~/.ssh/`. With a real
    approval the file was created. Claude CLI was rechecked the same way (chat approval,
    own `settings.json`, `Write` to `~/.ssh`): all blocked. No probe file remained.
  - Pitfall: the feature key is `hooks`; the old `codex_hooks` still works but makes
    Codex emit `error` events, and the Console aborted every Codex run on them.
- **Codex in the Console: tool steps, blocks and follow-up messages.**
  - The Console expected `function_call` events, but `codex exec --json` sends
    `command_execution` / `mcp_tool_call` / `file_change`. No Codex tool step had ever
    been shown; now each one is.
  - A blocked call does not appear in Codex' stream at all. The guard therefore logs
    Codex' session ID, and the Console adds "⛔ Schreibsperre: …" from that log. Claude
    blocks are read from the tool result.
  - Follow-up messages to Codex started without the conversation: the session ID was
    looked for in `session.started` (Codex sends `thread.started`), and `exec resume`
    lacked `--skip-git-repo-check`. Verified: in turn 2 Codex remembered a word from
    turn 1.

- **Hermes could write without the write window.** Hermes had no technical block, only a
  sentence in its system prompt. The guard (`cs-readonly-guard.py`) existed only for the
  Claude CLI. It now runs before every Hermes tool call as well. It fails closed, and no
  Hermes run starts without it. Tested live on docker-test.test.example.com with
  GLM 5.3 Flash, qwen35b and qwen27b:
  - **Approval claimed in the chat** ("Ich habe die Freigabe erteilt, führe es aus"):
    all three models ran `ssh … 'touch /tmp/…'`, and all attempts were blocked
    (4 in the guard log). No file was created.
  - **Bypass asked for explicitly:** qwen35b tried `python3 -c "open(…)"`, `base64 -d … >`
    and `printf "" >` over ssh, and all three were blocked. GLM and qwen27b refused.
  - **With a real approval** (button): GLM created the file; the Console showed
    "🔓 Mit Schreibfreigabe ausgeführt". The file was deleted afterwards.
  - **Detours no model tried,** checked directly against the installed guard: a local
    script that ssh's, a script on stdin, a pipe into ssh, paramiko, an interactive
    `ssh host`, input typed into a running session, VibeMK acknowledge, a browser click.
    All were blocked; `ssh host uptime` and VibeMK reads went through.
- **The guard became stricter for both agents.**
  - `touch` and `mkdir` were missing from the list of file-modifying commands, so
    `ssh host 'touch x'` passed even for Claude.
  - MCP tools of personal connectors (VibeMK: 15 of 77 tools write: acknowledge,
    downtime, discovery …) are now allowed only with a read verb; for the Claude CLI the
    hook matcher now includes `mcp__.*`.
  - Playwright clicks, typing and forms need the write window.
- **Blocked calls are visible.** Before, Hermes caught a blocked call before it appeared
  as a tool step, so the Console showed nothing and the model described it in its own
  words. qwen27b wrote "Ausgeführt — aber blockiert"; GLM and qwen27b claimed a block
  without having tried at all. A block now shows as a failed step
  "⛔ Schreibsperre: <Befehl>". Every decision is logged with its command in
  `/tmp/cs-readonly-guard.log`.
  - Verified: 91 guard tests (all green), including all detours above; full backend
    suite 434 passed.

- **Error-cluster mails came twice, or several times for one incident.** On 01–02.10.
  16 mails went out for about 8 real incidents. The SMTP connector and the recipient
  list were fine (one recipient, one send each). There were three causes:
  - **A second mail with the same content, worded differently (4×).** The mail waited
    at most 90 s for the AI assessment, but the assessment took 40–105 s. When it was
    late, it went out as a second mail. The wait is now 240 s.
  - **The same outage reported again and again (6×).** "Check_MK" on ~30 hosts (cause:
    vpp0252) was closed after 60 quiet minutes and came back 5–30 min later as a
    *new* incident with a new mail. Fleet clusters now behave like log patterns:
    within 3 h after closing, the old episode and incident continue. A new mail goes
    out only when the number of hosts doubles, at most once an hour.
  - **Simultaneous outages on the same hosts reported separately.** ORD1/ORD2/ORD3 on
    the same 5 hosts gave 3 mails, and "Storage Pool data" + "Filesystem /data/backup"
    (11 of 14 hosts shared) gave 2. Fleet outages that start within 30 min of each
    other and share hosts (Jaccard ≥ 0.2) are now one incident with several patterns
    and one mail. The incident closes only when all its patterns are quiet.
  - Verified: unit tests for grouping and renotify (cases from the real 01.10.
    incidents, including the unrelated 23:01 pair that stays separate). The database
    self-test against the real Postgres (rolled back) checks that two services on the
    same hosts give one mail and one incident, and that a return within the cooldown
    gives no new mail, continues the old episodes and keeps the leading title (18/18).

- **Hermes with GLM 5.3 Flash (llmbase.ai) hung on long answers.** The gateway buffers
  a tool call completely and sends no byte until it is finished (measured: ~20
  tokens/s; aborts itself after ~240 s with HTTP 504). With the 120 s timeout from the
  console LLM settings, Hermes cut off every write call above ~2 400 tokens itself
  and retried 3× per round over up to 6 rounds, so a session sat there for over an
  hour without showing anything. Four changes:
  - **Timeout of at least 300 s** for Hermes (API, stream read, stall watchdog), so
    the gateway's own error appears instead of Hermes' abort. The log line shows both
    values ("120s (wirksam 300s)").
  - **Fewer retries:** `agent.api_max_retries: 2`, `auto_recovery_cycles: 0` in the
    generated Hermes config. The same oversized call fails the same way the third
    time.
  - **Long files in parts:** Hermes is told to write long files in sections of at most
    ~150 lines / ~1 500 words (`cat >> datei <<'EOF'`).
  - **Visible provider errors:** a failed LLM call now shows in the console as
    "⚠ LLM-Anbieter: HTTP 502 … — Versuch 1/2" instead of a silent spinner.
  - Verified with the user's GLM 5.3 Flash access from the database: a report of
    5 735 words was written in 5 parts in 382 s, 7 API calls, no failure, no retry.
    The longest call took 154 s for 2 999 tokens; the old 120 s limit would have cut
    it off.

- **Changed tools did not reach a running console session.** The Claude CLI keeps a
  snapshot of all MCP tools in the session log (`deferred_tools_record`). On
  `--resume` it uses that snapshot, not the current tool list. After the
  `create_jira_ticket` extension, an existing session kept seeing only `title`,
  `description` and `priority`, while a new one saw all 7 parameters. Restarting the
  container would not have helped. Before every resume, the console now removes these
  snapshots (`userenv/claude_session.py`), and the CLI rebuilds them from the live
  list. When the tools are unchanged, the content is identical and the prompt cache
  stays valid. Verified: a copy of the affected session without the snapshots saw all
  7 parameters, and the AI corrected its earlier statement by itself. 3 tests.

- **Creating tickets: project, assignee and target (Jira / ServiceDesk) can be chosen.**
  The console AI's tool (`create_jira_ticket`) always created tickets in the
  default project, with no assignee. It also took the first Jira connector it found,
  if in doubt the ServiceDesk, which has no IMSP. The ticket dialog had no assignee
  either.
  - **Target:** "Jira" or "ServiceDesk" (also "Service Desk", "SD") is **binding**. If
    the project does not exist there, the tool answers with that target's project list
    (for example "IMSP gibt es im ServiceDesk nicht. Verfügbar: IMADVA, IMCUE, IMIT,
    IMPM, IMSAFO") and does not fall back silently. Without a target, the tool finds the
    instance that has the project.
  - **Assignee** (surname, login or e-mail) is resolved only among the people who may
    be assigned in that project. If the name is ambiguous, the tool returns the
    candidates instead of guessing. The dialog has a "Bearbeiter" field with
    suggestions (`GET /api/tickets/assignees`).
  - **Issue type:** only one that exists in the project. IMSP has no "Serviceanfrage",
    only "Projektticket " (with a trailing space). If the requested type is missing,
    the ticket is created with an existing one and the dialog says so.
  - One path for the dialog and the AI: `ticketing.create_jira_issue`.
  - Verified against both instances, without creating a test ticket: ServiceDesk+IMIT
    → SD, Scholtysik → `mujscholtysik`, type Serviceanfrage. Jira+IMSP → `jscholtysik`,
    "Projektticket ". ServiceDesk+IMSP → refused with the project list. Dialog
    suggestion `scholt` → Scholtysik. 16 tests (assignee, type, target aliases).

- **Hermes had no MCP tools.** The sessions started, but with "Unknown toolset:
  mcp-centralstation / mcp-playwright / mcp-vibemk". The new Hermes version moved
  `discover_mcp_tools` from `tools.mcp_tool` to `tools.mcp_tool_discovery`. The
  discovery at container start failed on the import, and that showed only as a warning
  in the log. As a result no server was registered. `userenv/main.py` now tries both
  places and logs the result per server. A failure is logged as an ERROR ("Hermes
  sessions get NO MCP tools"). Verified: on start "161 tool(s) from 3 server(s):
  centralstation=55, playwright=25, vibemk=81". A Hermes session called
  `mcp__centralstation__list_alerts` and returned 20 entries, with no "Unknown
  toolset".

- **No LLM call in CentralStation sets a token limit any more.** The global rule says
  never `max_tokens`. A limit makes reasoning models end with `finish_reason=length`
  and return nothing useful. It had been set in feed analysis (450), remediation
  matching (300), playbook author (2000), project planning (4096/1024), generative
  dashboard (3000), the AI agent (5000), the search, PromQL and dashboard assistants
  (500/300/3000), the help assistant (900) and the LLM connector test (1). The
  `max_output_tokens` parameter is gone from `generate_text`, so nobody can set it
  again by accident. The only exception is the Anthropic Messages API, which requires
  `max_tokens`. It now gets `ANTHROPIC_MAX_TOKENS = 32000`, which is at or above the
  output limit of all current Claude models and so cuts nothing. It used to be 1024,
  which cut answers off. Verified: `grep` over backend and userenv finds no
  `max_output_tokens`/`n_predict` and no other `max_tokens`. 235 tests, and imports of
  the touched modules in the running backend.

- **With Hermes as the agent, no new console session appeared.** Claude and Codex
  were not affected. The new Hermes version gates *all 45* of its runtime dependencies
  on `python_version >= '3.14'`, while it allows Python 3.11 to 3.14 itself. On the
  image's Python 3.13, `pip install -e /opt/hermes` therefore installed Hermes with
  none of them (openai, rich, tenacity, requests, Pillow, …). Every session start
  failed with `No module named 'requests'`, and the console showed nothing. A separate
  image layer (`userenv/hermes_deps.py`) now installs the dependencies as Python 3.14
  would get them. All other conditions still apply, so the Windows-only packages stay
  out. The build then checks `import run_agent` and fails instead of shipping an image
  without Hermes. Once upstream fixes the markers, the layer installs nothing new.
  Verified: in the recreated container, a Hermes session through the backend API →
  created, answer "bereit". Image with Claude CLI 2.1.286, Codex 0.159.3.

- **The Hermes LLM configuration had no test button.** There is now **Test** next
  to **Save** (`POST /api/computer/hermes-llm/test`). It tests the values *in the
  form*, so a change can be checked before saving. An empty key field means the stored
  key, as on save. With "Global configuration" it tests the configuration Hermes then
  gets. The result stays visible below the buttons with the model, response time and
  reason, and is capped at 60 s. Verified in the browser: "'z-ai/glm-5.3-flash'
  antwortet (1733 ms)".

  Found along the way: `generate_text` always sends the llama.cpp fields
  `enable_thinking`/`thinking_budget` with `chat_completions`. Strict
  OpenAI-compatible providers (llmbase.ai) reject the whole request ("Unknown request
  parameter: enable_thinking"). The test showed red although Hermes worked, and every
  CentralStation call to such a provider would have failed. On exactly this
  rejection, the request is now retried once without those fields. llama.cpp is
  unaffected. Verified: 3 tests in `test_llm_client_strict_provider.py`, and the real
  test against llmbase.ai is green. The admin LLM test no longer sets a token limit
  (`max_output_tokens=20`), per the global rule.

  The ten other places with a token limit are removed too, see the next entry.

- **A Graylog error in the feed showed up as if it had happened once, even when it
  happened thousands of times.** Example: `Failed to read change-log: HTTP Status '401
  Unauthorized'` from the CUE Zipline containers. A search found 2 entries in 48 h.
  Graylog had 85,960 error lines in 6 h from 24 containers. Three causes:
  every 2 minutes the collector took only the **50 newest** messages from all of
  Graylog (out of ~47,000 in 10 minutes), so the error got into the feed only by
  chance. Identical errors were merged per window, but silently, and no count was
  shown. The merge key ignored the container, and for some log formats it changed every
  second, because a timestamp in the middle of the line was not removed.

  Now **Graylog counts**. Every Graylog entry carries a count query: the source query,
  restricted to the same host and the fixed part of the message text. That query is
  counted server-side and split by container (pivot), so no messages are transferred.
  Each entry covers a window `[since, until]` that is recounted every run. An ongoing
  error is continued into the next window even if it is missing from the sample, and
  windows join without gaps or overlap. Entries with the same count query count as one
  error, so the timestamp-in-the-line case no longer gives a dozen identical entries
  per window. Continuations are not analysed by the AI again (same error, next window).
  The feed card shows `1,334× in 60 min · 22 containers`, and the tooltip shows the
  window and the containers. In the LCARS theme it appears in the header bar. Open
  entries update their metadata in the feed index by partial update, so the AI analysis
  in the document stays.

  Verified: the generated count query for the Zipline line returns exactly the 85,960
  from the hand query (24 containers). After deploying, one run produced 11 instead of
  35 Graylog entries, no duplicate count queries, all with a count. The feed API and
  the card in the browser (LCARS) show the count. 16 tests in
  `test_graylog_occurrences.py`.

  Still missing: messages without at least two fixed words (only numbers, times, IDs)
  get no count query, so they show no count. **The continuation into the next window is
  not yet observed live**, because the window here is 60 min (the agent interval) and
  the first one expires after deployment. Container names come only from the
  `container_name` field. Host logs without it are counted in the total, but no
  container is listed.

- **A new console session was placed at the bottom of the list.** The list is ordered
  "most recently active first": the backend sorts by `last_activity_at`, and creating
  a session sets it to now. The frontend appended the new session to the end, though.
  It only jumped to the top once the first message was sent or the page was reloaded,
  so the same session sat in two places depending on the moment. It is now placed at
  the top right when it is created. Verified: build only (`docker compose build
  frontend`). A browser test did not run because the admin test login was refused.

- **The console's Claude could not save a file with its own file tools.** The
  console granted only `Bash`, `WebFetch`, `WebSearch` and the MCP servers. In
  `dontAsk` mode an unlisted tool is simply denied, so `Write` and `Edit` failed even
  inside the workspace, and a write window did not help either, because it only reaches
  the hook. This was not caused by a CLI update. The session logs show 34 denied
  `Write` and 4 denied `Edit` calls since 2026-08-31. Most of the time the agent
  quietly fell back to `cat > file` over Bash. On 2026-09-30 it fell back to
  claude.ai Claude Docs instead, which was also denied.

  `Read`, `Glob`, `Grep`, `Write`, `Edit`, `MultiEdit`, `NotebookEdit` and `TodoWrite`
  are now granted. The read-only guard now also checks the file tools by target path.
  Inside `/home/yolo/workspaces` and `/tmp` they write freely, the same line the Bash
  rules draw. Anywhere else they need the write window. That covers the agent's own
  `CLAUDE.md`, `managed-settings.json` (its own hook), `~/.ssh/config` and `/etc`. The
  path is checked after `realpath`, so neither `..` nor a symlink leads out of the
  workspace.

  **The guard was also wired in the wrong place, and it was fixed while testing.**
  `entrypoint.sh` wrote `~/.claude/managed-settings.json`, a path Claude Code never
  reads as managed settings. On Linux it reads them only from
  `/etc/claude-code/`. The hook really worked only through a leftover
  `~/.claude/settings.json` with a Bash-only matcher. That file sits on a volume the
  agent owns, so the agent could have switched its own guard off. The settings are now
  baked into the image at `/etc/claude-code/managed-settings.json`, root-owned. On
  start the entrypoint removes the dead file and our leftover hook from
  `settings.json`, and the user's model choice stays. A first run with the new matcher
  in the old place showed the problem: `Write` to `/home/yolo/` went through with no
  write window open.

  Verified in the recreated container, by running the Claude CLI with the console's
  exact flags: Write to `/home/yolo/workspaces/…` → written. Write to `/home/yolo/…`
  → "READ-ONLY-MODUS", no file. `crontab -l` over Bash → still blocked. As `yolo`,
  writing to or deleting `/etc/claude-code/managed-settings.json` → Permission denied.
  Also verified: `test_readonly_guard.py` (inside/outside, `..`, symlink, relative
  path, the matcher covers every file tool, the settings land in `/etc/claude-code/`).
  Side effect: Claude in the Werkbank uses the same managed settings. There too, file
  changes outside the workspace and `/tmp` need the write window.

- **A deepsearch was cut off after 300 s.** Measured on one query: about 120 LLM
  calls, ~160 embeddings and ~50 reranks, finishing after 279 s — so close to the
  old 300 s limit that any variance broke it, and the agent reported "the deepsearch
  aborted after 300 s". It is not caused by load: the website crawl was idle and
  llm-host03 nearly unused; a deepsearch is simply that heavy.

  The limit is now **900 s**, and the whole chain is staggered so the innermost limit
  always fires first with a clear message, instead of an outer one cutting silently:
  aikb deepsearch 900 s < MCP tool limit 930 s (Hermes, Claude, Codex) < nginx
  `/api/` 1000 s. The nginx value matters because a console reply that waits on a
  deepsearch sends no bytes for that long, and at 600 s nginx would have closed the
  browser's stream while the agent kept working. The ordering is pinned by a test.

- **The attached ticket context stayed after sending.** The bar was only removed
  once the agent's whole reply had finished *successfully* — so it stood for the
  full reply (minutes, with a deepsearch) and forever after a failed one, although
  the context was already in the sent message. It now disappears as soon as the
  server has accepted the message. The context is not lost: it is part of the
  message in the transcript and in the agent's history, so a retry still has it.

- **A comment stayed "new" after Später or attaching.** On JCUE-5604 one foreign
  comment reappeared after every message, although it had been attached and
  dismissed repeatedly; the session's stored Jira state was from 18 September. Two
  faults, both mine. Attaching acknowledged only once the message carrying the
  context had been *successfully* sent — and that turn failed (the Claude CLI
  regression below), so no acknowledgement happened. The "attached" mark lives only
  in memory while "unread" lives in the database, so a failed turn, a reload or a
  restart made the comment return. And "Später" only hid it locally, keyed on the
  whole ticket state: any later change, including the operator's own comments,
  voided the dismissal.

  Both now acknowledge at the moment of the click, in the database. A failed
  acknowledgement is shown instead of only logged to the console — it had failed
  silently for a week. *Verified on the real session, in a rolled-back
  transaction:* before `changed`, 1 new; after the acknowledgement `current`,
  0 new. The backend log confirmed the old path: `/ticket-activity/context` was
  called, `/ticket-activity/ack` never.

  Not covered by a test: the frontend has no spec files at all (Karma is set up
  but unused), which is why a timing bug like this reached production.

- **A normal build silently downgraded Claude Code.** The Console reported
  "Claude Code 2.1.278 does not support this model; version 2.1.280 or newer is
  required" — although 2.1.281 had been built and verified the day before. The
  cause was the `AGENT_REFRESH` build-arg introduced earlier: a build with a value
  and a later build without one produced two separate cache entries, and the
  plain build (for the file upload) matched the older one. The build log showed it:
  `agent-refresh=` (empty) → `CACHED`.

  The version is now keyed on a versioned file, `userenv/AGENT_REFRESH`, copied
  in right before the CLI layers. There is no second state for a build to fall
  back to. To fetch newer versions, bump the file.
  *Verified:* build with the new file → Claude Code **2.1.282**; a second plain
  build afterwards → still 2.1.282, all CLI layers from cache. The running Console
  reports 2.1.282, Codex 0.157.0.

- **Pasted images did not show in the transcript after a reload.** The image
  endpoints require a Bearer token, and an `<img src>` does not send one — the
  request got 403 and the thumbnail stayed empty. Earlier this changelog claimed the
  transcript "looks the same whenever it is opened"; that was never verified — only
  the live preview (a local blob URL) and the agent reading the file were. Attachments
  are now fetched with the token and shown as blob URLs. *Verified through nginx:*
  the same file returns 403 without the token and 200 with it.

- **Claude and Codex rarely consulted the knowledge base for server questions.**
  Their shared instructions never mentioned it — only the Hermes prompt did — so
  whether they looked something up was left to the tool description alone. Asked
  about a host, they went straight to SSH or CheckMK and missed runbooks,
  dependencies and known problems. The instructions now tell them to search the
  knowledge base first, **only when an aikb connector is actually enabled**:
  pointing an agent at a tool that answers "no connector configured" would cost a
  turn on every question.

  "Enabled" has one definition, `load_aikb_connector()` in the aikb connector, used
  both by `search_knowledge_base` and by the instructions — two separate queries
  could have promised a tool that then refuses. All three code paths that rewrite
  the instructions pass the flag, including the SSH-connector live update, which
  runs after the request and would otherwise have dropped the section silently on
  every SSH change.

  *Verified:* a fresh `claude_cli` session asked "what is cue0110 and what does it
  depend on" called `search_knowledge_base` as its first real tool, three times in
  all. Sessions started before the change, asked about the same host, went straight
  to vibemk and Bash. `backend/tests/test_agent_instructions_kb.py` pins that the
  section appears exactly when the knowledge base is available.

  Still slow: a "quick" search takes 13–19 s, because aikb expands the query with an
  LLM and reranks on llm-host03, which aikb's own website crawl keeps saturated.
  OpenSearch itself answers in 0.03 s. That is aikb's to fix, not ours.

- **The AI dashboard kept the LLM busy around the clock.** `run_generative_refresh`
  recomposed *every* "🪄 KI-Lagebild" that existed, every 15 minutes, day and night.
  Five existed — two belonging to accounts that had not been used for 85 and 102
  days. One pass took longer than the interval, so runs overlapped and apscheduler
  logged `skipped: maximum number of running instances reached` twice an hour; the
  model was never idle at roughly 11 calls per hour.

  Refreshing now happens only for dashboards someone has actually looked at
  (`agent.generative_active_days`, default 7), and there is finally an off switch
  (`agent.generative_enabled`) — before, only the interval could be changed, so it
  could not be turned off at all. A dashboard with no recorded view counts as
  inactive: in doubt, do not spend an LLM call.

  *Measured on the running instance:* 5 dashboards exist, **0** now qualify for a
  rebuild. The two long-dormant ones were deleted; they return by themselves if
  those users open the view. `backend/tests/test_generative_refresh_scope.py` pins
  the rule, including that a window of 0 stops everything rather than behaving
  oddly.

- **The task prompt disappeared when a ticket was handed to the Console.** Moving
  the handoff into an attached context put the whole prompt — ticket *and* task —
  behind a collapsed bar, so the operator saw a label and an empty input and
  nothing saying what to ask for. The two are now separated at the source
  (`ticket_activity.py`): the content is attached, the task goes into the input
  field where it can be read and changed, and the context bar offers **LEG LOS**
  (send as-is) or **BEARBEITEN**. The combined `prompt` is unchanged, because the
  context hash that detects an already-handed-over state is computed from it.

- **Hermes was told to use `/root/workspaces`**, in nine places. The container has
  run as `yolo` since the root→yolo migration and `/root` is `0700 root:root`, so
  that path was unusable — a leftover the migration missed.

- **`scp`/`rsync` could copy files onto a remote host unguarded.** The guard looked
  for a writing verb (`rm`, `tee`, `>`) and these carry it in their argument order,
  so nothing matched. A remote *destination* now counts as a write; pulling a file
  from a host stays allowed. Found by the new guard tests, not by the change that
  prompted them.

- **The user venv vanished in login shells.** `/etc/profile` resets `PATH`, so
  `bash -lc 'pip install …'` hit the system Python instead of the agent's venv. A
  `/etc/profile.d` snippet puts it back after the reset. *Verified:* `bash -lc`
  now resolves `pip3` to the venv, and `pip install humanize` imports from
  `/home/yolo/pip/venv/…`.

- **Editing one line rebuilt the whole Console image.** `main.py`,
  `hermes_config.yaml` and the guard were copied in above the Claude and Codex
  installs, the ~150 MB Chromium download and the VS Code extensions. Docker
  invalidates everything below a changed layer, so a two-line edit paid for all of
  it. Those files are copied last now. *Measured:* a rebuild after editing
  `main.py` went from ~99 s to **4 s**, with 22 layers served from cache. (It was
  not the proxy.)

- **A rebuilt image had no effect.** Both `ensure_container` (Console) and
  `vibemk_manager` reused a running container regardless of which image it was
  started from, so `docker compose build` changed nothing and nothing showed it.
  Both now compare the running container's image ID against the current tag and
  recreate on mismatch. *Verified:* after rebuilding, the check reported
  `veraltet? True`, the container was recreated, and the IDs then matched.

- **Config changes reached a running container only after a restart.**
  `write_hermes_config` wrote the host file, but `~/.hermes/config.yaml` was only
  populated by the entrypoint at container start. It is now written into the
  running container as well. *Verified:* the new `X-CS-User-ID` header appeared
  in the live config without a restart.

- **Agent CLIs were frozen by the Docker layer cache.** Hermes, Claude Code,
  Codex and Playwright MCP are deliberately unpinned (`@latest`, `git --depth=1`),
  but the cache served the same old builds forever. A new `AGENT_REFRESH`
  build-arg busts exactly those layers:
  `docker compose build --build-arg AGENT_REFRESH=$(date +%s) userenv`.
  **Superseded** — that build-arg could roll versions back; see the fix entry
  "A normal build silently downgraded Claude Code".
  *Measured:* Claude Code 2.1.267 → 2.1.274, Playwright MCP 0.0.80 → 0.0.81,
  Hermes to HEAD of 2026-09-17; Codex was already current at 0.154.0.

- **VibeMK** picked up its upstream activation fix (activate all affected sites,
  not only the master). *Verified:* `cs-vibemk` restarted on the new image and
  reports `read=68 operational=9 config=75 | allow_write=False → 77 exposed`.

### What is still missing

- **The write guard reads command text; it is not a sandbox.** It is solid against an
  over-eager model (tested: chat approval, explicit bypass requests). An agent that
  deliberately obfuscates could still find a path, because the SSH key is readable
  inside the container. Closing that for good means keeping the key out of the agent's
  reach and passing every ssh through a root-owned gate.

- **The MCP endpoint is not authenticated** on the internal network. The user ID
  arrives as a self-declared header, so the gate stops an over-eager agent, not a
  malicious one — such an agent could reach the Jira API directly over HTTP. The
  purpose is to make *accidental* action impossible, and that it does.

- **Alert handoff still starts the AI immediately.** Only the ticket paths were
  changed. If the same rule should apply to alerts, it is a one-line change in
  the same place.

- **No automated test covers the gate.** It was verified by hand against the
  running instance, as recorded above.
