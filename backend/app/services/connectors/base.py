from abc import ABC, abstractmethod

import httpx

from app.schemas.connector import ConnectorTestResult


class BaseConnector(ABC):
    def __init__(self, base_url: str | None, credentials: dict):
        self.base_url = (base_url or "").rstrip("/")
        self.credentials = credentials

    @abstractmethod
    async def test_connection(self) -> ConnectorTestResult:
        pass

    def _client(self, **kwargs) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=10.0, verify=False, **kwargs)
