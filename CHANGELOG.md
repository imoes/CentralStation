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

- **`CHANGELOG.md` exists and is linked from the README.**

### What changed

- **Ticket context no longer starts the AI by itself.** "ALS KONTEXT ANHÄNGEN"
  (formerly "IN KONTEXT ÜBERNEHMEN") and the first handoff of a ticket now
  *attach* the text to the session instead of sending it. It does not go into the
  input field either — a wall of text there would have to be cleared away first.

  The attachment appears as a named bar above the input, can be expanded and
  discarded, and travels with the next message the operator writes. That message
  keeps the operator's own wording in the transcript and carries the context as a
  collapsed block. Several attachments are appended rather than replaced.

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
