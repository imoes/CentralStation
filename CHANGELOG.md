# Changelog

What changed in the *product* — new abilities, new screens, changed contracts,
things removed. Not a commit log: commits explain changes to code, this file
explains changes to what people can do.

Every entry names its evidence. An entry without proof is an intention, not a change.

This file starts on 2026-09-17. Earlier history lives in the commit log only.

---

## 2026-09-17

### What is new

- **Write operations now require consent that the AI cannot give itself.**
  Nine MCP tools that act outward — `jira_add_comment`, `jira_update_issue`,
  `jira_transition_issue`, `jira_close_issue`, `create_jira_ticket`,
  `acknowledge_alert`, `run_remediation`, `gitlab_create_branch`,
  `gitlab_create_merge_request` — refuse to run unless the user has opened a
  write window via **"Schreibzugriff freigeben"** in the Computer Console.

  This is the same consent channel that already governed shell commands
  (`userenv/cs-readonly-guard.py`): one approval, one storage location, a
  root-owned file inside the user's container that the agent cannot write.
  Agreement typed into the chat does not count, because the agent controls the
  chat.

  *Verified on the running instance:* a `jira_add_comment` call over
  `/api/mcp-http/` without a user header is refused with "Aufrufer ist nicht
  identifizierbar"; the same call carrying `X-CS-User-ID` is refused with "kein
  Schreib-Zeitfenster geöffnet"; after `set_write_approval` the gate opens and
  closes again on revoke. Read tools (`get_bridge_status`) were unaffected
  throughout.

- **MCP calls now carry the calling user.** Hermes, Claude CLI and Codex all send
  `X-CS-User-ID` to the CentralStation MCP server, so a per-user permission check
  is possible at all. *Verified:* the header appears in the live
  `~/.hermes/config.yaml` and changes the server's refusal reason.

- **The Console agent may install Python packages.** `pip install` into its own
  virtualenv (`/home/yolo/pip/venv`, persistent on the `cs-pip-{uid}` volume) no
  longer needs approval — the agent sometimes needs a library to evaluate anything
  at all, and that venv touches no system. `sudo pip`, `/usr/bin/pip`,
  `--break-system-packages`, `--target` into a system directory and any `pip
  install` over ssh stay blocked, as do `npm`, `gem`, `cargo` and `apt`.
  *Verified:* `backend/tests/test_readonly_guard.py` pins 40 decisions, allowed and
  refused, and passes.

- **Codex gets the same briefing as Claude.** Codex previously had no environment
  instructions at all — it knew nothing about SSH, the workspace or the read-only
  rule. One text now feeds both: `~/.claude/CLAUDE.md` for Claude,
  `$CODEX_HOME/AGENTS.md` for Codex.

- **The agent can attach files to Jira and ServiceDesk tickets.** Two new MCP
  tools, `jira_add_attachment` and `jira_list_attachments`, plus the connector
  methods they need — none of this existed before. The agent writes a file into its
  workspace and names the path; the content never travels through the chat, because
  the workspace is mounted into the agent's container and the backend alike.

  Only that workspace is reachable: paths are resolved with `realpath`, so `..`,
  foreign absolute paths and symlinks pointing outside are refused. Uploads are
  capped at 25 MB and require a write window like every other outward action.

  *Verified:* the tools are registered on the live MCP endpoint (51 tools);
  `jira_add_attachment` without an open window is refused with "kein
  Schreib-Zeitfenster geöffnet"; `jira_list_attachments` answers from the real Jira
  instance. `backend/tests/test_mcp_attachment_paths.py` pins 8 path decisions,
  including the symlink escape and reaching into another user's workspace.
  **Not yet exercised: the upload itself** — that writes to a real ticket and is
  waiting for the go-ahead.

- **The Console agents work in the Werkbank's workspace.** They ran in `/app`
  before, so anything they wrote landed in the container layer: invisible in the
  Werkbank and gone on the next rebuild. All three now start in
  `/home/yolo/workspaces`, the folder code-server opens — create a file in the
  Console, keep editing it in the browser IDE. *Verified:* a `claude_cli` session
  driven through the Console API answers `pwd` with `/home/yolo/workspaces`.

  No history was migrated, and none needed to be: Claude resolves `--resume` by
  session id regardless of the working directory. Checked before changing anything —
  a session recorded under `/app` resumed from the new directory with its full
  context (375k cached tokens read).

- **`rsync` is installed**, next to the `scp`/`sftp` that came with the SSH client.
  Fetching from a host stays free; pushing to one needs a write window.

- **Images can be pasted into the Console with Ctrl+V.** A screenshot goes into
  the shared workspace and the message carries only its path, so nothing large
  travels through the chat protocol. The same marker line is what the agent reads
  and what the frontend turns back into thumbnails after a reload. Sending with no
  text is allowed.

  *Verified end to end:* a generated red PNG was uploaded through
  `POST /api/computer/images` (stored as uid 1000 so the agent owns it), and a
  `claude_cli` session asked "which colour is this image" answered **"Rot"**. A
  PHP payload named `.png` is refused, and `../../../etc/passwd` as an image id
  returns 404.

- **`CHANGELOG.md` exists and is linked from the README.**

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
  label is gone.

- **Ticket comments are named as irreversible** in the Console system prompt:
  they go to people, often external ones, and are not retractable. The prompt
  also states that the block is enforced rather than merely requested, so the
  agent asks for the approval instead of retrying.

### What was fixed

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
