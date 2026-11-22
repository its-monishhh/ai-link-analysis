"""Post-processing for competitor data returned by the LLM."""
from urllib.parse import urlparse

from app.schemas import CompetitorAnalysis


def _host(u: str) -> str:
    u = u if "://" in u else "https://" + u
    return (urlparse(u).hostname or "").removeprefix("www.")


def clean_competitors(ca: CompetitorAnalysis, brand_name: str, site_url: str) -> CompetitorAnalysis:
    """Drop the brand itself and duplicates; label everything as AI-suggested."""
    own_host, seen, kept = _host(site_url), set(), []
    for c in ca.competitors:
        key = c.name.strip().lower()
        if not key or key in seen or key == brand_name.strip().lower():
            continue
        if c.website and _host(c.website) == own_host:
            continue
        seen.add(key)
        c.source = "ai_generated_suggestion"
        kept.append(c)
    ca.competitors = kept
    if len(kept) < 3:
        ca.note += " Fewer than 3 reliable competitors could be identified."
    return ca
