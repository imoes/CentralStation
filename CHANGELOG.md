# Changelog

What changed in the *product* — new abilities, new screens, changed contracts,
things removed. Not a commit log: commits explain changes to code, this file
explains changes to what people can do.

Every entry names its evidence. An entry without proof is an intention, not a change.

This file starts on 2026-09-17. Earlier history lives in the commit log only.

---

## 2026-09-17

### What is new

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

### What is still missing (error clusters)

- Windows with more than 80 000 lines in 5 min only count a lower bound. Exact
  recounting by Graylog for this case is missing.
- The hour-of-week baseline only becomes possible after 14 days of runtime. Until
  then, the median over all windows applies.
- Wazuh is not included yet (v1: Graylog + CheckMK).
- The triage prompt is shared by log and fleet clusters. It is not tailored to fleet
  causes (hypervisor, piggyback source).

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

- **The MCP endpoint is not authenticated** on the internal network. The user ID
  arrives as a self-declared header, so the gate stops an over-eager agent, not a
  malicious one — such an agent could reach the Jira API directly over HTTP. The
  purpose is to make *accidental* action impossible, and that it does.

- **Alert handoff still starts the AI immediately.** Only the ticket paths were
  changed. If the same rule should apply to alerts, it is a one-line change in
  the same place.

- **No automated test covers the gate.** It was verified by hand against the
  running instance, as recorded above.
