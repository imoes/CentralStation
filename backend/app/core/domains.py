"""Internal DNS suffixes — deployment data, not source code.

Several places need to recognise "this token is one of our hosts": hostname
extraction from alert text, the e-mail digest, topology enrichment. The suffixes
used to be hardcoded, which put the operator's internal domain into every clone and
every commit of this repository.

They now come from CS_INTERNAL_DOMAINS in the environment (i.e. the gitignored .env),
as a comma-separated list of DNS suffixes:

    CS_INTERNAL_DOMAINS=example.com,test.example.com

The default covers only the generic RFC-style suffixes, so an unconfigured
deployment still works and leaks nothing.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache

#: Suffixes assumed internal when nothing is configured.
DEFAULT_INTERNAL_DOMAINS = ("internal", "local")


@lru_cache(maxsize=1)
def internal_domains() -> tuple[str, ...]:
    """Configured internal DNS suffixes, longest first (so the regex prefers them)."""
    raw = (os.getenv("CS_INTERNAL_DOMAINS") or "").strip()
    items = [d.strip().lstrip(".") for d in raw.split(",") if d.strip()]
    items = items or list(DEFAULT_INTERNAL_DOMAINS)
    return tuple(sorted(set(items), key=len, reverse=True))


@lru_cache(maxsize=1)
def domain_alternation() -> str:
    """Regex alternation of the internal suffixes, e.g. r'example\\.com|internal'."""
    return "|".join(re.escape(d) for d in internal_domains())


@lru_cache(maxsize=1)
def host_pattern() -> re.Pattern:
    """Matches a FQDN ending in one of the internal suffixes."""
    return re.compile(rf"\b([\w.-]+\.(?:{domain_alternation()}))\b")
