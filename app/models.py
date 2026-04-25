"""
ORM models — one class per spec §11 table.

List fields (e.g. competitors_mentioned, cited_sources) are stored as JSON
text in SQLite. The ORM presents them as Python lists.
"""

from __future__ import annotations

from datetime import datetime, date
from typing import Optional

from sqlalchemy import String, Integer, Float, Boolean, Date, DateTime, Text, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Setting(Base):
    __tablename__ = "settings"

    setting_key: Mapped[str] = mapped_column(String, primary_key=True)
    setting_value: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")


class Prompt(Base):
    __tablename__ = "prompts"

    prompt_id: Mapped[str] = mapped_column(String, primary_key=True)
    prompt_text: Mapped[str] = mapped_column(Text)
    topic_cluster: Mapped[str] = mapped_column(String, default="")
    funnel_stage: Mapped[str] = mapped_column(String, default="awareness")
    country: Mapped[str] = mapped_column(String, default="")
    language: Mapped[str] = mapped_column(String, default="en")
    business_priority: Mapped[int] = mapped_column(Integer, default=3)
    target_brand: Mapped[str] = mapped_column(String, default="OCSiAl")
    target_product: Mapped[str] = mapped_column(String, default="TUBALL")
    target_url: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="active")

    # Frontend display fields (snapshot from most recent ai_result)
    platform: Mapped[str] = mapped_column(String, default="ChatGPT")
    priority: Mapped[str] = mapped_column(String, default="Medium")  # High/Medium/Low
    brand_mentioned: Mapped[bool] = mapped_column(Boolean, default=False)
    product_mentioned: Mapped[bool] = mapped_column(Boolean, default=False)
    domain_cited: Mapped[bool] = mapped_column(Boolean, default=False)
    competitors_mentioned: Mapped[list] = mapped_column(JSON, default=list)
    cited_sources: Mapped[list] = mapped_column(JSON, default=list)
    answer_quality_score: Mapped[int] = mapped_column(Integer, default=0)
    monitor_status: Mapped[str] = mapped_column(String, default="Unchecked")  # Good/Gap/Risk/Needs review/Unchecked
    related_url_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    ai_results: Mapped[list["AiResult"]] = relationship(back_populates="prompt", cascade="all, delete-orphan")


class AiResult(Base):
    __tablename__ = "ai_results"

    result_id: Mapped[str] = mapped_column(String, primary_key=True)
    prompt_id: Mapped[str] = mapped_column(ForeignKey("prompts.prompt_id"), index=True)
    platform: Mapped[str] = mapped_column(String, default="")
    date_checked: Mapped[date] = mapped_column(Date, default=date.today)
    answer_text: Mapped[str] = mapped_column(Text, default="")
    brand_mentioned: Mapped[bool] = mapped_column(Boolean, default=False)
    product_mentioned: Mapped[bool] = mapped_column(Boolean, default=False)
    domain_cited: Mapped[bool] = mapped_column(Boolean, default=False)
    competitors_mentioned: Mapped[list] = mapped_column(JSON, default=list)
    cited_sources: Mapped[list] = mapped_column(JSON, default=list)
    entities: Mapped[list] = mapped_column(JSON, default=list)  # extracted entities for drawer
    answer_quality_score: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")

    prompt: Mapped["Prompt"] = relationship(back_populates="ai_results")


class Source(Base):
    __tablename__ = "sources"

    source_id: Mapped[str] = mapped_column(String, primary_key=True)
    source_url: Mapped[str] = mapped_column(String)
    domain: Mapped[str] = mapped_column(String, default="")
    title: Mapped[str] = mapped_column(String, default="")
    source_type: Mapped[str] = mapped_column(String, default="Unknown")
    cited_by_prompts: Mapped[list] = mapped_column(JSON, default=list)
    mentions_brand: Mapped[bool] = mapped_column(Boolean, default=False)
    mentions_product: Mapped[bool] = mapped_column(Boolean, default=False)
    mentions_competitor: Mapped[bool] = mapped_column(Boolean, default=False)
    links_to_owned_domain: Mapped[bool] = mapped_column(Boolean, default=False)
    source_influence_score: Mapped[int] = mapped_column(Integer, default=0)
    outreach_status: Mapped[str] = mapped_column(String, default="Not reviewed")
    recommended_action: Mapped[str] = mapped_column(Text, default="")
    updated: Mapped[str] = mapped_column(String, default="")  # human-friendly text for UI


class MarketContext(Base):
    __tablename__ = "market_context"

    context_id: Mapped[str] = mapped_column(String, primary_key=True)
    batch_id: Mapped[str] = mapped_column(String, index=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    brand_summary: Mapped[str] = mapped_column(Text, default="")
    competitor_candidates: Mapped[list] = mapped_column(JSON, default=list)
    application_areas: Mapped[list] = mapped_column(JSON, default=list)
    source_citations: Mapped[list] = mapped_column(JSON, default=list)
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict)


