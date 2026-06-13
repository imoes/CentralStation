"""AWX / Ansible Automation Platform connector."""
from __future__ import annotations

from .base import BaseConnector
from app.schemas.connector import ConnectorTestResult


class AWXConnector(BaseConnector):
    """Execute and author Ansible jobs via AWX REST API v2.

    base_url   — AWX root URL, e.g. "https://awx.example.com"
    credentials keys:
        token          str   Bearer token (AWX Personal Access Token)
        verify_ssl     str   "true" / "false" (default "false")
        project_id     str   Default SCM project ID for playbook authoring
        inventory_id   str   Default inventory ID
        credential_id  str   Default machine credential ID
    """

    API = "/api/v2"

    def __init__(self, base_url: str | None, credentials: dict) -> None:
        super().__init__(base_url, credentials)
        self.token         = credentials.get("token", "")
        self.verify        = str(credentials.get("verify_ssl", "false")).lower() == "true"
        self.project_id    = credentials.get("project_id")
        self.inventory_id  = credentials.get("inventory_id")
        self.credential_id = credentials.get("credential_id")

    def _hdr(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    async def test_connection(self) -> ConnectorTestResult:
        async with self._client(verify=self.verify) as c:
            r = await c.get(f"{self.base_url}{self.API}/ping/", headers=self._hdr())
            if r.status_code == 200:
                return ConnectorTestResult(success=True, message="AWX reachable")
            return ConnectorTestResult(success=False, message=f"HTTP {r.status_code}")

    # ── Execution ──────────────────────────────────────────────────

    async def list_job_templates(self) -> list[dict]:
        async with self._client(verify=self.verify) as c:
            r = await c.get(
                f"{self.base_url}{self.API}/job_templates/",
                headers=self._hdr(),
                params={"page_size": "200"},
            )
            r.raise_for_status()
            return r.json().get("results", [])

    async def get_survey_spec(self, template_id: int) -> dict:
        async with self._client(verify=self.verify) as c:
            r = await c.get(
                f"{self.base_url}{self.API}/job_templates/{template_id}/survey_spec/",
                headers=self._hdr(),
            )
            r.raise_for_status()
            return r.json()

    async def launch(self, template_id: int, extra_vars: dict | None = None) -> dict:
        """Launch a job template. Returns {job: id, url: ...}."""
        payload: dict = {}
        if extra_vars:
            payload["extra_vars"] = extra_vars
        async with self._client(verify=self.verify) as c:
            r = await c.post(
                f"{self.base_url}{self.API}/job_templates/{template_id}/launch/",
                headers=self._hdr(),
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
            return {"job": data.get("job"), "url": data.get("url")}

    async def get_job(self, job_id: int) -> dict:
        async with self._client(verify=self.verify) as c:
            r = await c.get(
                f"{self.base_url}{self.API}/jobs/{job_id}/",
                headers=self._hdr(),
            )
            r.raise_for_status()
            return r.json()

    async def get_job_stdout(self, job_id: int) -> str:
        async with self._client(verify=self.verify) as c:
            r = await c.get(
                f"{self.base_url}{self.API}/jobs/{job_id}/stdout/",
                headers=self._hdr(),
                params={"format": "txt"},
            )
            r.raise_for_status()
            return r.text

    # ── Authoring ─────────────────────────────────────────────────

    async def list_projects(self) -> list[dict]:
        async with self._client(verify=self.verify) as c:
            r = await c.get(
                f"{self.base_url}{self.API}/projects/",
                headers=self._hdr(),
                params={"page_size": "100"},
            )
            r.raise_for_status()
            return r.json().get("results", [])

    async def project_update(self, project_id: int | str) -> dict:
        """Trigger an SCM sync for a project."""
        async with self._client(verify=self.verify) as c:
            r = await c.post(
                f"{self.base_url}{self.API}/projects/{project_id}/update/",
                headers=self._hdr(),
            )
            r.raise_for_status()
            return r.json() if r.content else {}

    async def list_project_playbooks(self, project_id: int | str) -> list[str]:
        async with self._client(verify=self.verify) as c:
            r = await c.get(
                f"{self.base_url}{self.API}/projects/{project_id}/playbooks/",
                headers=self._hdr(),
            )
            r.raise_for_status()
            return r.json()

    async def list_inventories(self) -> list[dict]:
        async with self._client(verify=self.verify) as c:
            r = await c.get(
                f"{self.base_url}{self.API}/inventories/",
                headers=self._hdr(),
                params={"page_size": "100"},
            )
            r.raise_for_status()
            return r.json().get("results", [])

    async def create_job_template(
        self,
        name: str,
        playbook: str,
        project_id: int | str | None = None,
        inventory_id: int | str | None = None,
        credential_id: int | str | None = None,
        ask_vars: bool = True,
    ) -> dict:
        payload: dict = {
            "name": name,
            "job_type": "run",
            "playbook": playbook,
            "ask_variables_on_launch": ask_vars,
            "project": project_id or self.project_id,
            "inventory": inventory_id or self.inventory_id,
        }
        if credential_id or self.credential_id:
            payload["credential"] = credential_id or self.credential_id
        async with self._client(verify=self.verify) as c:
            r = await c.post(
                f"{self.base_url}{self.API}/job_templates/",
                headers=self._hdr(),
                json=payload,
            )
            r.raise_for_status()
            return r.json()
