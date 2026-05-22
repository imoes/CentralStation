"""Microsoft Teams connector via Microsoft Graph API.

Uses the same Azure App (client_credentials) as the O365 connector.
Credentials: tenant_id, client_id, client_secret
"""
import httpx

from app.schemas.connector import ConnectorTestResult
from app.services.connectors.base import BaseConnector

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class TeamsConnector(BaseConnector):
    async def _get_token(self, client: httpx.AsyncClient) -> str:
        tenant_id = self.credentials.get("tenant_id", "")
        client_id = self.credentials.get("client_id", "")
        client_secret = self.credentials.get("client_secret", "")
        r = await client.post(
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        r.raise_for_status()
        return r.json()["access_token"]

    async def test_connection(self) -> ConnectorTestResult:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                token = await self._get_token(client)
                r = await client.get(
                    f"{GRAPH_BASE}/teams",
                    headers={"Authorization": f"Bearer {token}"},
                )
                r.raise_for_status()
            return ConnectorTestResult(success=True, message="Teams/Graph reachable")
        except httpx.HTTPStatusError as e:
            return ConnectorTestResult(success=False, message=f"HTTP {e.response.status_code}")
        except Exception as e:
            return ConnectorTestResult(success=False, message=str(e))

    async def get_joined_teams(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token = await self._get_token(client)
            r = await client.get(
                f"{GRAPH_BASE}/teams",
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
            return r.json().get("value", [])

    async def get_team_channels(self, team_id: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token = await self._get_token(client)
            r = await client.get(
                f"{GRAPH_BASE}/teams/{team_id}/channels",
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
            channels = r.json().get("value", [])
            for ch in channels:
                ch["team_id"] = team_id
            return channels

    async def get_channel_messages(
        self,
        channel_ref: str,
        top: int = 10,
    ) -> list[dict]:
        """Fetch recent messages from a channel.

        channel_ref format: "{team_id}:{channel_id}"
        """
        if ":" not in channel_ref:
            return []
        team_id, channel_id = channel_ref.split(":", 1)
        async with httpx.AsyncClient(timeout=20.0) as client:
            token = await self._get_token(client)
            r = await client.get(
                f"{GRAPH_BASE}/teams/{team_id}/channels/{channel_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
                params={"$top": top, "$orderby": "createdDateTime desc"},
            )
            r.raise_for_status()
            msgs = r.json().get("value", [])
            # Attach channel name from URL context
            for m in msgs:
                m["_channel_id"] = channel_id
                m["_team_id"] = team_id
            return msgs
