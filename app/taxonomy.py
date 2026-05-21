"""
Canonical buyer-opportunity taxonomy for monitored prompts.

The taxonomy is intentionally deterministic. Prompt Research, evidence
aggregation, and recommendation processing all use these fields so legacy topic
cluster wording does not split one buyer opportunity into several strategies.
"""

from __future__ import annotations

import re
from typing import Iterable


TAXONOMY_VERSION = "2026-05-21.v1"

PRODUCT_PATTERNS = (
    ("TUBALL MATRIX", ("tuball matrix", "masterbatch")),
    ("TUBALL / graphene nanotubes", ("tuball", "graphene nanotube", "graphene nanotubes")),
    ("SWCNT additives", ("single-walled carbon nanotube", "single wall carbon nanotube", "swcnt")),
    ("CNT additives", ("carbon nanotube", "carbon nanotubes", "cnt", "nanotube")),
    ("Conductive additives", ("conductive additive", "conductive filler", "conductive agent", "conductivity")),
    ("Anti-static / ESD additives", ("anti-static", "antistatic", "static electricity", "esd")),
    ("Carbon black substitutes", ("carbon black", "conductive carbon black")),
)

APPLICATION_PATTERNS = (
    ("silicone rubber", ("silicone rubber", "silicone")),
    ("rubber and elastomers", ("elastomer", "elastomers", "rubber", "epdm", "fkm")),
    ("plastics and polymers", ("plastic", "plastics", "polymer", "polymers", "pa ", "polyamide", "polycarbonate", "abs", "tpu")),
    ("coatings and paints", ("coating", "coatings", "paint", "paints", "gelcoat", "flooring")),
    ("epoxy and resins", ("epoxy", "resin", "thermoset")),
    ("battery electrodes", ("battery", "batteries", "electrode", "cathode", "anode")),
    ("adhesives and sealants", ("adhesive", "adhesives", "sealant")),
    ("composites", ("composite", "composites", "lightweight")),
    ("masterbatch and compounds", ("masterbatch", "compound", "compounds")),
)

INTENT_PATTERNS = (
    ("supplier/vendor", ("supplier", "suppliers", "vendor", "vendors", "manufacturer", "manufacturers", "companies", "company", "procurement")),
    ("comparison", (" vs ", " versus ", "compare", "comparison")),
    ("substitute/alternative", ("substitute", "replacement", "instead of", "alternative", "alternatives")),
    ("safety/regulatory", ("safety", "regulatory", "regulation", "reach", "toxicity", "standard", "standards")),
    ("application/use-case", ("what additive", "which additive", "best additive", "how to make", "application", "use case")),
    ("performance", ("low dosage", "low loading", "color", "transparent", "mechanical", "viscosity", "conductivity", "uniform")),
)

SUBSTITUTE_PATTERNS = (
    ("carbon black", ("carbon black", "conductive carbon black", "ketjenblack")),
    ("MWCNT", ("mwcnt", "multi-walled carbon nanotube", "multi wall carbon nanotube", "multiwalled carbon nanotube")),
    ("graphene", ("graphene nanoplatelet", "graphene nanoplatelets", "graphene")),
    ("carbon fiber", ("carbon fiber", "carbon fibres", "carbon fibre")),
    ("metal fillers", ("metal filler", "metal fillers", "metal powder", "metal powders")),
)


def classify_prompt_taxonomy(text: str, legacy_cluster: str = "") -> dict:
    hay = re.sub(r"\s+", " ", f"{text or ''} {legacy_cluster or ''}".lower())
    product_area, product_hits = _first_pattern(hay, PRODUCT_PATTERNS)
    application, application_hits = _first_pattern(hay, APPLICATION_PATTERNS)
    buyer_intent, intent_hits = _first_pattern(hay, INTENT_PATTERNS)
    substitute_theme, substitute_hits = _first_pattern(hay, SUBSTITUTE_PATTERNS)

    product_area = product_area or "Conductive / anti-static additives"
    application = application or "general industrial materials"
    buyer_intent = buyer_intent or "category education"
    substitute_theme = substitute_theme or ""

    if substitute_theme and buyer_intent == "category education":
        buyer_intent = "substitute/alternative"
    if any(term in hay for term in ("best", "which", "what additive", "how to make")) and buyer_intent == "category education":
        buyer_intent = "application/use-case"

    display_label = taxonomy_label(product_area, application, buyer_intent, substitute_theme, fallback=legacy_cluster)
    matched_dimensions = sum(bool(v) for v in (product_hits, application_hits, intent_hits, substitute_hits))
    confidence = min(95, 35 + matched_dimensions * 15 + min(15, len(product_hits | application_hits | intent_hits | substitute_hits) * 3))
    return {
        "opportunity_key": _slug("|".join([product_area, application, buyer_intent, substitute_theme])),
        "opportunity_label": display_label,
        "display_label": display_label,
        "product_area": product_area,
        "application": application,
        "buyer_intent": buyer_intent,
        "substitute_theme": substitute_theme,
        "taxonomy_confidence": confidence,
        "taxonomy_version": TAXONOMY_VERSION,
    }


