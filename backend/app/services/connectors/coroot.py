"""Coroot observability connector.

Fetches active (unresolved) incidents from Coroot projects and exposes
application/node overview for the MCP tool.

Auth: POST /api/login → cookie coroot_session (re-authenticated per call).
API: /api/project/{project_id}/incidents → data[].resolved_at==null
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import httpx

from app.schemas.connector import ConnectorTestResult
from app.services.connectors.base import BaseConnector

log = logging.getLogger(__name__)

_SEVERITY_MAP = {
    "critical": "critical",
    "warning":  "high",
    "info":     "medium",
}


class CorootConnector(BaseConnector):
    """Credentials keys: email, password, project_ids (JSON array of project IDs).

    project_ids defaults to ["cue-prod"] — matched by project name, not ID,
    so the connector resolves the ID from /api/user on each call.
    If project_ids is set to a JSON array of Coroot project IDs (e.g. ["c4pb1vqx"]),
    those are used directly.
    """

    # ── Internal helpers ────────────────────────────────────────────

    async def _login(self, client: httpx.AsyncClient) -> str:
        r = await client.post(
            f"{self.base_url}/api/login",
            json={
                "email": self.credentials.get("email", ""),
                "password": self.credentials.get("password", ""),
            },
        )
        r.raise_for_status()
        return r.cookies.get("coroot_session", "")

    async def _get_all_projects(self, client: httpx.AsyncClient, cookie: str) -> list[dict]:
        """Return [{id, name}] from /api/user."""
        r = await client.get(
            f"{self.base_url}/api/user",
            cookies={"coroot_session": cookie},
        )
        r.raise_for_status()
        return r.json().get("projects", [])

    def _configured_project_ids(self, all_projects: list[dict]) -> list[str]:
        """Resolve configured project_ids credential to actual Coroot project IDs.

        If project_ids is a JSON array of IDs → use directly.
        If project_ids is empty/missing → return only cue-prod (by name match).
        """
        raw = self.credentials.get("project_ids", "")
        if raw:
            try:
                ids = json.loads(raw)
                if isinstance(ids, list) and ids:
                    return ids
            except (json.JSONDecodeError, TypeError):
                pass

        # Default: use project named "cue-prod", or first project if not found
        for p in all_projects:
            if p.get("name", "").lower() == "cue-prod":
                return [p["id"]]
        return [all_projects[0]["id"]] if all_projects else []

    # ── Public API ──────────────────────────────────────────────────

    async def list_projects(self) -> list[dict]:
        """All available projects — used by the frontend discovery endpoint."""
        async with self._client() as client:
            cookie = await self._login(client)
            return await self._get_all_projects(client, cookie)

    async def test_connection(self) -> ConnectorTestResult:
        try:
            async with self._client() as client:
                cookie = await self._login(client)
                all_projects = await self._get_all_projects(client, cookie)
                configured_ids = self._configured_project_ids(all_projects)
                id_to_name = {p["id"]: p["name"] for p in all_projects}
                monitored = [id_to_name.get(pid, pid) for pid in configured_ids]
                return ConnectorTestResult(
                    success=True,
                    message=f"OK — Monitoring: {', '.join(monitored)} ({len(all_projects)} Projekte verfügbar)",
                    details={"projects": all_projects, "monitored": configured_ids},
                )
        except Exception as exc:
            return ConnectorTestResult(success=False, message=str(exc))

    async def get_incidents(self) -> list[dict]:
        """Active (unresolved) incidents across configured projects.

        Returns normalised dicts with keys:
          severity, title, body, external_id, metadata
        """
        results: list[dict] = []
        async with self._client() as client:
            cookie = await self._login(client)
            all_projects = await self._get_all_projects(client, cookie)
            project_ids = self._configured_project_ids(all_projects)
            id_to_name = {p["id"]: p["name"] for p in all_projects}

            for pid in project_ids:
                try:
                    r = await client.get(
                        f"{self.base_url}/api/project/{pid}/incidents",
                        cookies={"coroot_session": cookie},
                    )
                    r.raise_for_status()
                    data = r.json().get("data") or []
                    project_name = id_to_name.get(pid, pid)

                    for inc in data:
                        if inc.get("resolved_at") is not None:
                            continue  # skip resolved

                        app_id: str = inc.get("application_id", "")
                        app_name = app_id.split(":")[-1] if app_id else "unknown"
                        short_desc = inc.get("short_description") or "SLO violation"
                        severity_raw = inc.get("severity", "info")
                        severity = _SEVERITY_MAP.get(severity_raw, "medium")
                        impact = inc.get("impact", 0.0)
                        inc_key = inc.get("key", "")

                        # Build human-readable body
                        details = inc.get("details") or {}
                        avail_impact = details.get("availability_impact", {}).get("percentage", 0)
                        lat_impact = details.get("latency_impact", {}).get("percentage", 0)
                        duration_ms = inc.get("duration", 0)
                        duration_min = int(duration_ms / 60000)
                        opened_at_ms = inc.get("opened_at", 0)
                        opened_dt = datetime.fromtimestamp(opened_at_ms / 1000, tz=timezone.utc)

                        body_parts = [
                            f"Projekt: {project_name}",
                            f"Anwendung: {app_name}",
                            f"Seit: {opened_dt.strftime('%Y-%m-%d %H:%M UTC')} ({duration_min} min)",
                        ]
                        if avail_impact > 0:
                            body_parts.append(f"Verfügbarkeits-Impact: {avail_impact:.1f}%")
                        if lat_impact > 0:
                            body_parts.append(f"Latenz-Impact: {lat_impact:.1f}%")
                        if impact > 0:
                            body_parts.append(f"Gesamter Impact: {impact:.1f}%")

                        rca = inc.get("rca") or {}
                        if rca.get("short_summary"):
                            body_parts.append(f"RCA: {rca['short_summary']}")

                        results.append({
                            "severity": severity,
                            "title": f"{app_name}: {short_desc}",
                            "body": "\n".join(body_parts),
                            "external_id": f"coroot:{pid}:{inc_key}",
                            "metadata": {
                                "project_id": pid,
                                "project_name": project_name,
                                "application_id": app_id,
                                "application": app_name,
                                "incident_key": inc_key,
                                "severity": severity_raw,
                                "impact": round(impact, 2),
                                "opened_at": opened_dt.isoformat(),
                                "duration_minutes": duration_min,
                                "short_description": short_desc,
                            },
                        })
                except Exception as exc:
                    log.warning("Coroot: failed to fetch incidents for project %s: %s", pid, exc)

        return results

    async def get_application_overview(self, project_id: str) -> list[dict]:
        """Application list with incident status for the MCP tool."""
        async with self._client() as client:
            cookie = await self._login(client)
            # Get open incidents for this project and aggregate by app
            r = await client.get(
                f"{self.base_url}/api/project/{project_id}/incidents",
                cookies={"coroot_session": cookie},
            )
            if r.status_code != 200:
                return []
            incidents = [i for i in (r.json().get("data") or []) if i.get("resolved_at") is None]
            by_app: dict[str, dict] = {}
            for inc in incidents:
                app_id = inc.get("application_id", "")
                app_name = app_id.split(":")[-1]
                if app_name not in by_app:
                    by_app[app_name] = {
                        "application": app_name,
                        "project_id": project_id,
                        "incidents": [],
                    }
                by_app[app_name]["incidents"].append({
                    "key": inc.get("key"),
                    "severity": inc.get("severity"),
                    "description": inc.get("short_description"),
                    "impact": round(inc.get("impact", 0), 2),
                })
            return list(by_app.values())
