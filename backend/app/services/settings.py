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
    thinking_budget: int = 1500

    @property
    def is_configured(self) -> bool:
        # anthropic_messages (Claude OAuth) uses Anthropic's default endpoint —
        # no base_url required, just a model + OAuth token.
        if self.api_mode == "anthropic_messages":
            return bool(self.model and self.api_key)
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
class PrometheusConfig:
    """Settings for a future Prometheus deployment (node_exporter on hosts).
    Connector credentials (URL, auth) are stored per-connector in connector_configs.
    These global settings govern query behaviour defaults.
    """
    query_timeout: int = 30
    default_step: str = "1m"
    default_hours: int = 4

    @property
    def is_configured(self) -> bool:
        return True  # no required fields; depends on connector being present


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
    auto_jira: bool = False
    auto_enrich: bool = True
    rag_enabled: bool = True
    jira_severity_threshold: str = "critical"
    checkmk_locations: list = None  # type: ignore[assignment]
    workflow_web_search: bool = True
    # Alert scoring
    scoring_enabled: bool = True   # Master switch — off = all alerts go to LLM (Beta testing)
    enrich_score_threshold: int = 80
    max_alerts_for_llm: int = 30
    flap_window_minutes: int = 30
    flap_threshold: int = 3
    score_learning_enabled: bool = True
    score_delta_decay_days: int = 7
    worklist_interval_minutes: int = 15
    worklist_size: int = 15
    generative_interval_minutes: int = 15

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


async def set_secret_setting(db: AsyncSession, key: str, value: str | None) -> None:
    """Store a setting encrypted regardless of current is_secret flag.

    Use for long or sensitive values (OAuth tokens, API keys) that must not be
    stored in the VARCHAR(1024) value_plain column.
    """
    result = await db.execute(select(GlobalSetting).where(GlobalSetting.key == key))
    row = result.scalar_one_or_none()
    if not row:
        row = GlobalSetting(key=key, is_secret=True)
        db.add(row)
    else:
        row.is_secret = True

    if value is not None:
        row.value_encrypted = encrypt_credentials({"v": value})
        row.value_plain = None
    else:
        row.value_encrypted = None
        row.value_plain = None


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



async def get_active_llm_config(db: AsyncSession) -> LLMConfig:
    """Return the LLMConfig for the currently selected provider.

    llm.provider = "custom"  → local llamacpp03 endpoint (default)
    llm.provider = "openai-codex" → OpenAI Codex via stored OAuth token
    """
    s = await get_all_settings(db)
    provider = s.get("llm.provider") or "custom"

    if provider == "openai-codex":
        from app.api.oauth_providers import get_codex_access_token
        token = await get_codex_access_token(db)
        if token:
            from app.api.oauth_providers import CODEX_BASE_URL
            model = s.get("llm.codex_model") or "gpt-4o"
            return LLMConfig(
                base_url=CODEX_BASE_URL,
                model=model,
                api_key=token,
                timeout_seconds=int(s.get("llm.codex_timeout_seconds") or 60),
                api_mode="codex_responses",
                thinking_mode=s.get("llm.thinking_mode", "false") == "true",
            )

    if provider == "claude-oauth":
        # OAuth access token (sk-ant-oat...) stored in DB via the browser PKCE flow.
        # Passed as api_key with anthropic_messages mode — Hermes recognises the
        # sk-ant- prefix as an OAuth Bearer token (not an x-api-key).
        from app.api.oauth_providers import get_claude_access_token
        token = await get_claude_access_token(db)
        if token:
            return LLMConfig(
                base_url="",
                model=s.get("llm.claude_model") or "claude-opus-4-8",
                api_key=token,
                api_mode="anthropic_messages",
                thinking_mode=s.get("llm.thinking_mode", "false") == "true",
            )

    return await get_llm_config(db)


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


async def get_prometheus_config(db: AsyncSession) -> PrometheusConfig:
    s = await get_all_settings(db)
    return PrometheusConfig(
        query_timeout=int(s.get("prometheus.query_timeout") or 30),
        default_step=s.get("prometheus.default_step") or "1m",
        default_hours=int(s.get("prometheus.default_hours") or 4),
    )


def _csv_list(s: dict, key: str) -> list[str]:
    return [v.strip() for v in (s.get(key) or "").split(",") if v.strip()]


async def get_agent_config(db: AsyncSession) -> AgentConfig:
    s = await get_all_settings(db)
    return AgentConfig(
        interval_minutes=int(s.get("agent.interval_minutes") or 10),
        aggregation_interval_minutes=int(s.get("agent.aggregation_interval_minutes") or 2),
        auto_jira=s.get("agent.auto_jira", "false") == "true",
        auto_enrich=s.get("agent.auto_enrich", "true") == "true",
        rag_enabled=s.get("agent.rag_enabled", "true") == "true",
        jira_severity_threshold=s.get("agent.jira_severity_threshold") or "critical",
        checkmk_locations=_csv_list(s, "agent.checkmk_locations"),
        workflow_web_search=s.get("workflow.web_search", "true") == "true",
        scoring_enabled=s.get("agent.scoring_enabled", "true") == "true",
        enrich_score_threshold=int(s.get("agent.enrich_score_threshold") or 80),
        max_alerts_for_llm=int(s.get("agent.max_alerts_for_llm") or 30),
        flap_window_minutes=int(s.get("agent.flap_window_minutes") or 30),
        flap_threshold=int(s.get("agent.flap_threshold") or 3),
        score_learning_enabled=s.get("agent.score_learning_enabled", "true") == "true",
        score_delta_decay_days=int(s.get("agent.score_delta_decay_days") or 7),
        worklist_interval_minutes=int(s.get("agent.worklist_interval_minutes") or 15),
        worklist_size=int(s.get("agent.worklist_size") or 15),
        generative_interval_minutes=int(s.get("agent.generative_interval_minutes") or 15),
    )
