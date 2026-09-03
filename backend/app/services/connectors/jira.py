"""Jira REST API connector.

Auth: Bearer Token (Personal Access Token)
Ref: llm-cmk-analyzer JQL dedup pattern
"""
import re

import httpx


def wiki_to_markdown(text: str, heading_offset: int = 0) -> str:
    """Convert Jira (Server/DC) wiki markup to Markdown.

    Jira Cloud returns ADF, which _adf_to_text flattens. Server/DC returns wiki markup
    as a plain string, and it used to be passed through untouched — so a description
    reached the console as literal "h2. Aufgabe" and "{{php.conf}}" instead of a
    heading and inline code.

    heading_offset demotes the ticket's own headings so they nest below the headings
    of whatever embeds them — without it a ticket's "h2." lands on the same level as
    the surrounding "## Beschreibung" and the structure reads flat.
    """
    if not text:
        return ""

    # Jira Server returns CRLF; without normalising, a stray \r rides along inside
    # every captured heading and list item.
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Fenced blocks first, so the inline rules cannot mangle their contents.
    blocks: list[str] = []

    def _stash(lang: str, body: str) -> str:
        blocks.append("```" + lang.strip(": ") + "\n" + body.strip("\n") + "\n```")
        return "\x00BLOCK%d\x00" % (len(blocks) - 1)

    text = re.sub(r"\{code(:[^}]*)?\}(.*?)\{code\}",
                  lambda m: _stash(m.group(1) or "", m.group(2)), text, flags=re.S)
    text = re.sub(r"\{noformat\}(.*?)\{noformat\}",
                  lambda m: _stash("", m.group(1)), text, flags=re.S)

    # {{monospace}} — and Jira's escaped form {{{}text{}}} for awkward contents.
    text = re.sub(r"\{\{\{\}(.*?)\{\}\}\}", r"`\1`", text, flags=re.S)
    text = re.sub(r"\{\{(.*?)\}\}", r"`\1`", text, flags=re.S)

    out: list[str] = []
    for line in text.split("\n"):
        m = re.match(r"^h([1-6])\.\s*(.*)$", line)          # h2. Heading
        if m:
            level = min(6, int(m.group(1)) + heading_offset)
            out.append("#" * level + " " + m.group(2))
            continue
        if line.startswith("bq. "):
            out.append("> " + line[4:])
            continue
        m = re.match(r"^\s*([*#]+)\s+(.*)$", line)          # nested lists (may be indented)
        if m:
            depth = len(m.group(1)) - 1
            bullet = "-" if m.group(1)[-1] == "*" else "1."
            out.append("  " * depth + bullet + " " + m.group(2))
            continue
        if re.match(r"^-{4,}$", line.strip()):
            out.append("---")
            continue
        out.append(line)
    text = "\n".join(out)

    # Mentions before links: [~mmustermann] is not a link and would otherwise survive
    # as literal brackets. Display only — writing one still needs the [~name] form.
    text = re.sub(r"\[~([\w.\-]+)\]", r"@\1", text)
    text = re.sub(r"\[([^\]|]+)\|([^\]]+)\]", r"[\1](\2)", text)   # [label|url]
    text = re.sub(r"\[(https?://[^\]]+)\]", r"<\1>", text)          # [url]
    # Jira *bold* -> **bold**. List bullets were consumed above, so a leading * here
    # really is emphasis.
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"**\1**", text)
    text = re.sub(r"\{quote\}(.*?)\{quote\}",
                  lambda m: "\n".join("> " + ln for ln in m.group(1).strip().split("\n")),
                  text, flags=re.S)
    text = re.sub(r"\{color:[^}]*\}(.*?)\{color\}", r"\1", text, flags=re.S)

    for i, b in enumerate(blocks):
        text = text.replace("\x00BLOCK%d\x00" % i, b)
    return text.strip()


