"""
Shared monitoring visibility rules.

For this MVP, OCSiAl and TUBALL are equivalent success entities. A prompt is
covered if the AI answer mentions either entity OR cites an owned domain.
"""

from __future__ import annotations

from urllib.parse import urlparse


DEFAULT_SUCCESS_TERMS = ["OCSiAl", "TUBALL"]
DEFAULT_OWNED_DOMAINS = ["ocsial.com", "tuball.com", "industries.tuball.com"]


def split_csv(value: str | None) -> list[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def unique_terms(*groups: list[str]) -> list[str]:
    out: list[str] = []
    seen = set()
    for group in groups:
        for item in group:
            key = item.strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append(item.strip())
    return out


def detect_terms(text: str, terms: list[str]) -> list[str]:
    low = (text or "").lower()
    found: list[str] = []
    for term in terms:
        if term and term.lower() in low:
            found.append(term)
    return found


def domain_of(url_or_domain: str) -> str:
    raw = (url_or_domain or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    try:
        return (urlparse(raw).hostname or "").lower().lstrip("www.")
    except Exception:
        return ""


def domain_matches_owned(domain: str, owned_domains: list[str]) -> bool:
    d = domain_of(domain)
    owned = [domain_of(x) for x in (owned_domains or DEFAULT_OWNED_DOMAINS)]
    return any(d == od or d.endswith("." + od) for od in owned if od)


def derive_monitor_status(*, visible: bool, competitors: list[str] | None, domain_cited: bool = False) -> str:
    if visible or domain_cited:
        return "Good"
    if competitors:
        return "Risk"
    return "Gap"


def brand_success_terms(target_brand: str = "OCSiAl", target_product: str = "TUBALL", aliases: list[str] | None = None) -> list[str]:
    return unique_terms(
        DEFAULT_SUCCESS_TERMS,
        [target_brand or "OCSiAl", target_product or "TUBALL"],
        aliases or [],
    )


def is_run_prompt(prompt) -> bool:
    return getattr(prompt, "monitor_status", "Unchecked") != "Unchecked"


def is_visible_prompt(prompt) -> bool:
    return bool(
        getattr(prompt, "brand_mentioned", False)
        or getattr(prompt, "product_mentioned", False)
        or getattr(prompt, "domain_cited", False)
    )
