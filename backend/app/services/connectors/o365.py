"""Microsoft O365 connector via delegated permissions (Device Code Flow).

Auth flow:
  1. Initiate: POST /devicecode → get user_code + verification_url
  2. User enters code at aka.ms/devicelogin
  3. Poll /token with device_code grant → get access_token + refresh_token
  4. Store refresh_token in credentials; use it silently from then on

Credentials: tenant_id, client_id, refresh_token (+ optional client_secret)
Scopes: Mail.Read Mail.Send Calendars.ReadWrite offline_access
"""
import httpx

from app.schemas.connector import ConnectorTestResult
from app.services.connectors.base import BaseConnector

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
DEVICE_CODE_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/devicecode"

DELEGATED_SCOPES = "Mail.Read Mail.Send Calendars.ReadWrite offline_access"


class O365Connector(BaseConnector):
    async def _get_token(self, client: httpx.AsyncClient) -> str:
        tenant_id = self.credentials.get("tenant_id", "")
        client_id = self.credentials.get("client_id", "")
        refresh_token = self.credentials.get("refresh_token", "")
        client_secret = self.credentials.get("client_secret", "")

        if not refresh_token:
            raise ValueError("O365-Connector nicht autorisiert. Bitte 'Mit Microsoft anmelden' durchführen.")

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

    async def get_unread_mails(
        self,
        mailbox: str | None,
        folder: str = "Inbox",
        top: int = 20,
    ) -> list[dict]:
        """Fetch unread mails from the delegated user's mailbox."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            token = await self._get_token(client)
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            # With delegated permissions use /me/mailFolders/...
            url = f"{GRAPH_BASE}/me/mailFolders/{folder}/messages"
            r = await client.get(
                url,
                headers=headers,
                params={
                    "$filter": "isRead eq false",
                    "$top": top,
                    "$select": "id,subject,from,receivedDateTime,bodyPreview,webLink",
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
                "web_link": m.get("webLink", ""),
            })
        return mails
