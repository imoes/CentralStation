"""Pydantic output models for the AI agent."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


class Finding(BaseModel):
    source: str
    severity: Literal["critical", "high", "medium", "low", "info"]
    title: str
    description: str
    host: str | None = None
    affected_service: str | None = None
    location: str | None = None


class Recommendation(BaseModel):
    priority: Literal["critical", "high", "medium", "low"]
    action: str
    rationale: str
    jira_title: str | None = None
    references: list[str] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    severity_summary: Literal["critical", "high", "medium", "low", "info", "none"] = "none"
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
