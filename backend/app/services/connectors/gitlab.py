"""GitLab connector — REST API v4 client for GitLab CE/EE."""
from __future__ import annotations

import urllib.parse
from typing import Any

from .base import BaseConnector
from app.schemas.connector import ConnectorTestResult


class GitLabConnector(BaseConnector):
    """Interact with a GitLab instance via REST API v4.

    base_url   — GitLab root URL, e.g. "https://gitlab.example.com"
    credentials keys:
        token               str   Personal Access Token (api scope for write ops)
        default_project_id  str   optional numeric project ID used as fallback
    """

    def __init__(self, base_url: str | None, credentials: dict) -> None:
        super().__init__(base_url, credentials)
        self.api = f"{(base_url or '').rstrip('/')}/api/v4"
        self.token = credentials.get("token", "")
        self.default_project_id = credentials.get("default_project_id")

    def _hdr(self) -> dict:
        return {"PRIVATE-TOKEN": self.token, "Accept": "application/json"}

    def _encode_path(self, path: str) -> str:
        return urllib.parse.quote(path, safe="")

    async def test_connection(self) -> ConnectorTestResult:
        async with self._client() as c:
            r = await c.get(f"{self.api}/version", headers=self._hdr())
            if r.status_code == 200:
                data = r.json()
                return ConnectorTestResult(
                    success=True,
                    message=f"GitLab {data.get('version', 'OK')} reachable",
                )
            return ConnectorTestResult(success=False, message=f"HTTP {r.status_code}")

    async def get_project(self, project_id: str | int) -> dict:
        """Fetch a single project (incl. path_with_namespace, http_url_to_repo)."""
        async with self._client() as c:
            r = await c.get(
                f"{self.api}/projects/{self._encode_path(str(project_id))}",
                headers=self._hdr(),
            )
            r.raise_for_status()
            return r.json()

    async def list_projects(self, search: str = "") -> list[dict]:
        params = {"membership": "true", "per_page": "100"}
        if search:
            params["search"] = search
        async with self._client() as c:
            r = await c.get(f"{self.api}/projects", headers=self._hdr(), params=params)
            r.raise_for_status()
            return r.json()

    async def get_file(self, project_id: str | int, path: str, ref: str = "main") -> dict:
        enc = self._encode_path(path)
        async with self._client() as c:
            r = await c.get(
                f"{self.api}/projects/{project_id}/repository/files/{enc}",
                headers=self._hdr(),
                params={"ref": ref},
            )
            r.raise_for_status()
            return r.json()

    async def create_or_update_file(
        self,
        project_id: str | int,
        path: str,
        branch: str,
        content: str,
        message: str,
    ) -> dict[str, Any]:
        enc = self._encode_path(path)
        url = f"{self.api}/projects/{project_id}/repository/files/{enc}"
        payload = {"branch": branch, "content": content, "commit_message": message}
        async with self._client() as c:
            # Try GET to decide POST (create) vs PUT (update)
            check = await c.get(url, headers=self._hdr(), params={"ref": branch})
            method = c.put if check.status_code == 200 else c.post
            r = await method(url, headers={**self._hdr(), "Content-Type": "application/json"}, json=payload)
            r.raise_for_status()
            return r.json()

    async def create_branch(self, project_id: str | int, branch: str, ref: str = "main") -> dict:
        async with self._client() as c:
            r = await c.post(
                f"{self.api}/projects/{project_id}/repository/branches",
                headers=self._hdr(),
                params={"branch": branch, "ref": ref},
            )
            r.raise_for_status()
            return r.json()

    async def list_merge_requests(self, project_id: str | int, state: str = "opened") -> list[dict]:
        async with self._client() as c:
            r = await c.get(
                f"{self.api}/projects/{project_id}/merge_requests",
                headers=self._hdr(),
                params={"state": state, "per_page": "50"},
            )
            r.raise_for_status()
            return r.json()

    async def create_merge_request(
        self,
        project_id: str | int,
        source_branch: str,
        target_branch: str,
        title: str,
    ) -> dict:
        async with self._client() as c:
            r = await c.post(
                f"{self.api}/projects/{project_id}/merge_requests",
                headers={**self._hdr(), "Content-Type": "application/json"},
                json={"source_branch": source_branch, "target_branch": target_branch, "title": title},
            )
            r.raise_for_status()
            return r.json()

    async def list_pipelines(self, project_id: str | int, ref: str = "main") -> list[dict]:
        async with self._client() as c:
            r = await c.get(
                f"{self.api}/projects/{project_id}/pipelines",
                headers=self._hdr(),
                params={"ref": ref, "per_page": "10"},
            )
            r.raise_for_status()
            return r.json()

    async def list_issues(self, project_id: str | int, state: str = "opened") -> list[dict]:
        async with self._client() as c:
            r = await c.get(
                f"{self.api}/projects/{project_id}/issues",
                headers=self._hdr(),
                params={"state": state, "per_page": "50"},
            )
            r.raise_for_status()
            return r.json()

    async def create_issue(self, project_id: str | int, title: str, description: str = "") -> dict:
        async with self._client() as c:
            r = await c.post(
                f"{self.api}/projects/{project_id}/issues",
                headers={**self._hdr(), "Content-Type": "application/json"},
                json={"title": title, "description": description},
            )
            r.raise_for_status()
            return r.json()