def _adf_to_text(node) -> str:
    """Recursively convert Atlassian Document Format (ADF) node to plain text."""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    t = node.get("type", "")
    if t == "text":
        return node.get("text", "")
    if t == "hardBreak":
        return "\n"
    if t == "mention":
        return f"@{(node.get('attrs') or {}).get('text', 'user')}"
    if t == "emoji":
        return (node.get("attrs") or {}).get("text", "")
    if t == "inlineCard":
        return (node.get("attrs") or {}).get("url", "")

    children = [_adf_to_text(c) for c in node.get("content", [])]

    if t == "doc":
        return "\n\n".join(p for p in ("".join(children)).split("\n\n") if p.strip())
    if t == "paragraph":
        return "".join(children)
    if t in ("heading",):
        lvl = (node.get("attrs") or {}).get("level", 1)
        return "#" * lvl + " " + "".join(children)
    if t == "bulletList":
        return "\n".join(children)
    if t == "orderedList":
        return "\n".join(f"{i+1}. {c}" for i, c in enumerate(children))
    if t == "listItem":
        return "• " + "".join(children)
    if t == "blockquote":
        return "\n".join(f"> {l}" for l in "".join(children).splitlines())
    if t == "codeBlock":
        lang = (node.get("attrs") or {}).get("language", "")
        return f"```{lang}\n{''.join(children)}\n```"
    if t == "rule":
        return "---"
    return "".join(children)

from app.schemas.connector import ConnectorTestResult
from app.services.connectors.base import BaseConnector


