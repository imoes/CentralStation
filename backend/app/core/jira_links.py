"""Die eine Stelle, an der aus Jira-Instanz und Vorgangsschlüssel ein Link wird.

Jira und Service Desk zeigen einen Vorgang unter `<base>/browse/<KEY>`. Vorher stand
diese Regel an sechs Stellen als f-String — mit und ohne `rstrip('/')`.
"""
from urllib.parse import quote


def browse_url(base_url: str | None, key: str | None) -> str | None:
    """Link auf den Vorgang, oder None wenn Instanz oder Schlüssel fehlen."""
    if not base_url or not key:
        return None
    return f"{base_url.rstrip('/')}/browse/{quote(key, safe='')}"
