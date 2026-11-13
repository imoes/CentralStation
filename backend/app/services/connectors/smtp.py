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
        from_email  str  envelope / From header — required, fully qualified domain
        from_name   str  display name in From header
    """

    def __init__(self, base_url: str | None, credentials: dict) -> None:
        super().__init__(base_url, credentials)
        self.port       = int(credentials.get("port") or "587")
        self.tls          = str(credentials.get("tls",        "false")).lower() == "true"
        self.ssl          = str(credentials.get("ssl",        "false")).lower() == "true"
        self.auth         = str(credentials.get("auth",       "false")).lower() == "true"
        self.verify_ssl   = str(credentials.get("verify_ssl", "false")).lower() == "true"
        self.user         = credentials.get("user", "")
        self.password     = credentials.get("password", "")
        self.from_email   = (credentials.get("from_email") or "").strip()
        self.from_name    = credentials.get("from_name") or "CentralStation"

    def sender_problem(self) -> str | None:
        """Why this sender cannot be used, or None if it can.

        A sender without a fully qualified domain (e.g. ``centralstation@localhost``) is
        refused: the relay (PMG) signs the mail with DKIM first and only afterwards
        rewrites the From header to ``…@localhost.example.com``. The signed header no
        longer matches, the signature fails, and under DMARC p=reject Microsoft drops
        the mail (measured 2026-11-12: dkim=fail with the short sender, dkim=pass with
        the qualified one).
        """
        addr = self.from_email
        if not addr:
            return "SMTP connector has no sender address (from_email)"
        local, sep, domain = addr.rpartition("@")
        if not sep or not local or not domain:
            return f"sender address {addr!r} is not an e-mail address"
        if "." not in domain.strip("."):
            return (f"sender address {addr!r} has no fully qualified domain; the relay "
                    "would rewrite it after DKIM signing and the signature would fail")
        return None

    def _send_kwargs(self) -> dict:
        kw: dict = {
            "hostname": self.base_url or "",
            "port": self.port,
            "timeout": 30,
            "validate_certs": self.verify_ssl,
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
        problem = self.sender_problem()
        if problem:
            return ConnectorTestResult(success=False, message=problem)
        try:
            smtp = aiosmtplib.SMTP(
                hostname=self.base_url or "",
                port=self.port,
                use_tls=self.ssl,
                validate_certs=self.verify_ssl,
                timeout=10,
            )
            await smtp.connect()
            if self.tls and not self.ssl:
                await smtp.starttls(validate_certs=self.verify_ssl)
            if self.auth and self.user:
                await smtp.login(self.user, self.password)
            await smtp.quit()
            return ConnectorTestResult(success=True, message="SMTP-Verbindung erfolgreich")
        except Exception as exc:
            return ConnectorTestResult(success=False, message=str(exc))

    async def send(self, to: str, subject: str, html: str) -> None:
        import aiosmtplib
        msg = self.build_message(to, subject, html)
        await aiosmtplib.send(msg, **self._send_kwargs())

    def build_message(self, to: str, subject: str, html: str):
        """The complete message as it leaves CentralStation.

        Date and Message-ID are set here so that no relay has to add them after
        DKIM signing. Raises ValueError with the reason if the sender is unusable.
        """
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.utils import formataddr, formatdate, make_msgid

        problem = self.sender_problem()
        if problem:
            raise ValueError(problem)
        msg = MIMEMultipart("alternative")
        msg["From"]       = formataddr((self.from_name, self.from_email))
        msg["To"]         = to
        msg["Subject"]    = subject
        msg["Date"]       = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=self.from_email.rpartition("@")[2])
        msg.attach(MIMEText(html, "html", "utf-8"))
        return msg