class JiraConnector(BaseConnector):
    def _headers(self) -> dict:
        token = self.credentials.get("token", "")
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _api(self, path: str) -> str:
        return f"{self.base_url}/rest/api/2{path}"

    async def test_connection(self) -> ConnectorTestResult:
        try:
            async with self._client() as client:
                r = await client.get(self._api("/myself"), headers=self._headers())
                r.raise_for_status()
            data = r.json()
            return ConnectorTestResult(
                success=True,
                message=f"Jira OK — user: {data.get('displayName', '?')}",
            )
        except httpx.HTTPStatusError as e:
            return ConnectorTestResult(success=False, message=f"HTTP {e.response.status_code}")
        except Exception as e:
            return ConnectorTestResult(success=False, message=str(e))

    async def list_projects(self) -> list[dict]:
        """Return available projects: [{key, name}]. Works on Jira + ServiceDesk."""
        async with self._client(timeout=20.0) as client:
            r = await client.get(self._api("/project"), headers=self._headers())
            r.raise_for_status()
        out: list[dict] = []
        for p in r.json():
            key = p.get("key")
            if key:
                out.append({"key": key, "name": p.get("name", key)})
        return out

    async def search_issues(self, jql: str, fields: list[str] | None = None) -> list[dict]:
        fields = fields or ["summary", "status", "priority", "assignee", "created", "updated"]
        payload = {"jql": jql, "maxResults": 50, "fields": fields}
        async with self._client(timeout=30.0) as client:
            r = await client.post(self._api("/search"), headers=self._headers(), json=payload)
            if r.status_code == 400:
                msgs = r.json().get("errorMessages", []) or list(r.json().get("errors", {}).values())
                raise ValueError(f"Ungültige JQL-Abfrage: {'; '.join(msgs) if msgs else r.text[:200]}")
            r.raise_for_status()
        return r.json().get("issues", [])

    async def issue_exists_by_summary(self, project: str, summary: str) -> str | None:
        """JQL dedup — returns issue key if a matching open issue exists."""
        safe = summary.replace('"', '\\"')
        jql = f'project="{project}" AND summary~"{safe}" AND statusCategory != Done ORDER BY created DESC'
        issues = await self.search_issues(jql)
        return issues[0]["key"] if issues else None

    async def create_issue(
        self,
        project: str,
        summary: str,
        description: str,
        issue_type: str = "Bug",
        priority: str = "High",
        labels: list[str] | None = None,
    ) -> dict:
        payload = {
            "fields": {
                "project": {"key": project},
                "summary": summary,
                "description": description,
                "issuetype": {"name": issue_type},
            }
        }
        # Priority is instance-specific (names differ per Jira config). Only send
        # it when explicitly provided so a wrong/unknown name can't reject the
        # whole create — callers may retry without priority.
        if priority:
            payload["fields"]["priority"] = {"name": priority}
        if labels:
            payload["fields"]["labels"] = labels
        async with self._client(timeout=30.0) as client:
            r = await client.post(self._api("/issue"), headers=self._headers(), json=payload)
            if r.is_error:
                import logging
                logging.getLogger(__name__).warning(
                    "create_issue %s failed %s: %s", project, r.status_code, r.text[:500]
                )
            r.raise_for_status()
        return r.json()

    async def list_issue_types(self) -> list[str]:
        """Return non-subtask issue type names available in this Jira instance."""
        async with self._client(timeout=15.0) as client:
            r = await client.get(self._api("/issuetype"), headers=self._headers())
            r.raise_for_status()
        return [t["name"] for t in r.json() if not t.get("subtask", False)]

    async def transition_issue(
        self, issue_key: str, status_name: str, fields: dict | None = None
    ) -> None:
        """Transition an issue by name, optionally setting the transition's fields.

        Raises on an unknown status instead of returning quietly — a silent no-op left
        callers believing the issue had moved.
        """
        transitions = await self.get_transitions(issue_key)
        target = next(
            (t for t in transitions if t["name"].lower() == status_name.lower()), None
        )
        if not target:
            raise ValueError(
                f"Übergang '{status_name}' nicht verfügbar. "
                f"Möglich: {[t['name'] for t in transitions]}"
            )
        payload: dict = {"transition": {"id": target["id"]}}
        if fields:
            payload["fields"] = fields
        async with self._client(timeout=15.0) as client:
            r = await client.post(
                self._api(f"/issue/{issue_key}/transitions"),
                headers=self._headers(),
                json=payload,
            )
            r.raise_for_status()

    async def get_transitions(self, issue_key: str, with_fields: bool = False) -> list[dict]:
        """Available transitions. with_fields also returns each transition's screen
        fields, which is the only way to see that one is mandatory."""
        params = {"expand": "transitions.fields"} if with_fields else None
        async with self._client(timeout=15.0) as client:
            r = await client.get(
                self._api(f"/issue/{issue_key}/transitions"),
                headers=self._headers(),
                params=params,
            )
            r.raise_for_status()
        return r.json().get("transitions", [])

    @staticmethod
    def required_fields(transition: dict) -> dict:
        """Mandatory screen fields of a transition: {field_id: {name, allowed}}.

        Closing an issue is the case that matters — both instances make `resolution`
        mandatory on their done-transition, and a POST without it fails with 400.
        """
        out: dict = {}
        for fid, meta in (transition.get("fields") or {}).items():
            if not meta.get("required"):
                continue
            out[fid] = {
                "name": meta.get("name") or fid,
                "allowed": [v.get("name") for v in (meta.get("allowedValues") or []) if v.get("name")],
            }
        return out

    async def _post_transition(self, issue_key: str, target: dict) -> None:
        """POST a transition, auto-filling mandatory fields where the choice is safe.

        Only `resolution` is filled automatically, and only from the values the
        transition itself offers — preferring a "done" wording over rejection ones, so
        an automated status sync never resolves a ticket as Duplicate or Rejected.
        Any other mandatory field is left to the caller and surfaces as a 400.
        """
        fields: dict = {}
        req = self.required_fields(target)
        if "resolution" in req:
            allowed = req["resolution"]["allowed"]
            preferred = next(
                (a for a in allowed if a.lower() in ("fertig", "done", "erledigt", "gelöst", "geloest")),
                None,
            )
            if preferred or allowed:
                fields["resolution"] = {"name": preferred or allowed[0]}
        payload: dict = {"transition": {"id": target["id"]}}
        if fields:
            payload["fields"] = fields
        async with self._client(timeout=15.0) as client:
            r = await client.post(
                self._api(f"/issue/{issue_key}/transitions"),
                headers=self._headers(),
                json=payload,
            )
            r.raise_for_status()

    async def transition_issue_by_candidates(
        self,
        issue_key: str,
        status_names: list[str],
        target_category: str | None = None,
    ) -> str | None:
        """Try name candidates first, then fall back to statusCategory matching.

        target_category: Jira statusCategory key — "new", "indeterminate", "done"
        """
        # with_fields: a done-transition usually requires `resolution`; posting without
        # it fails with 400, which is how project/kanban sync silently stopped moving
        # issues to Done.
        transitions = await self.get_transitions(issue_key, with_fields=True)

        # Pass 1: match by transition name
        for candidate in status_names:
            target = next(
                (t for t in transitions if t["name"].lower() == candidate.lower()),
                None,
            )
            if not target:
                continue
            await self._post_transition(issue_key, target)
            return target["name"]

        # Pass 2: fall back to destination statusCategory key
        if target_category:
            target = next(
                (
                    t for t in transitions
                    if (t.get("to") or {}).get("statusCategory", {}).get("key") == target_category
                ),
                None,
            )
            if target:
                await self._post_transition(issue_key, target)
                return target["name"]

        return None

    async def update_issue(
        self,
        issue_key: str,
        *,
        summary: str | None = None,
        description: str | None = None,
        priority: str | None = None,
    ) -> None:
        fields: dict = {}
        if summary is not None:
            fields["summary"] = summary
        if description is not None:
            fields["description"] = description
        if priority is not None:
            fields["priority"] = {"name": priority}
        if not fields:
            return
        async with self._client(timeout=30.0) as client:
            r = await client.put(
                self._api(f"/issue/{issue_key}"),
                headers=self._headers(),
                json={"fields": fields},
            )
            r.raise_for_status()

    async def get_issue_detail(self, issue_key: str) -> dict:
        """Full issue detail: description (ADF→text) + all comments."""
        async with self._client(timeout=20.0) as client:
            r = await client.get(
                self._api(f"/issue/{issue_key}"),
                headers=self._headers(),
                params={"fields": "summary,description,comment,status,priority,assignee,created,updated,issuetype"},
            )
            r.raise_for_status()
            data = r.json()
            fields = data.get("fields") or {}

            comment_meta = fields.get("comment") or {}
            inline_comments = comment_meta.get("comments") or []
            total_comments = comment_meta.get("total", len(inline_comments))

            # Jira only returns the first few comments inline — fetch all if there are more
            if total_comments > len(inline_comments):
                rc = await client.get(
                    self._api(f"/issue/{issue_key}/comment"),
                    headers=self._headers(),
                    params={"maxResults": 200, "orderBy": "created"},
                )
                if rc.status_code == 200:
                    inline_comments = rc.json().get("comments") or inline_comments

        raw_desc = fields.get("description")
        description = _adf_to_text(raw_desc) if isinstance(raw_desc, dict) else (raw_desc or "")

        comments = []
        for c in reversed(inline_comments):
            raw_body = c.get("body", "")
            body = _adf_to_text(raw_body) if isinstance(raw_body, dict) else raw_body
            comments.append({
                "id": c.get("id"),
                "author": (c.get("author") or {}).get("displayName", "?"),
                "body": body,
                "created": c.get("created"),
                "updated": c.get("updated"),
            })

        return {
            "key": data.get("key"),
            "summary": fields.get("summary"),
            "description": description,
            "status": (fields.get("status") or {}).get("name"),
            "priority": (fields.get("priority") or {}).get("name"),
            "assignee": (fields.get("assignee") or {}).get("displayName"),
            "created": fields.get("created"),
            "updated": fields.get("updated"),
            "comments": comments,
        }

    async def search_users(self, query: str, limit: int = 10) -> list[dict]:
        """Find users by name fragment. Returns [{username, display_name, email}].

        `username` is what a mention needs: Jira Server/DC notifies on [~username],
        not on a display name or an "@" prefix.
        """
        async with self._client(timeout=15.0) as client:
            r = await client.get(
                self._api("/user/search"),
                headers=self._headers(),
                params={"username": query, "maxResults": limit},
            )
            r.raise_for_status()
            data = r.json()
        if not isinstance(data, list):
            return []
        return [
            {
                "username": u.get("name") or u.get("accountId") or "",
                "display_name": u.get("displayName") or "",
                "email": u.get("emailAddress") or "",
                "active": u.get("active", True),
            }
            for u in data[:limit]
        ]

    async def add_comment(self, issue_key: str, body: str) -> dict:
        async with self._client(timeout=15.0) as client:
            r = await client.post(
                self._api(f"/issue/{issue_key}/comment"),
                headers=self._headers(),
                json={"body": body},
            )
            r.raise_for_status()
        c = r.json()
        raw_body = c.get("body", "")
        return {
            "id": c.get("id"),
            "author": (c.get("author") or {}).get("displayName", "?"),
            "body": _adf_to_text(raw_body) if isinstance(raw_body, dict) else raw_body,
            "created": c.get("created"),
        }

    async def get_unassigned_issues(self, project: str) -> list[dict]:
        jql = f'project="{project}" AND assignee is EMPTY AND statusCategory != Done ORDER BY created DESC'
        return await self.search_issues(jql)