class PromptDraft(Base):
    __tablename__ = "prompt_drafts"

    draft_id: Mapped[str] = mapped_column(String, primary_key=True)
    batch_id: Mapped[str] = mapped_column(String, index=True)
    query_text: Mapped[str] = mapped_column(Text)
    topic_cluster: Mapped[str] = mapped_column(String, default="")
    intent_type: Mapped[str] = mapped_column(String, default="")
    business_priority: Mapped[int] = mapped_column(Integer, default=3)
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String, default="draft")  # draft/imported/skipped
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class Url(Base):
    __tablename__ = "urls"

    url_id: Mapped[str] = mapped_column(String, primary_key=True)
    url: Mapped[str] = mapped_column(String)
    domain: Mapped[str] = mapped_column(String, default="")
    page_type: Mapped[str] = mapped_column(String, default="Other")
    topic_cluster: Mapped[str] = mapped_column(String, default="")
    target_prompts: Mapped[list] = mapped_column(JSON, default=list)
    indexable: Mapped[bool] = mapped_column(Boolean, default=True)
    canonical: Mapped[str] = mapped_column(String, default="")
    title: Mapped[str] = mapped_column(String, default="")
    h1: Mapped[str] = mapped_column(String, default="")
    has_direct_answer: Mapped[bool] = mapped_column(Boolean, default=False)
    has_comparison_table: Mapped[bool] = mapped_column(Boolean, default=False)
    has_faq: Mapped[bool] = mapped_column(Boolean, default=False)
    has_citations: Mapped[bool] = mapped_column(Boolean, default=False)
    has_internal_links: Mapped[bool] = mapped_column(Boolean, default=False)
    has_cta: Mapped[bool] = mapped_column(Boolean, default=False)
    has_schema: Mapped[bool] = mapped_column(Boolean, default=False)
    page_readiness_score: Mapped[int] = mapped_column(Integer, default=0)
    recommended_action: Mapped[str] = mapped_column(Text, default="")

    seo_metrics: Mapped[list["SeoMetric"]] = relationship(back_populates="url_ref", cascade="all, delete-orphan")


class SeoMetric(Base):
    __tablename__ = "seo_metrics"

    metric_id: Mapped[str] = mapped_column(String, primary_key=True)
    date: Mapped[date] = mapped_column(Date, default=date.today)
    url_id: Mapped[str] = mapped_column(ForeignKey("urls.url_id"), index=True)
    query: Mapped[str] = mapped_column(String, default="")
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    ctr: Mapped[float] = mapped_column(Float, default=0.0)
    avg_position: Mapped[float] = mapped_column(Float, default=0.0)
    sessions: Mapped[int] = mapped_column(Integer, default=0)
    conversions: Mapped[int] = mapped_column(Integer, default=0)

    url_ref: Mapped["Url"] = relationship(back_populates="seo_metrics")


class Recommendation(Base):
    __tablename__ = "recommendations"

    recommendation_id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String)
    type: Mapped[str] = mapped_column(String, default="Update existing page")
    diagnosis: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    recommended_actions: Mapped[list] = mapped_column(JSON, default=list)
    acceptance_criteria: Mapped[list] = mapped_column(JSON, default=list)
    related_prompt_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    related_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    related_source_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    priority_score: Mapped[int] = mapped_column(Integer, default=50)
    confidence_score: Mapped[int] = mapped_column(Integer, default=50)
    expected_impact: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String, default="New")  # New/Approved/Rejected/Task created
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Score breakdown for UI (ring + bars)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)


class UsageLog(Base):
    __tablename__ = "usage_log"

    log_id: Mapped[str] = mapped_column(String, primary_key=True)
    run_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    model: Mapped[str] = mapped_column(String, default="")
    prompt_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)


class Task(Base):
    __tablename__ = "tasks"

    task_id: Mapped[str] = mapped_column(String, primary_key=True)
    recommendation_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    task_title: Mapped[str] = mapped_column(String)
    task_type: Mapped[str] = mapped_column(String, default="Update existing page")
    owner: Mapped[str] = mapped_column(String, default="")
    owner_initials: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="Recommended")
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    priority: Mapped[str] = mapped_column(String, default="Medium")
    acceptance_criteria: Mapped[list] = mapped_column(JSON, default=list)
    expected_impact: Mapped[str] = mapped_column(Text, default="")
    actual_impact: Mapped[str] = mapped_column(Text, default="")
    review_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    related_prompt_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    related_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    related_source_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    blocked_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