def taxonomy_label(product_area: str, application: str, buyer_intent: str, substitute_theme: str = "", fallback: str = "") -> str:
    label_parts: list[str] = []
    if application and application != "general industrial materials":
        label_parts.append(human_label(application))
    label_parts.append(human_label(product_area or "GEO opportunity"))
    if buyer_intent not in ("", "category education", "application/use-case"):
        label_parts.append(human_label(buyer_intent))
    if substitute_theme:
        label_parts.append("vs " + human_label(substitute_theme))
    return " / ".join(dict.fromkeys(label_parts))[:96] or human_label(fallback or "GEO opportunity")


def prompt_taxonomy(prompt) -> dict:
    if getattr(prompt, "taxonomy_version", "") and getattr(prompt, "product_area", ""):
        display_label = getattr(prompt, "taxonomy_label", "") or taxonomy_label(
            getattr(prompt, "product_area", ""),
            getattr(prompt, "application", ""),
            getattr(prompt, "buyer_intent", ""),
            getattr(prompt, "substitute_theme", ""),
            getattr(prompt, "topic_cluster", ""),
        )
        return {
            "opportunity_key": _slug("|".join([
                getattr(prompt, "product_area", ""),
                getattr(prompt, "application", ""),
                getattr(prompt, "buyer_intent", ""),
                getattr(prompt, "substitute_theme", ""),
            ])),
            "opportunity_label": display_label,
            "display_label": display_label,
            "product_area": getattr(prompt, "product_area", ""),
            "application": getattr(prompt, "application", ""),
            "buyer_intent": getattr(prompt, "buyer_intent", ""),
            "substitute_theme": getattr(prompt, "substitute_theme", ""),
            "taxonomy_confidence": getattr(prompt, "taxonomy_confidence", 0),
            "taxonomy_version": getattr(prompt, "taxonomy_version", TAXONOMY_VERSION),
        }
    return classify_prompt_taxonomy(getattr(prompt, "prompt_text", ""), getattr(prompt, "topic_cluster", ""))


def apply_prompt_taxonomy(prompt, force: bool = False) -> dict:
    current_version = getattr(prompt, "taxonomy_version", "")
    if current_version == TAXONOMY_VERSION and getattr(prompt, "product_area", "") and not force:
        return prompt_taxonomy(prompt)
    row = classify_prompt_taxonomy(getattr(prompt, "prompt_text", ""), getattr(prompt, "topic_cluster", ""))
    prompt.product_area = row["product_area"]
    prompt.application = row["application"]
    prompt.buyer_intent = row["buyer_intent"]
    prompt.substitute_theme = row["substitute_theme"]
    prompt.taxonomy_label = row["display_label"]
    prompt.taxonomy_confidence = row["taxonomy_confidence"]
    prompt.taxonomy_version = row["taxonomy_version"]
    return row


def human_label(value: str) -> str:
    raw = re.sub(r"\s+", " ", (value or "").strip())
    if not raw:
        return raw
    acronyms = {"ai", "geo", "seo", "gsc", "ga4", "esd", "faq", "url", "cnt", "swcnt", "mwcnt", "pa", "pc", "abs", "tpu", "epdm", "fkm"}
    small = {"and", "or", "for", "to", "of", "in", "vs", "with", "without", "the", "a", "an"}
    words = []
    for i, word in enumerate(raw.replace("/", " / ").split()):
        lower = word.lower()
        if word == "/":
            words.append("/")
        elif lower in acronyms:
            words.append(lower.upper())
        elif i > 0 and lower in small:
            words.append(lower)
        else:
            words.append(lower[:1].upper() + lower[1:])
    return " ".join(words).replace(" / ", "/")


def _first_pattern(text: str, patterns: Iterable[tuple[str, Iterable[str]]]) -> tuple[str, set[str]]:
    for label, needles in patterns:
        hits = {needle for needle in needles if needle in f" {text} "}
        if hits:
            return label, hits
    return "", set()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-") or "general"
