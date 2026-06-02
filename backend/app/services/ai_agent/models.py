"""Pydantic output models for the AI agent."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, field_validator

# Mapping for non-standard severity values the LLM sometimes produces.
# e.g. syslog "warning" → "medium", "error" → "high", "notice" → "low"
_SEV_ALIAS: dict[str, str] = {
    "warning": "medium", "warn": "medium",
    "error": "high",
    "notice": "low",
    "debug": "info",
    "fatal": "critical",
    "severe": "high",
}


class Finding(BaseModel):
    source: str
    severity: Literal["critical", "high", "medium", "low", "info"] = "medium"
    title: str
    description: str
    host: str | None = None
    affected_service: str | None = None
    location: str | None = None

    @field_validator("severity", mode="before")
    @classmethod
    def normalise_severity(cls, v: str) -> str:
        return _SEV_ALIAS.get(str(v).lower(), str(v).lower())


class Recommendation(BaseModel):
    priority: Literal["critical", "high", "medium", "low"] = "medium"
    action: str
    rationale: str
    jira_title: str | None = None
    references: list[str] = Field(default_factory=list)

    @field_validator("priority", mode="before")
    @classmethod
    def normalise_priority(cls, v: str) -> str:
        mapped = _SEV_ALIAS.get(str(v).lower(), str(v).lower())
        # priority has no "info" — clamp to "low"
        return mapped if mapped in ("critical", "high", "medium", "low") else "low"


class AnalysisResult(BaseModel):
    severity_summary: Literal["critical", "high", "medium", "low", "info", "none"] = "none"

    @field_validator("severity_summary", mode="before")
    @classmethod
    def normalise_summary(cls, v: str) -> str:
        mapped = _SEV_ALIAS.get(str(v).lower(), str(v).lower())
        return mapped if mapped in ("critical", "high", "medium", "low", "info", "none") else "none"
    findings: list[Finding] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    rag_queries_used: list[dict] = Field(default_factory=list)
    token_usage: dict = Field(default_factory=dict)
    jira_tickets_created: list[str] = Field(default_factory=list)
    error: str | None = None


class AgentState(BaseModel):
    """LangGraph state object passed through all nodes."""
    raw_alerts: list[dict] = Field(default_factory=list)
    enriched_alerts: list[dict] = Field(default_factory=list)
    rag_context: list[dict] = Field(default_factory=list)
    analysis: AnalysisResult | None = None
    jira_project: str = "IMIT"
    auto_jira: bool = True
    jira_threshold: str = "critical"
