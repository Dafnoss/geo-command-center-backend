"""
Pydantic request/response schemas.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional, List, Literal

from pydantic import BaseModel, ConfigDict


# ---------------- Shared config ----------------
class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------- Prompt ----------------
class PromptOut(ORMModel):
    prompt_id: str
    prompt_text: str
    topic_cluster: str
    funnel_stage: str
    country: str
    language: str
    business_priority: int
    target_brand: str
    target_product: str
    target_url: str
    status: str
    platform: str
    priority: str
    brand_mentioned: bool
    product_mentioned: bool
    domain_cited: bool
    competitors_mentioned: List[str]
    cited_sources: List[str]
    answer_quality_score: int
    monitor_status: str
    related_url_id: Optional[str]


class PromptCreate(BaseModel):
    prompt_id: Optional[str] = None
    prompt_text: str
    topic_cluster: str = ""
    country: str = ""
    language: str = "en"
    business_priority: int = 3
    target_brand: str = "OCSiAl"
    target_product: str = "TUBALL"
    target_url: str = ""
    priority: str = "Medium"


class PromptUpdate(BaseModel):
    prompt_text: Optional[str] = None
    topic_cluster: Optional[str] = None
    business_priority: Optional[int] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    monitor_status: Optional[str] = None


# ---------------- AI Result ----------------
class AiResultOut(ORMModel):
    result_id: str
    prompt_id: str
    platform: str
    date_checked: date
    answer_text: str
    brand_mentioned: bool
    product_mentioned: bool
    domain_cited: bool
    competitors_mentioned: List[str]
    cited_sources: List[str]
    entities: List[dict]
    answer_quality_score: int
    notes: str


class AiResultCreate(BaseModel):
    result_id: Optional[str] = None
    prompt_id: str
    platform: str = "ChatGPT"
    answer_text: str = ""
    brand_mentioned: bool = False
    product_mentioned: bool = False
    domain_cited: bool = False
    competitors_mentioned: List[str] = []
    cited_sources: List[str] = []
    entities: List[dict] = []
    answer_quality_score: int = 0
    notes: str = ""


# ---------------- Source ----------------
class SourceOut(ORMModel):
    source_id: str
    source_url: str
    domain: str
    title: str
    source_type: str
    cited_by_prompts: List[str]
    mentions_brand: bool
    mentions_product: bool
    mentions_competitor: bool
    links_to_owned_domain: bool
    source_influence_score: int
    outreach_status: str
    recommended_action: str
    updated: str


class SourceUpdate(BaseModel):
    outreach_status: Optional[str] = None
    recommended_action: Optional[str] = None


# ---------------- URL ----------------
class UrlOut(ORMModel):
    url_id: str
    url: str
    domain: str
    page_type: str
    topic_cluster: str
    target_prompts: List[str]
    indexable: bool
    canonical: str
    title: str
    h1: str
    has_direct_answer: bool
    has_comparison_table: bool
    has_faq: bool
    has_citations: bool
    has_internal_links: bool
    has_cta: bool
    has_schema: bool
    page_readiness_score: int
    recommended_action: str


class UrlUpdate(BaseModel):
    has_direct_answer: Optional[bool] = None
    has_comparison_table: Optional[bool] = None
    has_faq: Optional[bool] = None
    has_citations: Optional[bool] = None
    has_internal_links: Optional[bool] = None
    has_cta: Optional[bool] = None
    has_schema: Optional[bool] = None
    indexable: Optional[bool] = None


# ---------------- SEO metric ----------------
class SeoMetricOut(ORMModel):
    metric_id: str
    date: date
    url_id: str
    query: str
    clicks: int
    impressions: int
    ctr: float
    avg_position: float
    sessions: int
    conversions: int


# ---------------- Google integrations ----------------
class GoogleConnectorStatus(BaseModel):
    configured: bool
    connected: bool
    status: str
    account_label: str = ""
    scopes: List[str] = []
    last_sync_at: Optional[datetime] = None
    search_console_sites: List[str] = []
    ga4_property_id: str = ""
    ga4_properties: List[dict] = []
    search_rows: int = 0
    analytics_rows: int = 0


class GoogleAuthUrlOut(BaseModel):
    authorization_url: str


class GoogleSyncOut(BaseModel):
    ok: bool
    search_console_sites: List[str] = []
    search_rows: int = 0
    analytics_rows: int = 0
    warnings: List[str] = []


class GoogleSearchMetricOut(ORMModel):
    metric_id: str
    site_url: str
    date_start: date
    date_end: date
    query: str
    page: str
    clicks: int
    impressions: int
    ctr: float
    avg_position: float


class GoogleAnalyticsMetricOut(ORMModel):
    metric_id: str
    property_id: str
    date_start: date
    date_end: date
    page_path: str
    page_title: str
    active_users: int
    sessions: int
    conversions: float


# ---------------- Recommendation ----------------
class RecommendationOut(ORMModel):
    recommendation_id: str
    title: str
    type: str
    diagnosis: str
    evidence: List[str]
    recommended_actions: List[str]
    acceptance_criteria: List[str]
    related_prompt_id: Optional[str]
    related_url: Optional[str]
    related_source_id: Optional[str]
    priority_score: int
    confidence_score: int
    expected_impact: str
    status: str
    created_at: datetime
    score_breakdown: dict


class RecommendationCreate(BaseModel):
    title: str
    type: str
    diagnosis: str
    evidence: List[str]
    recommended_actions: List[str] = []
    acceptance_criteria: List[str] = []
    related_prompt_id: Optional[str] = None
    related_url: Optional[str] = None
    related_source_id: Optional[str] = None
    priority_score: int = 50
    confidence_score: int = 50
    expected_impact: str = ""


class RecommendationStatusUpdate(BaseModel):
    status: Literal["New", "Accepted", "In Progress", "Done", "Rejected", "Stale", "Approved", "Task created"]
    notes: str = ""
    affected_page_url: str = ""
    expected_prompt_ids: List[str] = []


class GenerateRequest(BaseModel):
    prompt_id: Optional[str] = None
    source_id: Optional[str] = None
    url_id: Optional[str] = None
    limit: int = 1  # how many recommendations to generate


class RecommendationSummaryOut(BaseModel):
    total_active: int
    high_priority: int
    gap_driven: int
    risk_driven: int
    cluster_level: int
    prompt_level: int
    approved_or_done: int
    rejected: int


class RecommendationProcessOut(BaseModel):
    considered_prompts: int
    clusters_processed: int
    created: int
    updated: int
    rejected_stale: int
    recommendations: List[RecommendationOut]


class ClusterEvidenceOut(BaseModel):
    cluster: str
    prompt_count: int
    run_count: int
    good_count: int
    risk_count: int
    gap_count: int
    coverage_rate: int
    brand_mention_rate: int
    owned_citation_rate: int
    competitor_pressure_rate: int
    top_competitors: List[dict] = []
    top_external_sources: List[dict] = []
    top_owned_sources: List[dict] = []
    gsc_impressions: int = 0
    gsc_clicks: int = 0
    gsc_avg_position: float = 0
    ga4_sessions: int = 0
    ga4_users: int = 0
    top_gsc_queries: List[dict] = []
    top_ga4_pages: List[dict] = []
    best_existing_page: Optional[dict] = None
    linked_prompt_ids: List[str] = []
    linked_prompt_texts: List[str] = []
    priority_components: dict
    opportunity_type: str
    opportunity_title: str


# ---------------- Market intelligence ----------------
class CompetitorCandidate(BaseModel):
    name: str
    domain: str = ""
    reason: str = ""


class MarketContextOut(ORMModel):
    context_id: str
    batch_id: str
    generated_at: datetime
    brand_summary: str
    competitor_candidates: List[dict]
    application_areas: List[str]
    source_citations: List[dict]
    raw_payload: dict


class PromptDraftOut(ORMModel):
    draft_id: str
    batch_id: str
    query_text: str
    topic_cluster: str
    intent_type: str
    business_priority: int
    reason: str
    status: str
    created_at: datetime


class DraftBatchOut(BaseModel):
    batch_id: str
    context: Optional[MarketContextOut]
    drafts: List[PromptDraftOut]
    competitor_candidates: List[CompetitorCandidate] = []


class GenerateDraftsRequest(BaseModel):
    count: int = 25


class ApproveDraftsRequest(BaseModel):
    draft_ids: List[str]


class ApproveDraftsOut(BaseModel):
    batch_id: str
    imported: List[PromptOut]
    skipped: List[str]


class ApproveCompetitorsRequest(BaseModel):
    competitors: List[CompetitorCandidate]


class ApproveCompetitorsOut(BaseModel):
    competitors: str


# ---------------- Task ----------------
class TaskOut(ORMModel):
    task_id: str
    recommendation_id: Optional[str]
    task_title: str
    task_type: str
    owner: str
    owner_initials: str
    status: str
    due_date: Optional[date]
    priority: str
    acceptance_criteria: List[str]
    expected_impact: str
    actual_impact: str
    review_date: Optional[date]
    related_prompt_id: Optional[str]
    related_url: Optional[str]
    related_source_id: Optional[str]
    blocked_reason: str
    created_at: datetime


class TaskCreate(BaseModel):
    task_title: str
    task_type: str = "Update existing page"
    owner: str = ""
    priority: str = "Medium"
    due_date: Optional[date] = None
    acceptance_criteria: List[str] = []
    expected_impact: str = ""
    recommendation_id: Optional[str] = None
    related_prompt_id: Optional[str] = None
    related_url: Optional[str] = None
    related_source_id: Optional[str] = None


class TaskUpdate(BaseModel):
    status: Optional[str] = None
    owner: Optional[str] = None
    priority: Optional[str] = None
    due_date: Optional[date] = None
    actual_impact: Optional[str] = None
    blocked_reason: Optional[str] = None


# ---------------- Dashboard ----------------
class KpiDelta(BaseModel):
    value: int
    suffix: str = ""
    delta: str = ""
    delta_dir: str = "flat"
    help: str = ""
    trend: List[int] = []


class ClusterStat(BaseModel):
    name: str
    avg: int


class TaskStatusSegment(BaseModel):
    label: str
    value: int
    color: str


class DashboardOut(BaseModel):
    top_issue: str
    imported_prompts: KpiDelta
    run_coverage: KpiDelta
    ai_visibility: KpiDelta
    domain_citation: KpiDelta
    competitor_pressure: KpiDelta
    high_priority_tasks: KpiDelta
    cluster_stats: List[ClusterStat]
    task_status_segments: List[TaskStatusSegment]
    total_tasks: int
    completed_tasks: int
    avg_time_to_done_days: float
    top_gaps: List[PromptOut]
    top_recommendations: List[RecommendationOut]
    source_opportunities: List[SourceOut]


# ---------------- Settings ----------------
class SettingOut(ORMModel):
    setting_key: str
    setting_value: str
    notes: str


class SettingUpdate(BaseModel):
    setting_value: str
