"""Jira REST API connector.

Auth: Bearer Token (Personal Access Token)
Ref: llm-cmk-analyzer JQL dedup pattern
"""
import httpx


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
                "priority": {"name": priority},
            }
        }
        if labels:
            payload["fields"]["labels"] = labels
        async with self._client(timeout=30.0) as client:
            r = await client.post(self._api("/issue"), headers=self._headers(), json=payload)
            r.raise_for_status()
        return r.json()

    async def transition_issue(self, issue_key: str, status_name: str) -> None:
        """Transition an issue to a new status by name."""
        async with self._client(timeout=15.0) as client:
            r = await client.get(
                self._api(f"/issue/{issue_key}/transitions"),
                headers=self._headers(),
            )
            r.raise_for_status()
            transitions = r.json().get("transitions", [])
            target = next(
                (t for t in transitions if t["name"].lower() == status_name.lower()), None
            )
            if not target:
                return
            await client.post(
                self._api(f"/issue/{issue_key}/transitions"),
                headers=self._headers(),
                json={"transition": {"id": target["id"]}},
            )

    async def get_transitions(self, issue_key: str) -> list[dict]:
        async with self._client(timeout=15.0) as client:
            r = await client.get(
                self._api(f"/issue/{issue_key}/transitions"),
                headers=self._headers(),
            )
            r.raise_for_status()
        return r.json().get("transitions", [])

    async def transition_issue_by_candidates(
        self,
        issue_key: str,
        status_names: list[str],
        target_category: str | None = None,
    ) -> str | None:
        """Try name candidates first, then fall back to statusCategory matching.

        target_category: Jira statusCategory key — "new", "indeterminate", "done"
        """
        transitions = await self.get_transitions(issue_key)

        # Pass 1: match by transition name
        for candidate in status_names:
            target = next(
                (t for t in transitions if t["name"].lower() == candidate.lower()),
                None,
            )
            if not target:
                continue
            async with self._client(timeout=15.0) as client:
                r = await client.post(
                    self._api(f"/issue/{issue_key}/transitions"),
                    headers=self._headers(),
                    json={"transition": {"id": target["id"]}},
                )
                r.raise_for_status()
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
                async with self._client(timeout=15.0) as client:
                    r = await client.post(
                        self._api(f"/issue/{issue_key}/transitions"),
                        headers=self._headers(),
                        json={"transition": {"id": target["id"]}},
                    )
                    r.raise_for_status()
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
