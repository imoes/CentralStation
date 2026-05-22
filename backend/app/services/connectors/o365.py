"""Microsoft O365 connector via Azure App (client_credentials flow).

Credentials: tenant_id, client_id, client_secret
Scope: https://graph.microsoft.com/.default
"""
import httpx

from app.schemas.connector import ConnectorTestResult
from app.services.connectors.base import BaseConnector

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class O365Connector(BaseConnector):
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
                    f"{GRAPH_BASE}/organization",
                    headers={"Authorization": f"Bearer {token}"},
                )
                r.raise_for_status()
            return ConnectorTestResult(success=True, message="O365/Graph reachable")
        except httpx.HTTPStatusError as e:
            return ConnectorTestResult(success=False, message=f"HTTP {e.response.status_code}")
        except Exception as e:
            return ConnectorTestResult(success=False, message=str(e))

    async def get_unread_mails(
        self,
        mailbox: str,
        folder: str = "Inbox",
        top: int = 20,
    ) -> list[dict]:
        """Fetch unread mails from a shared/user mailbox."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            token = await self._get_token(client)
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            url = f"{GRAPH_BASE}/users/{mailbox}/mailFolders/{folder}/messages"
            r = await client.get(
                url,
                headers=headers,
                params={
                    "$filter": "isRead eq false",
                    "$top": top,
                    "$select": "subject,from,receivedDateTime,bodyPreview,importance",
                    "$orderby": "receivedDateTime desc",
                },
            )
            r.raise_for_status()

        mails = []
        for m in r.json().get("value", []):
            mails.append({
                "id": m.get("id", ""),
                "subject": m.get("subject", ""),
                "from": m.get("from", {}).get("emailAddress", {}).get("address", ""),
                "received_at": m.get("receivedDateTime", ""),
                "preview": m.get("bodyPreview", "")[:500],
                "importance": m.get("importance", "normal"),
            })
        return mails
