"""OpenSearch client factory — used for the feed index."""
import os

from opensearchpy import AsyncOpenSearch

_client: AsyncOpenSearch | None = None


def get_opensearch() -> AsyncOpenSearch:
    global _client
    if _client is None:
        url = os.getenv("OPENSEARCH_URL", "https://opensearch:9200")
        user = os.getenv("OPENSEARCH_USER", "admin")
        password = os.getenv("OPENSEARCH_PASSWORD", "")
        use_ssl = url.startswith("https")
        _client = AsyncOpenSearch(
            hosts=[url],
            http_auth=(user, password) if password else None,
            http_compress=True,
            use_ssl=use_ssl,
            verify_certs=False,  # self-signed cert inside Docker network
            ssl_show_warn=False,
        )
    return _client


async def close_opensearch() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
