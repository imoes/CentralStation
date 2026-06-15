"""SMTP connector — wraps aiosmtplib for email dispatch."""
from __future__ import annotations

from .base import BaseConnector
from app.schemas.connector import ConnectorTestResult


class SMTPConnector(BaseConnector):
    """Send emails via an SMTP server.

    base_url  — SMTP hostname (e.g. "smtp.example.com")
    credentials keys:
        port        str  default "587"
        tls         str  "true" → STARTTLS after connect
        ssl         str  "true" → implicit TLS from start
        auth        str  "true" → login with user/password
        user        str
        password    str
        from_email  str  envelope / From header
        from_name   str  display name in From header
    """

    def __init__(self, base_url: str | None, credentials: dict) -> None:
        super().__init__(base_url, credentials)
        self.port       = int(credentials.get("port") or "587")
        self.tls        = str(credentials.get("tls",  "false")).lower() == "true"
        self.ssl        = str(credentials.get("ssl",  "false")).lower() == "true"
        self.auth       = str(credentials.get("auth", "false")).lower() == "true"
        self.user       = credentials.get("user", "")
        self.password   = credentials.get("password", "")
        self.from_email = credentials.get("from_email", "centralstation@localhost")
        self.from_name  = credentials.get("from_name", "CentralStation")

    def _send_kwargs(self) -> dict:
        kw: dict = {
            "hostname": self.base_url or "",
            "port": self.port,
            "timeout": 30,
        }
        if self.ssl:
            kw["use_tls"] = True
        if self.tls and not self.ssl:
            kw["start_tls"] = True
        if self.auth and self.user:
            kw["username"] = self.user
            kw["password"] = self.password
        return kw

    async def test_connection(self) -> ConnectorTestResult:
        import aiosmtplib
        try:
            smtp = aiosmtplib.SMTP(
                hostname=self.base_url or "",
                port=self.port,
                use_tls=self.ssl,
                timeout=10,
            )
            await smtp.connect()
            if self.tls and not self.ssl:
                await smtp.starttls()
            if self.auth and self.user:
                await smtp.login(self.user, self.password)
            await smtp.quit()
            return ConnectorTestResult(success=True, message="SMTP-Verbindung erfolgreich")
        except Exception as exc:
            return ConnectorTestResult(success=False, message=str(exc))

    async def send(self, to: str, subject: str, html: str) -> None:
        import aiosmtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        msg = MIMEMultipart("alternative")
        msg["From"]    = f"{self.from_name} <{self.from_email}>"
        msg["To"]      = to
        msg["Subject"] = subject
        msg.attach(MIMEText(html, "html", "utf-8"))
        await aiosmtplib.send(msg, **self._send_kwargs())
