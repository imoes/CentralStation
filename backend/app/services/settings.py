from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_credentials, encrypt_credentials
from app.models.settings import GlobalSetting


@dataclass
class LLMConfig:
    base_url: str
    model: str
    api_key: str | None = None
    timeout_seconds: int = 120
    api_mode: str = "chat_completions"
    thinking_mode: bool = False

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.model)


@dataclass
class VisionConfig:
    base_url: str
    model: str
    api_key: str | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.model)


@dataclass
class SearXNGConfig:
    base_url: str
    enabled: bool = True
    results_count: int = 5

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url) and self.enabled


@dataclass
class AgentConfig:
    interval_minutes: int = 10
    aggregation_interval_minutes: int = 2
    auto_jira: bool = True
    auto_enrich: bool = True
    jira_severity_threshold: str = "critical"
    checkmk_locations: list = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.checkmk_locations is None:
            self.checkmk_locations = []


async def get_all_settings(db: AsyncSession) -> dict[str, str | None]:
    result = await db.execute(select(GlobalSetting))
    rows = result.scalars().all()
    out: dict[str, str | None] = {}
    for row in rows:
        if row.is_secret and row.value_encrypted:
            try:
                data = decrypt_credentials(row.value_encrypted)
                out[row.key] = data.get("v")
            except Exception:
                out[row.key] = None
        else:
            out[row.key] = row.value_plain
    return out


async def get_setting(db: AsyncSession, key: str) -> str | None:
    result = await db.execute(select(GlobalSetting).where(GlobalSetting.key == key))
    row = result.scalar_one_or_none()
    if not row:
        return None
    if row.is_secret and row.value_encrypted:
        try:
            return decrypt_credentials(row.value_encrypted).get("v")
        except Exception:
            return None
    return row.value_plain


async def set_setting(db: AsyncSession, key: str, value: str | None) -> None:
    result = await db.execute(select(GlobalSetting).where(GlobalSetting.key == key))
    row = result.scalar_one_or_none()
    if not row:
        row = GlobalSetting(key=key, is_secret=False)
        db.add(row)

    if row.is_secret and value is not None:
        row.value_encrypted = encrypt_credentials({"v": value})
        row.value_plain = None
    else:
        row.value_plain = value
        row.value_encrypted = None


async def get_llm_config(db: AsyncSession) -> LLMConfig:
    s = await get_all_settings(db)
    return LLMConfig(
        base_url=s.get("llm.base_url") or "",
        model=s.get("llm.model") or "",
        api_key=s.get("llm.api_key"),
        timeout_seconds=int(s.get("llm.timeout_seconds") or 120),
        api_mode=s.get("llm.api_mode") or "chat_completions",
        thinking_mode=s.get("llm.thinking_mode", "false") == "true",
    )


async def get_vision_config(db: AsyncSession) -> VisionConfig:
    s = await get_all_settings(db)
    return VisionConfig(
        base_url=s.get("llm.vision_base_url") or "",
        model=s.get("llm.vision_model") or "",
        api_key=s.get("llm.vision_api_key"),
    )


async def get_searxng_config(db: AsyncSession) -> SearXNGConfig:
    s = await get_all_settings(db)
    return SearXNGConfig(
        base_url=s.get("searxng.base_url") or "",
        enabled=s.get("searxng.enabled", "true") == "true",
        results_count=int(s.get("searxng.results_count") or 5),
    )


def _csv_list(s: dict, key: str) -> list[str]:
    return [v.strip() for v in (s.get(key) or "").split(",") if v.strip()]


async def get_agent_config(db: AsyncSession) -> AgentConfig:
    s = await get_all_settings(db)
    return AgentConfig(
        interval_minutes=int(s.get("agent.interval_minutes") or 10),
        aggregation_interval_minutes=int(s.get("agent.aggregation_interval_minutes") or 2),
        auto_jira=s.get("agent.auto_jira", "true") == "true",
        auto_enrich=s.get("agent.auto_enrich", "true") == "true",
        jira_severity_threshold=s.get("agent.jira_severity_threshold") or "critical",
        checkmk_locations=_csv_list(s, "agent.checkmk_locations"),
    )
