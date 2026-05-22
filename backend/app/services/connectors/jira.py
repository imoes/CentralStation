"""Jira REST API connector.

Auth: Bearer Token (Personal Access Token)
Ref: llm-cmk-analyzer JQL dedup pattern
"""
import httpx

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

    async def search_issues(self, jql: str, fields: list[str] | None = None) -> list[dict]:
        fields = fields or ["summary", "status", "priority", "assignee", "created", "updated"]
        payload = {"jql": jql, "maxResults": 50, "fields": fields}
        async with self._client(timeout=30.0) as client:
            r = await client.post(self._api("/search"), headers=self._headers(), json=payload)
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
    ) -> str | None:
        transitions = await self.get_transitions(issue_key)
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

    async def get_unassigned_issues(self, project: str) -> list[dict]:
        jql = f'project="{project}" AND assignee is EMPTY AND statusCategory != Done ORDER BY created DESC'
        return await self.search_issues(jql)
