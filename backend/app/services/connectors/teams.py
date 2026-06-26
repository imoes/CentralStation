"""Microsoft Teams connector via delegated permissions (Device Code Flow).

Uses the same auth mechanism as o365.py (refresh_token grant).
Credentials: tenant_id, client_id, refresh_token (+ optional client_secret)
Scopes: ChannelMessage.Read.All Team.ReadBasic.All offline_access
"""
import httpx

from app.schemas.connector import ConnectorTestResult
from app.services.connectors.base import BaseConnector

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
DEVICE_CODE_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/devicecode"

DELEGATED_SCOPES = "ChannelMessage.Read.All Team.ReadBasic.All offline_access"


class TeamsConnector(BaseConnector):
    async def _get_token(self, client: httpx.AsyncClient) -> str:
        tenant_id = self.credentials.get("tenant_id", "")
        client_id = self.credentials.get("client_id", "")
        refresh_token = self.credentials.get("refresh_token", "")
        client_secret = self.credentials.get("client_secret", "")

        if not refresh_token:
            raise ValueError("Teams-Connector nicht autorisiert. Bitte 'Mit Microsoft anmelden' durchführen.")

        data: dict = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
            "scope": DELEGATED_SCOPES,
        }
        if client_secret:
            data["client_secret"] = client_secret

        r = await client.post(TOKEN_URL.format(tenant_id=tenant_id), data=data)
        r.raise_for_status()
        return r.json()["access_token"]

    async def test_connection(self) -> ConnectorTestResult:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                token = await self._get_token(client)
                r = await client.get(
                    f"{GRAPH_BASE}/me",
                    headers={"Authorization": f"Bearer {token}"},
                )
                r.raise_for_status()
                name = r.json().get("displayName", "")
            return ConnectorTestResult(success=True, message=f"Verbunden als {name}")
        except ValueError as e:
            return ConnectorTestResult(success=False, message=str(e))
        except httpx.HTTPStatusError as e:
            return ConnectorTestResult(success=False, message=f"HTTP {e.response.status_code}")
        except Exception as e:
            return ConnectorTestResult(success=False, message=str(e))

    async def get_joined_teams(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token = await self._get_token(client)
            r = await client.get(
                f"{GRAPH_BASE}/me/joinedTeams",
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
        if ":" not in channel_ref:
            return []
        team_id, channel_id = channel_ref.split(":", 1)
        async with httpx.AsyncClient(timeout=20.0) as client:
            token = await self._get_token(client)
            r = await client.get(
                f"{GRAPH_BASE}/teams/{team_id}/channels/{channel_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
                params={"$top": top},
            )
            r.raise_for_status()
            msgs = r.json().get("value", [])
            for m in msgs:
                m["_channel_id"] = channel_id
                m["_team_id"] = team_id
            return msgs
