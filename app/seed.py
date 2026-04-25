"""
Seed the database with the fixture data used by the React prototype.

- Creates all tables (idempotent).
- Populates rows only when the corresponding table is empty, so it's safe
  to run every time the backend starts.

Source of truth: data.js in the project root. This file ports those
fixtures to Python so the backend can run without a JS runtime.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Iterable
import uuid

from sqlalchemy.orm import Session

from app.database import Base, engine, SessionLocal
from app import models
from app.scoring import page_readiness_score


# ----------------------------- helpers -----------------------------
def _parse_date(s: str | None):
    if not s:
        return None
    # data.js uses e.g. "May 05, 2026"
    try:
        return datetime.strptime(s, "%b %d, %Y").date()
    except Exception:
        return None


def _priority_from_business(n: int) -> str:
    if n >= 5:
        return "High"
    if n >= 3:
        return "Medium"
    return "Low"


def _rec_status_map(s: str) -> str:
    # UI had "In progress" which is really a Task-exists state.
    mapping = {
        "New": "New",
        "Approved": "Approved",
        "Rejected": "Rejected",
        "In progress": "Task created",
    }
    return mapping.get(s, "New")


def _initials(name: str) -> str:
    parts = [p for p in name.split() if p]
    return "".join(p[0] for p in parts[:2]).upper()


# ----------------------------- fixture data -----------------------------
PROMPTS = [
    {"id": "P001", "text": "Graphene nanotubes vs carbon black", "cluster": "Comparisons", "platform": "ChatGPT", "country": "US", "lang": "EN", "priority": 5, "brand": False, "product": False, "cited": False, "comps": ["Cabot", "Carbon black (generic)"], "quality": 2, "status": "Gap", "url": None},
    {"id": "P002", "text": "Best conductive additive for epoxy", "cluster": "Coatings & epoxies", "platform": "Perplexity", "country": "DE", "lang": "EN", "priority": 5, "brand": True, "product": True, "cited": True, "comps": ["Cabot", "Imerys"], "quality": 4, "status": "Good", "url": "tuball.com/epoxy"},
    {"id": "P003", "text": "SWCNT for lithium-ion batteries", "cluster": "EV / Li-ion batteries", "platform": "ChatGPT", "country": "US", "lang": "EN", "priority": 5, "brand": True, "product": False, "cited": False, "comps": ["LG Chem CNT", "Cnano"], "quality": 3, "status": "Risk", "url": "ocsial.com/li-ion"},
    {"id": "P004", "text": "Are carbon nanotubes safe?", "cluster": "Safety & regulatory", "platform": "Google AI", "country": "US", "lang": "EN", "priority": 4, "brand": False, "product": False, "cited": False, "comps": [], "quality": 3, "status": "Gap", "url": None},
    {"id": "P005", "text": "Best suppliers of single-wall carbon nanotubes", "cluster": "Supplier landscape", "platform": "ChatGPT", "country": "US", "lang": "EN", "priority": 5, "brand": True, "product": False, "cited": False, "comps": ["LG Chem CNT", "Cnano", "Nanocyl"], "quality": 3, "status": "Risk", "url": None},
    {"id": "P006", "text": "TUBALL vs carbon black", "cluster": "Brand / Product", "platform": "ChatGPT", "country": "EU", "lang": "EN", "priority": 5, "brand": True, "product": True, "cited": True, "comps": ["Cabot"], "quality": 4, "status": "Good", "url": "tuball.com"},
    {"id": "P007", "text": "OCSiAl graphene nanotubes", "cluster": "Brand / Product", "platform": "Perplexity", "country": "US", "lang": "EN", "priority": 4, "brand": True, "product": True, "cited": True, "comps": [], "quality": 5, "status": "Good", "url": "ocsial.com"},
    {"id": "P008", "text": "Graphene nanotubes for EV batteries", "cluster": "EV / Li-ion batteries", "platform": "ChatGPT", "country": "CN", "lang": "EN", "priority": 5, "brand": True, "product": True, "cited": False, "comps": ["LG Chem CNT"], "quality": 3, "status": "Risk", "url": "ocsial.com/ev"},
    {"id": "P009", "text": "REACH registered single-wall carbon nanotubes", "cluster": "Safety & regulatory", "platform": "Perplexity", "country": "EU", "lang": "EN", "priority": 4, "brand": True, "product": False, "cited": True, "comps": [], "quality": 4, "status": "Good", "url": "ocsial.com/hs"},
    {"id": "P010", "text": "Conductive additive for silicone", "cluster": "Elastomers & rubber", "platform": "ChatGPT", "country": "US", "lang": "EN", "priority": 3, "brand": False, "product": False, "cited": False, "comps": ["Cabot", "Imerys"], "quality": 3, "status": "Gap", "url": None},
    {"id": "P011", "text": "Carbon black alternative for conductive polymers", "cluster": "Plastics & composites", "platform": "Google AI", "country": "US", "lang": "EN", "priority": 4, "brand": False, "product": False, "cited": False, "comps": ["Cabot", "Arkema"], "quality": 3, "status": "Gap", "url": None},
    {"id": "P012", "text": "Anti-static additive for rubber", "cluster": "Elastomers & rubber", "platform": "ChatGPT", "country": "DE", "lang": "EN", "priority": 3, "brand": True, "product": False, "cited": False, "comps": ["Cabot", "Orion"], "quality": 3, "status": "Risk", "url": "tuball.com/rubber"},
    {"id": "P013", "text": "Conductive additive for non-black plastics", "cluster": "Plastics & composites", "platform": "Perplexity", "country": "US", "lang": "EN", "priority": 4, "brand": True, "product": True, "cited": False, "comps": ["Arkema"], "quality": 3, "status": "Risk", "url": "tuball.com/plastics"},
    {"id": "P014", "text": "Single-wall vs multi-wall carbon nanotubes", "cluster": "Comparisons", "platform": "ChatGPT", "country": "US", "lang": "EN", "priority": 4, "brand": False, "product": False, "cited": False, "comps": ["Nanocyl", "Showa Denko"], "quality": 3, "status": "Gap", "url": None},
    {"id": "P015", "text": "How to reduce conductive filler loading", "cluster": "Plastics & composites", "platform": "ChatGPT", "country": "US", "lang": "EN", "priority": 3, "brand": True, "product": True, "cited": True, "comps": [], "quality": 4, "status": "Good", "url": "tuball.com/loading"},
    {"id": "P016", "text": "TUBALL conductive additive", "cluster": "Brand / Product", "platform": "Google AI", "country": "US", "lang": "EN", "priority": 5, "brand": True, "product": True, "cited": True, "comps": [], "quality": 5, "status": "Good", "url": "tuball.com"},
    {"id": "P017", "text": "Conductive additive for SMC composites", "cluster": "Plastics & composites", "platform": "Perplexity", "country": "EU", "lang": "EN", "priority": 3, "brand": False, "product": False, "cited": False, "comps": ["Imerys"], "quality": 2, "status": "Gap", "url": None},
    {"id": "P018", "text": "SWCNT for epoxy coatings", "cluster": "Coatings & epoxies", "platform": "ChatGPT", "country": "US", "lang": "EN", "priority": 4, "brand": True, "product": True, "cited": True, "comps": [], "quality": 4, "status": "Good", "url": "tuball.com/epoxy"},
    {"id": "P019", "text": "Graphene nanotubes for tires", "cluster": "Elastomers & rubber", "platform": "ChatGPT", "country": "DE", "lang": "EN", "priority": 4, "brand": True, "product": True, "cited": False, "comps": ["Cabot"], "quality": 3, "status": "Risk", "url": "ocsial.com/tires"},
    {"id": "P020", "text": "Conductive coatings for tanks", "cluster": "Coatings & epoxies", "platform": "Google AI", "country": "US", "lang": "EN", "priority": 3, "brand": False, "product": False, "cited": False, "comps": ["Cabot"], "quality": 2, "status": "Gap", "url": None},
    {"id": "P021", "text": "Graphene nanotube concentrate for plastics", "cluster": "Plastics & composites", "platform": "Perplexity", "country": "US", "lang": "EN", "priority": 4, "brand": True, "product": True, "cited": False, "comps": ["Arkema"], "quality": 3, "status": "Risk", "url": "tuball.com/plastics"},
    {"id": "P022", "text": "Conductive additive for cleanroom gloves", "cluster": "Elastomers & rubber", "platform": "ChatGPT", "country": "US", "lang": "EN", "priority": 2, "brand": False, "product": False, "cited": False, "comps": [], "quality": 2, "status": "Gap", "url": None},
    {"id": "P023", "text": "Conductive additive for conveyor belts", "cluster": "Elastomers & rubber", "platform": "ChatGPT", "country": "US", "lang": "EN", "priority": 2, "brand": True, "product": False, "cited": False, "comps": ["Cabot"], "quality": 3, "status": "Review", "url": "tuball.com/rubber"},
    {"id": "P024", "text": "What are graphene nanotubes?", "cluster": "Brand / Product", "platform": "ChatGPT", "country": "US", "lang": "EN", "priority": 4, "brand": True, "product": True, "cited": True, "comps": [], "quality": 5, "status": "Good", "url": "ocsial.com"},
    {"id": "P025", "text": "Conductive additive for transparent coatings", "cluster": "Coatings & epoxies", "platform": "Perplexity", "country": "EU", "lang": "EN", "priority": 3, "brand": True, "product": False, "cited": False, "comps": [], "quality": 3, "status": "Review", "url": None},
]


AI_ANSWERS = [
    {
        "prompt_id": "P001",
        "platform": "ChatGPT",
        "date": date(2026, 4, 18),
        "answer": (
            "When comparing conductive additives, carbon black remains the default choice for most "
            "conductive polymer and elastomer applications due to its low cost and long track record. "
            "Typical conductive grades from suppliers like Cabot Corp and Orion Engineered Carbons are "
            "dosed at 15–25% by weight to reach the percolation threshold.\n\n"
            "Graphene-based materials — including multi-wall carbon nanotubes and, more recently, "
            "single-wall carbon nanotubes — can achieve the same conductivity at fractions of a percent "
            "loading, but are generally described as premium additives used where carbon black is "
            "incompatible (e.g. transparent coatings, white or colored parts, very low filler loading "
            "requirements).\n\n"
            "Key trade-offs most commonly discussed include cost per kg, processing compatibility, and "
            "achievable electrical resistivity. Carbon black is still cited as the industry-standard "
            "solution for the majority of conductive polymer applications."
        ),
        "brand_mentioned": False,
        "product_mentioned": False,
        "domain_cited": False,
        "competitors_mentioned": ["Cabot Corp", "Orion Engineered Carbons", "Carbon black (generic)", "Multi-wall CNT"],
        "cited_sources": ["chemanager-online.com", "plasticsinsight.com", "rubberworld.com"],
        "entities": [
            {"label": "Competitors", "items": ["Cabot Corp", "Orion Engineered Carbons", "Carbon black (generic)", "Multi-wall CNT"]},
        ],
        "answer_quality_score": 2,
    },
    {
        "prompt_id": "P003",
        "platform": "ChatGPT",
        "date": date(2026, 4, 22),
        "answer": (
            "Single-wall carbon nanotubes (SWCNTs) are increasingly used in lithium-ion battery "
            "electrodes, particularly on the cathode side, to reduce conductive additive loading while "
            "maintaining high electrical conductivity. Major Chinese and Korean battery manufacturers "
            "have adopted SWCNT-based conductive pastes from suppliers such as LG Chem and Cnano "
            "Technology.\n\n"
            "OCSiAl is also named as a producer of graphene nanotubes for battery applications, though "
            "the specific performance data most often cited in AI summaries comes from academic studies "
            "referencing LG Chem and Cnano products. Typical loading is reported as 0.05–0.2 wt% to "
            "replace 1–3 wt% of conventional carbon black."
        ),
        "brand_mentioned": True,
        "product_mentioned": False,
        "domain_cited": False,
        "competitors_mentioned": ["LG Chem CNT", "Cnano Technology"],
        "cited_sources": ["batteriesnews.com", "sciencedirect.com/li-ion-review", "greencarcongress.com"],
        "entities": [
            {"label": "Brand", "items": ["OCSiAl"]},
            {"label": "Competitors", "items": ["LG Chem CNT", "Cnano Technology"]},
        ],
        "answer_quality_score": 3,
    },
    {
        "prompt_id": "P005",
        "platform": "ChatGPT",
        "date": date(2026, 4, 20),
        "answer": (
            "Industrial-scale SWCNT production is concentrated among a small number of suppliers. The "
            "companies most frequently listed in industry reviews include:\n\n"
            "- LG Chem — large-scale CNT production in South Korea, primarily for EV battery cathode additives.\n"
            "- Cnano Technology — Chinese producer supplying battery and elastomer markets.\n"
            "- Nanocyl — Belgian manufacturer of multi-wall carbon nanotubes, widely used in composites.\n"
            "- OCSiAl — producer of graphene nanotubes (branded TUBALL), with manufacturing in Serbia "
            "and a reported 80 t/yr capacity.\n\n"
            "For single-wall products specifically, the dominant producers cited are LG Chem, Cnano, and OCSiAl."
        ),
        "brand_mentioned": True,
        "product_mentioned": False,
        "domain_cited": False,
        "competitors_mentioned": ["LG Chem CNT", "Cnano Technology", "Nanocyl"],
        "cited_sources": ["chemarc.com/cnt-suppliers", "marketsandmarkets.com/swcnt", "statista.com/cnt-producers"],
        "entities": [
            {"label": "Brand", "items": ["OCSiAl"]},
            {"label": "Competitors", "items": ["LG Chem CNT", "Cnano Technology", "Nanocyl"]},
        ],
        "answer_quality_score": 3,
    },
]


SOURCES = [
    {"id": "S01", "domain": "batteriesnews.com", "url": "batteriesnews.com/top-cnt-suppliers-2024", "title": "Top CNT suppliers shaping the EV battery market", "type": "Industry media", "cited_by": ["P003", "P008", "P005"], "brand": False, "product": False, "comp": True, "links_owned": False, "influence": 86, "outreach": "Outreach needed", "updated": "Feb 2024"},
    {"id": "S02", "domain": "sciencedirect.com", "url": "sciencedirect.com/article/swcnt-review-2023", "title": "Conductive additives in Li-ion cathodes: a 2023 review", "type": "Academic", "cited_by": ["P003", "P008", "P014"], "brand": True, "product": False, "comp": True, "links_owned": False, "influence": 94, "outreach": "Good source", "updated": "Nov 2023"},
    {"id": "S03", "domain": "chemarc.com", "url": "chemarc.com/cnt-suppliers", "title": "Directory of CNT manufacturers", "type": "Directory", "cited_by": ["P005", "P001"], "brand": True, "product": False, "comp": True, "links_owned": False, "influence": 68, "outreach": "Needs correction", "updated": "Jan 2022"},
    {"id": "S04", "domain": "rubberworld.com", "url": "rubberworld.com/conductive-fillers-tires", "title": "Conductive fillers in tire compounds", "type": "Industry media", "cited_by": ["P019", "P012", "P001"], "brand": False, "product": False, "comp": True, "links_owned": False, "influence": 72, "outreach": "Not reviewed", "updated": "Sep 2023"},
    {"id": "S05", "domain": "plasticsinsight.com", "url": "plasticsinsight.com/conductive-polymers", "title": "Conductive polymers: material choices", "type": "Industry media", "cited_by": ["P011", "P013", "P001"], "brand": False, "product": False, "comp": True, "links_owned": False, "influence": 70, "outreach": "Outreach needed", "updated": "Apr 2023"},
    {"id": "S06", "domain": "ocsial.com", "url": "ocsial.com", "title": "OCSiAl corporate site", "type": "Supplier list", "cited_by": ["P007", "P024", "P009"], "brand": True, "product": True, "comp": False, "links_owned": True, "influence": 82, "outreach": "Good source", "updated": "Apr 2026"},
    {"id": "S07", "domain": "tuball.com", "url": "tuball.com", "title": "TUBALL product site", "type": "Supplier list", "cited_by": ["P006", "P016", "P002"], "brand": True, "product": True, "comp": False, "links_owned": True, "influence": 78, "outreach": "Good source", "updated": "Apr 2026"},
    {"id": "S08", "domain": "echa.europa.eu", "url": "echa.europa.eu/substance-information/-/swcnt", "title": "REACH dossier — single-wall carbon nanotubes", "type": "Regulatory", "cited_by": ["P009", "P004"], "brand": True, "product": False, "comp": False, "links_owned": False, "influence": 88, "outreach": "Good source", "updated": "Jun 2024"},
    {"id": "S09", "domain": "statista.com", "url": "statista.com/cnt-producers", "title": "Global CNT producers by capacity", "type": "Industry media", "cited_by": ["P005", "P003"], "brand": True, "product": False, "comp": True, "links_owned": False, "influence": 74, "outreach": "Not reviewed", "updated": "Jan 2024"},
    {"id": "S10", "domain": "greencarcongress.com", "url": "greencarcongress.com/ev-cathode-additives", "title": "EV cathode additives: supplier landscape", "type": "Industry media", "cited_by": ["P003", "P008"], "brand": False, "product": False, "comp": True, "links_owned": False, "influence": 76, "outreach": "Outreach needed", "updated": "Oct 2023"},
    {"id": "S11", "domain": "reddit.com", "url": "reddit.com/r/batteries/swcnt-thread", "title": "r/batteries — are SWCNTs worth it?", "type": "Forum", "cited_by": ["P003"], "brand": False, "product": False, "comp": True, "links_owned": False, "influence": 42, "outreach": "Rejected", "updated": "Mar 2024"},
    {"id": "S12", "domain": "marketsandmarkets.com", "url": "marketsandmarkets.com/swcnt-market", "title": "SWCNT market report 2024", "type": "Industry media", "cited_by": ["P005"], "brand": True, "product": False, "comp": True, "links_owned": False, "influence": 80, "outreach": "Outreach sent", "updated": "Feb 2024"},
]


URLS = [
    {"id": "U01", "url": "ocsial.com/", "type": "Homepage", "cluster": "Brand / Product", "prompts": ["P007", "P024"], "indexable": True, "clicks": 3820, "impressions": 68400, "ctr": 5.6, "pos": 3.4, "sessions": 4120, "conv": 94, "checks": {"answer": True, "table": False, "faq": True, "cite": True, "links": True, "cta": True, "schema": True, "index": True}},
    {"id": "U02", "url": "tuball.com/", "type": "Homepage", "cluster": "Brand / Product", "prompts": ["P006", "P016"], "indexable": True, "clicks": 2160, "impressions": 41200, "ctr": 5.2, "pos": 3.9, "sessions": 2240, "conv": 61, "checks": {"answer": True, "table": False, "faq": False, "cite": True, "links": True, "cta": True, "schema": True, "index": True}},
    {"id": "U03", "url": "tuball.com/epoxy", "type": "Application page", "cluster": "Coatings & epoxies", "prompts": ["P002", "P018"], "indexable": True, "clicks": 840, "impressions": 24600, "ctr": 3.4, "pos": 6.1, "sessions": 920, "conv": 38, "checks": {"answer": True, "table": True, "faq": True, "cite": True, "links": True, "cta": True, "schema": True, "index": True}},
    {"id": "U04", "url": "ocsial.com/li-ion", "type": "Application page", "cluster": "EV / Li-ion batteries", "prompts": ["P003"], "indexable": True, "clicks": 1240, "impressions": 38900, "ctr": 3.2, "pos": 7.8, "sessions": 1410, "conv": 24, "checks": {"answer": True, "table": False, "faq": False, "cite": False, "links": True, "cta": True, "schema": True, "index": True}},
    {"id": "U05", "url": "ocsial.com/ev", "type": "Application page", "cluster": "EV / Li-ion batteries", "prompts": ["P008"], "indexable": True, "clicks": 680, "impressions": 21400, "ctr": 3.2, "pos": 8.4, "sessions": 740, "conv": 12, "checks": {"answer": False, "table": False, "faq": False, "cite": False, "links": True, "cta": True, "schema": True, "index": True}},
    {"id": "U06", "url": "ocsial.com/hs", "type": "Safety/regulatory page", "cluster": "Safety & regulatory", "prompts": ["P009", "P004"], "indexable": True, "clicks": 310, "impressions": 9800, "ctr": 3.2, "pos": 9.1, "sessions": 340, "conv": 5, "checks": {"answer": True, "table": False, "faq": False, "cite": True, "links": False, "cta": False, "schema": True, "index": True}},
    {"id": "U07", "url": "tuball.com/rubber", "type": "Application page", "cluster": "Elastomers & rubber", "prompts": ["P012", "P023"], "indexable": True, "clicks": 420, "impressions": 14200, "ctr": 3.0, "pos": 8.8, "sessions": 460, "conv": 11, "checks": {"answer": True, "table": False, "faq": False, "cite": True, "links": True, "cta": True, "schema": False, "index": True}},
    {"id": "U08", "url": "tuball.com/plastics", "type": "Application page", "cluster": "Plastics & composites", "prompts": ["P013", "P021"], "indexable": True, "clicks": 540, "impressions": 18700, "ctr": 2.9, "pos": 9.2, "sessions": 600, "conv": 14, "checks": {"answer": False, "table": False, "faq": False, "cite": True, "links": True, "cta": True, "schema": True, "index": True}},
    {"id": "U09", "url": "tuball.com/loading", "type": "Blog/article", "cluster": "Plastics & composites", "prompts": ["P015"], "indexable": True, "clicks": 260, "impressions": 7200, "ctr": 3.6, "pos": 7.1, "sessions": 290, "conv": 4, "checks": {"answer": True, "table": True, "faq": True, "cite": True, "links": True, "cta": False, "schema": True, "index": True}},
    {"id": "U10", "url": "ocsial.com/tires", "type": "Application page", "cluster": "Elastomers & rubber", "prompts": ["P019"], "indexable": False, "clicks": 92, "impressions": 4100, "ctr": 2.2, "pos": 11.8, "sessions": 104, "conv": 2, "checks": {"answer": False, "table": False, "faq": False, "cite": False, "links": True, "cta": True, "schema": False, "index": False}},
]


RECS = [
    {
        "id": "R01", "priority_score": 92, "confidence_score": 78, "type": "Create new page",
        "title": "Create comparison page: Graphene nanotubes vs carbon black",
        "diagnosis": "OCSiAl/TUBALL is absent from AI answers for the highest-priority comparison prompt. Generic sources and competitor additives are named instead. No owned page directly answers this query.",
        "evidence": [
            "Brand not mentioned in tested AI answers (4/4 platforms)",
            "Owned domains not cited in any result",
            "Cabot and carbon-black generic references appear in 6 of 6 cited sources",
            "No existing owned URL targets the 'vs carbon black' comparison intent",
            "Estimated SWCNT-comparison search demand: 2.1k impressions/mo across 18 related queries",
        ],
        "actions": [
            "Publish dedicated comparison page at tuball.com/vs-carbon-black",
            "Open with direct answer in first 100 words",
            "Include HTML comparison table: loading %, resistivity, cost-per-part, processing compatibility",
            "Add application-specific sections for polymers, coatings, batteries, and rubber",
            "Cite 3+ peer-reviewed studies and REACH dossier",
            "Add internal links to TUBALL application pages and sample-request CTA",
        ],
        "acceptance": [
            "Page is indexable and in sitemap",
            "Contains HTML comparison table",
            "At least 3 source citations",
            "Direct-answer section in first 100 words",
            "Internal links from at least 3 application pages",
            "Request-sample CTA visible above fold",
        ],
        "impact": "+15–25 pts on AI visibility for comparison cluster; est. 600+ monthly organic sessions within 90 days.",
        "promptId": "P001", "urlId": None, "sourceId": None, "status": "New", "created": "Apr 22, 2026",
    },
    {
        "id": "R02", "priority_score": 89, "confidence_score": 82, "type": "Create PR outreach brief",
        "title": "Pitch inclusion to batteriesnews.com supplier roundup",
        "diagnosis": "batteriesnews.com is cited in 3 high-priority EV/Li-ion prompts and names LG Chem, Cnano, and Nanocyl — but does not mention OCSiAl or TUBALL despite OCSiAl's demonstrated capacity for battery-grade SWCNT.",
        "evidence": [
            "Source cited in prompts P003, P008, P005 (all High priority)",
            "Source influence score: 86",
            "Competitors mentioned: LG Chem CNT, Cnano Technology",
            "OCSiAl not mentioned; tuball.com not linked",
            "Article last updated Feb 2024 — outreach window is open",
        ],
        "actions": [
            "Identify editorial contact at batteriesnews.com",
            "Prepare evidence package: REACH dossier, 80 t/yr capacity, 1,500+ customer claim",
            "Draft pitch angle: 'Missing from your SWCNT supplier list — OCSiAl'",
            "Offer exclusive data on SWCNT loading reduction in cathodes",
            "Follow up after 7 days",
        ],
        "acceptance": [
            "Contact identified and logged",
            "Outreach angle drafted and approved",
            "Evidence package prepared (1-pager PDF)",
            "First email sent",
            "Outreach status updated to 'Outreach sent'",
        ],
        "impact": "If inclusion secured: +7 pts citation rate on EV cluster, potentially influences 3+ downstream AI sources.",
        "promptId": "P003", "urlId": None, "sourceId": "S01", "status": "Approved", "created": "Apr 22, 2026",
    },
    {
        "id": "R03", "priority_score": 84, "confidence_score": 80, "type": "Update existing page",
        "title": "Add comparison table and FAQ to ocsial.com/ev",
        "diagnosis": "ocsial.com/ev ranks for high-value EV battery prompts but has low page readiness (44/100). Missing direct-answer section, comparison table, FAQ, and source citations — which are the elements AI engines prefer when selecting citations.",
        "evidence": [
            "Page appears for prompt P008 but is not cited in AI answer",
            "Readiness score: 44/100 (weak)",
            "Missing: direct answer, comparison table, FAQ, citations",
            "21.4k impressions/mo, but 3.2% CTR and position 8.4",
            "Competitor pages with comparison tables are cited in 2 of 3 overlapping AI results",
        ],
        "actions": [
            "Add 80-word direct answer at top of page",
            "Insert comparison table: TUBALL vs Cnano vs LG Chem (loading %, cost, safety)",
            "Add FAQ section with 6 questions from People-Also-Ask",
            "Cite REACH dossier and 2+ academic sources",
            "Add breadcrumbs and schema markup",
            "Request recrawl via Search Console",
        ],
        "acceptance": [
            "Readiness score ≥ 75",
            "Direct-answer section present",
            "HTML comparison table present",
            "FAQ section with 6+ Q&A",
            "3+ inline citations",
            "Submitted for recrawl",
        ],
        "impact": "Expected lift to position 4–5; +40% organic CTR; AI citation probability doubles based on pages with similar readiness gains.",
        "promptId": "P008", "urlId": "U05", "sourceId": None, "status": "New", "created": "Apr 21, 2026",
    },
    {
        "id": "R04", "priority_score": 81, "confidence_score": 72, "type": "Create PR outreach brief",
        "title": "Request correction on chemarc.com CNT directory",
        "diagnosis": "chemarc.com directory lists CNT suppliers and is cited by AI answers — but the OCSiAl entry is outdated (Jan 2022) and lacks the 80 t/yr capacity figure and TUBALL MATRIX product line.",
        "evidence": [
            "Directory cited in P001 and P005 (both High priority)",
            "Competitors (LG Chem, Cnano, Nanocyl) have up-to-date entries",
            "OCSiAl entry lists 2022 capacity and missing product variants",
            "Source influence: 68",
        ],
        "actions": [
            "Send correction request with updated company data",
            "Provide press-release link with 2026 capacity",
            "Request link to tuball.com as primary product site",
            "Offer interview with OCSiAl CTO",
        ],
        "acceptance": [
            "Correction request sent",
            "Entry updated with 2026 data",
            "Link to tuball.com added",
            "Outreach status updated",
        ],
        "impact": "+5 pts citation rate on Supplier cluster prompts.",
        "promptId": "P005", "urlId": None, "sourceId": "S03", "status": "New", "created": "Apr 22, 2026",
    },
    {
        "id": "R05", "priority_score": 68, "confidence_score": 75, "type": "Add FAQ section",
        "title": "Add FAQ to tuball.com/rubber (anti-static additive intent)",
        "diagnosis": "TUBALL rubber page ranks for 'anti-static additive for rubber' but AI answers cite Cabot and Orion — likely because those pages have FAQ sections addressing the exact wording of common safety and loading questions.",
        "evidence": [
            "Prompt P012 brand mentioned but not cited",
            "Page readiness: 62/100, no FAQ present",
            "Competitor pages have FAQ sections",
            "GSC shows 18 long-tail 'anti-static rubber' queries",
        ],
        "actions": [
            "Draft 8 FAQ entries covering loading %, cure compatibility, REACH status",
            "Implement FAQPage schema",
            "Internal link from /rubber to /safety pages",
        ],
        "acceptance": [
            "FAQ section live with 8+ entries",
            "FAQPage schema validates",
            "Readiness score ≥ 75",
        ],
        "impact": "Citation probability +10 pts for rubber cluster.",
        "promptId": "P012", "urlId": "U07", "sourceId": None, "status": "Approved", "created": "Apr 20, 2026",
    },
    {
        "id": "R06", "priority_score": 64, "confidence_score": 70, "type": "Create new page",
        "title": "Create safety hub: 'Are carbon nanotubes safe?'",
        "diagnosis": "Safety prompts show brand absent in AI answers — a high-risk category because negative or neutral answers can shape industry perception of SWCNTs generally.",
        "evidence": [
            "Prompt P004 'Are carbon nanotubes safe?' — brand missing",
            "AI answer references generic MSDS data, not OCSiAl's REACH dossier",
            "Owned /hs page exists but has low readiness (52/100)",
            "Regulatory cluster has only 2 owned pages",
        ],
        "actions": [
            "Create /safety-hub with three subsections: REACH, toxicology, handling",
            "Link to ECHA REACH dossier",
            "Publish handling video and PDF",
            "Cross-link from every application page",
        ],
        "acceptance": [
            "Hub page live and indexable",
            "Links to REACH dossier",
            "3+ citations",
            "FAQ section present",
        ],
        "impact": "Reduces safety-answer gap risk; improves trust signals for EV/battery prompts.",
        "promptId": "P004", "urlId": "U06", "sourceId": None, "status": "New", "created": "Apr 19, 2026",
    },
    {
        "id": "R07", "priority_score": 62, "confidence_score": 68, "type": "Fix indexing issue",
        "title": "Fix indexing issue on ocsial.com/tires",
        "diagnosis": "Tires application page is marked noindex despite being a high-opportunity cluster. Page gets 4.1k impressions/mo but cannot appear in AI citations.",
        "evidence": [
            "Indexable: false",
            "4,100 impressions/mo",
            "Readiness 38/100",
            "Prompt P019 brand mentioned but page not cited",
        ],
        "actions": [
            "Remove noindex",
            "Submit to Search Console",
            "Add to sitemap",
            "Verify rendering",
        ],
        "acceptance": [
            "Page is indexable",
            "URL inspected and indexed",
            "Appears in sitemap",
        ],
        "impact": "Unblocks AI citation for tires cluster.",
        "promptId": "P019", "urlId": "U10", "sourceId": None, "status": "In progress", "created": "Apr 17, 2026",
    },
    {
        "id": "R08", "priority_score": 58, "confidence_score": 72, "type": "Add comparison table",
        "title": "Add comparison table to tuball.com/plastics",
        "diagnosis": "Plastics application page mentions TUBALL but lacks structured comparison data that AI engines preferentially cite.",
        "evidence": [
            "Prompt P013, P021 brand mentioned but not cited",
            "Readiness 54/100",
            "No comparison table present",
            "Arkema Graphistrength mentioned as alternative",
        ],
        "actions": [
            "Add HTML comparison table: TUBALL vs Arkema vs carbon black",
            "Include typical loading, color impact, processing data",
        ],
        "acceptance": [
            "Comparison table live",
            "Readiness ≥ 70",
        ],
        "impact": "+8 pts citation rate for plastics cluster.",
        "promptId": "P013", "urlId": "U08", "sourceId": None, "status": "New", "created": "Apr 18, 2026",
    },
    {
        "id": "R09", "priority_score": 42, "confidence_score": 65, "type": "Add citations/sources",
        "title": "Add source citations to ocsial.com/li-ion",
        "diagnosis": "Li-ion page has decent readiness but no inline citations, limiting AI trust.",
        "evidence": [
            "Readiness 58/100, missing citations",
            "Prompt P003 cites academic sources, not owned domain",
        ],
        "actions": [
            "Add 5+ inline citations to peer-reviewed work",
            "Link to REACH dossier",
        ],
        "acceptance": [
            "5+ citations inline",
            "All sources footnoted",
        ],
        "impact": "+5 pts citation probability.",
        "promptId": "P003", "urlId": "U04", "sourceId": None, "status": "New", "created": "Apr 16, 2026",
    },
    {
        "id": "R10", "priority_score": 38, "confidence_score": 60, "type": "Improve title/meta",
        "title": "Rewrite meta titles for application pages",
        "diagnosis": "Application pages use generic titles that don't match AI prompt phrasing.",
        "evidence": [
            "5 application pages have titles like 'TUBALL Applications'",
            "Prompt phrasing uses 'conductive additive for [material]'",
        ],
        "actions": [
            "Rewrite 5 titles using prompt-aligned phrasing",
            "Update meta descriptions",
        ],
        "acceptance": [
            "5 titles updated",
            "Search Console recrawl requested",
        ],
        "impact": "Minor CTR improvement; better AI prompt matching.",
        "promptId": None, "urlId": None, "sourceId": None, "status": "New", "created": "Apr 15, 2026",
    },
]


TASKS = [
    {"id": "T01", "recId": "R02", "title": "Pitch inclusion to batteriesnews.com supplier roundup", "type": "PR / Outreach", "priority": "High", "owner": "Maria L.", "due": "May 05, 2026", "status": "Approved", "accept": 5, "impact": "+7 pts EV citation rate", "rel": {"prompt": "P003", "source": "S01"}},
    {"id": "T02", "recId": "R05", "title": "Add FAQ to tuball.com/rubber", "type": "Content", "priority": "Medium", "owner": "Alexey K.", "due": "May 12, 2026", "status": "Approved", "accept": 3, "impact": "+10 pts rubber citation", "rel": {"prompt": "P012", "url": "U07"}},
    {"id": "T03", "recId": "R07", "title": "Remove noindex on ocsial.com/tires", "type": "Technical SEO", "priority": "Medium", "owner": "Daniel R.", "due": "Apr 29, 2026", "status": "In progress", "accept": 3, "impact": "Unblocks tires citations", "rel": {"prompt": "P019", "url": "U10"}},
    {"id": "T04", "recId": None, "title": "Audit robots.txt and sitemap across all domains", "type": "Technical SEO", "priority": "High", "owner": "Daniel R.", "due": "Apr 30, 2026", "status": "In progress", "accept": 4, "impact": "Baseline indexability", "rel": {}},
    {"id": "T05", "recId": None, "title": "Crawl health: fix 4 broken internal links on tuball.com", "type": "Technical SEO", "priority": "Low", "owner": "Daniel R.", "due": "May 02, 2026", "status": "Blocked", "accept": 2, "impact": "Minor UX / crawl signal", "rel": {}, "blocked": "Waiting on CMS access"},
    {"id": "T06", "recId": None, "title": "Publish Q1 battery applications case study PDF", "type": "Content", "priority": "Medium", "owner": "Alexey K.", "due": "Apr 25, 2026", "status": "Done", "accept": 5, "impact": "Asset for PR pitches", "rel": {}, "completedAt": "Apr 22, 2026"},
    {"id": "T07", "recId": None, "title": "Fix schema markup on homepage", "type": "Technical SEO", "priority": "Low", "owner": "Daniel R.", "due": "Apr 20, 2026", "status": "Done", "accept": 3, "impact": "Schema validation passes", "rel": {}, "completedAt": "Apr 18, 2026"},
    {"id": "T08", "recId": None, "title": "Update 'About OCSiAl' page with 2026 production capacity", "type": "Content", "priority": "Medium", "owner": "Maria L.", "due": "Apr 22, 2026", "status": "Done", "accept": 2, "impact": "Freshness signal", "rel": {}, "completedAt": "Apr 20, 2026"},
    {"id": "T09", "recId": "R02", "title": "Batteriesnews.com — confirm inclusion in next update", "type": "PR / Outreach", "priority": "High", "owner": "Maria L.", "due": "May 30, 2026", "status": "Impact check", "accept": 1, "impact": "Measure citation uplift", "rel": {"source": "S01"}, "reviewDate": "May 30, 2026"},
    {"id": "T10", "recId": None, "title": "Document GSC + GA4 weekly export process", "type": "Content", "priority": "Low", "owner": "Daniel R.", "due": "Apr 26, 2026", "status": "Impact check", "accept": 1, "impact": "Ops runbook", "rel": {}},
]


SETTINGS = [
    {"setting_key": "target_brand", "setting_value": "OCSiAl", "notes": "Primary brand to monitor in AI answers."},
    {"setting_key": "target_product", "setting_value": "TUBALL", "notes": "Primary product name."},
    {"setting_key": "owned_domains", "setting_value": "ocsial.com,tuball.com", "notes": "Comma-separated list of domains treated as 'owned' for citation scoring."},
    {"setting_key": "competitors", "setting_value": "Cabot Corp,Orion Engineered Carbons,Imerys Graphite & Carbon,LG Chem CNT,Cnano Technology,Nanocyl,Arkema Graphistrength,Showa Denko VGCF", "notes": "Comma-separated competitor list."},
    {"setting_key": "openai_model", "setting_value": "gpt-4o-mini", "notes": "Default OpenAI model used for recommendation generation."},
    {"setting_key": "monthly_cost_cap_usd", "setting_value": "20", "notes": "Soft cap on OpenAI spend per month (UI warning only, enforcement in backend)."},
]


# ----------------------------- seeding -----------------------------
def _derive_related_url_id(url_string: str | None) -> str | None:
    """Given a URL fragment from a prompt (e.g. 'tuball.com/epoxy'), find the matching URL id."""
    if not url_string:
        return None
    clean = url_string.rstrip("/").lower()
    for u in URLS:
        if u["url"].rstrip("/").lower() == clean:
            return u["id"]
    return None


def _seed_settings(db: Session):
    if db.query(models.Setting).count():
        return
    for s in SETTINGS:
        db.add(models.Setting(**s))
    db.commit()


def _seed_prompts(db: Session):
    if db.query(models.Prompt).count():
        return
    for p in PROMPTS:
        db.add(models.Prompt(
            prompt_id=p["id"],
            prompt_text=p["text"],
            topic_cluster=p["cluster"],
            funnel_stage="awareness",
            country=p["country"],
            language=p["lang"].lower(),
            business_priority=p["priority"],
            target_brand="OCSiAl",
            target_product="TUBALL",
            target_url=p["url"] or "",
            status="active",
            platform=p["platform"],
            priority=_priority_from_business(p["priority"]),
            brand_mentioned=p["brand"],
            product_mentioned=p["product"],
            domain_cited=p["cited"],
            competitors_mentioned=list(p["comps"]),
            cited_sources=[],
            answer_quality_score=p["quality"],
            monitor_status=p["status"],
            related_url_id=_derive_related_url_id(p["url"]),
        ))
    db.commit()


def _seed_ai_results(db: Session):
    if db.query(models.AiResult).count():
        return
    for a in AI_ANSWERS:
        db.add(models.AiResult(
            result_id=f"AR-{uuid.uuid4().hex[:10]}",
            prompt_id=a["prompt_id"],
            platform=a["platform"],
            date_checked=a["date"],
            answer_text=a["answer"],
            brand_mentioned=a["brand_mentioned"],
            product_mentioned=a["product_mentioned"],
            domain_cited=a["domain_cited"],
            competitors_mentioned=list(a["competitors_mentioned"]),
            cited_sources=list(a["cited_sources"]),
            entities=list(a["entities"]),
            answer_quality_score=a["answer_quality_score"],
            notes="",
        ))
    # also populate cited_sources on the parent prompt for UI
    for a in AI_ANSWERS:
        p = db.query(models.Prompt).filter_by(prompt_id=a["prompt_id"]).one_or_none()
        if p:
            p.cited_sources = list(a["cited_sources"])
    db.commit()


def _seed_sources(db: Session):
    if db.query(models.Source).count():
        return
    for s in SOURCES:
        db.add(models.Source(
            source_id=s["id"],
            source_url=s["url"],
            domain=s["domain"],
            title=s["title"],
            source_type=s["type"],
            cited_by_prompts=list(s["cited_by"]),
            mentions_brand=s["brand"],
            mentions_product=s["product"],
            mentions_competitor=s["comp"],
            links_to_owned_domain=s["links_owned"],
            source_influence_score=s["influence"],
            outreach_status=s["outreach"],
            recommended_action="",
            updated=s["updated"],
        ))
    db.commit()


def _seed_urls(db: Session):
    if db.query(models.Url).count():
        return
    for u in URLS:
        checks = u["checks"]
        row = models.Url(
            url_id=u["id"],
            url=u["url"],
            domain=u["url"].split("/")[0],
            page_type=u["type"],
            topic_cluster=u["cluster"],
            target_prompts=list(u["prompts"]),
            indexable=u["indexable"],
            canonical="",
            title="",
            h1="",
            has_direct_answer=checks["answer"],
            has_comparison_table=checks["table"],
            has_faq=checks["faq"],
            has_citations=checks["cite"],
            has_internal_links=checks["links"],
            has_cta=checks["cta"],
            has_schema=checks["schema"],
            page_readiness_score=0,
            recommended_action="",
        )
        # recompute via scoring module
        row.page_readiness_score = page_readiness_score({
            "indexable": row.indexable,
            "has_direct_answer": row.has_direct_answer,
            "has_comparison_table": row.has_comparison_table,
            "has_faq": row.has_faq,
            "has_citations": row.has_citations,
            "has_internal_links": row.has_internal_links,
            "has_cta": row.has_cta,
            "has_schema": row.has_schema,
        })
        db.add(row)

        # also seed one SeoMetric row snapshot for this URL
        db.add(models.SeoMetric(
            metric_id=f"SM-{uuid.uuid4().hex[:10]}",
            date=date.today(),
            url_id=u["id"],
            query="",
            clicks=u["clicks"],
            impressions=u["impressions"],
            ctr=u["ctr"],
            avg_position=u["pos"],
            sessions=u["sessions"],
            conversions=u["conv"],
        ))
    db.commit()


def _seed_recommendations(db: Session):
    if db.query(models.Recommendation).count():
        return
    for r in RECS:
        created = datetime.strptime(r["created"], "%b %d, %Y")
        db.add(models.Recommendation(
            recommendation_id=r["id"],
            title=r["title"],
            type=r["type"],
            diagnosis=r["diagnosis"],
            evidence=list(r["evidence"]),
            recommended_actions=list(r["actions"]),
            acceptance_criteria=list(r["acceptance"]),
            related_prompt_id=r["promptId"],
            related_url=r["urlId"],
            related_source_id=r["sourceId"],
            priority_score=r["priority_score"],
            confidence_score=r["confidence_score"],
            expected_impact=r["impact"],
            status=_rec_status_map(r["status"]),
            created_at=created,
            score_breakdown={
                "business_priority": 70,
                "ai_visibility_gap": 80,
                "seo_opportunity": 60,
                "competitor_pressure": 65,
                "source_influence": 55,
                "conversion_potential": 50,
                "implementation_effort": 40,
                "dependency_risk": 20,
            },
        ))
    db.commit()


def _seed_tasks(db: Session):
    if db.query(models.Task).count():
        return
    for t in TASKS:
        rel = t.get("rel") or {}
        # acceptance_criteria is int in fixtures → expand to placeholder list
        accept_count = int(t.get("accept") or 0)
        acceptance = [f"Criterion {i+1}" for i in range(accept_count)]
        db.add(models.Task(
            task_id=t["id"],
            recommendation_id=t.get("recId"),
            task_title=t["title"],
            task_type=t["type"],
            owner=t["owner"],
            owner_initials=_initials(t["owner"]),
            status=t["status"],
            due_date=_parse_date(t.get("due")),
            priority=t["priority"],
            acceptance_criteria=acceptance,
            expected_impact=t["impact"],
            actual_impact="",
            review_date=_parse_date(t.get("reviewDate")) or _parse_date(t.get("completedAt")),
            related_prompt_id=rel.get("prompt"),
            related_url=rel.get("url"),
            related_source_id=rel.get("source"),
            blocked_reason=t.get("blocked", "") or "",
            created_at=datetime.utcnow(),
        ))
    db.commit()


def init_db():
    """Create tables and seed all fixtures if the DB is empty."""
    import os
    from app.config import settings as _s
    # Only create the local sqlite data dir for true on-disk sqlite URLs
    if _s.database_url.startswith("sqlite:///"):
        os.makedirs("data", exist_ok=True)

    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        _seed_settings(db)
        _seed_sources(db)     # sources before prompts so we have something to reference
        _seed_urls(db)        # urls before prompts so related_url_id resolves
        _seed_prompts(db)
        _seed_ai_results(db)
        _seed_recommendations(db)
        _seed_tasks(db)
    finally:
        db.close()


if __name__ == "__main__":
    init_db()
    print("Database seeded.")
