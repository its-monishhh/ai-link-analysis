"""Orchestrates: extract -> prompt LLM -> validate -> (retry/fallback) -> response."""
import json
import time
from typing import Dict, Tuple

from pydantic import ValidationError

from app.config import Settings
from app.schemas import LinkAnalysisResponse, LLMOutput, Meta
from app.services import llm_service
from app.services.competitor_service import clean_competitors
from app.services.website_extractor import WebsiteContent

SYSTEM_PROMPT = """You are a senior digital-marketing and SEO analyst.
Analyse ONLY the website content provided by the user. Rules:
- Base services, brand facts and audience on the provided content. Label a service "inferred" if it is not explicitly on the site.
- Competitors are your own suggestions (min 3 if you can name real, well-known ones; otherwise fewer). Never invent companies or URLs; use null for website if unsure.
- NEVER output search volumes, rankings or traffic numbers.
- Exactly 5 search queries and 5 services. Query platforms must be chosen from: Google, Facebook, Instagram, LinkedIn, X.
- Return ONLY one valid JSON object, no markdown, no commentary.

JSON shape:
{
 "brand": {"name": str, "category": str, "summary": str},
 "analysis": {
  "competitor_analysis": {"brand_overview": str, "main_category": str, "main_products_services": [str],
    "competitors": [{"name": str, "website": str|null, "strengths": [str], "weaknesses": [str]}],
    "comparison_table": [{"comparison": str, "your_brand": str, "competitor": str}],
    "strengths": [str], "opportunities": [str]},
  "target_search_queries": [{"query": str, "platforms": [str], "intent": str, "business_relevance": str, "target_audience": str}],
  "seo_improvements": {"primary_keyword": str, "secondary_keywords": [str], "long_tail_keywords": [str],
    "business_keywords": [str], "recommended_title": str (<=60 chars), "title_structure": str,
    "meta_description": str (<=160 chars), "supporting_content": [str]},
  "top_services": [{"name": str, "description": str, "target_industry": str, "target_customer": str,
    "main_benefit": str, "source": "website"|"inferred"}],
  "target_audience": {"primary_audience": [str], "secondary_audience": [str], "industries": [str],
    "business_types": [str], "customer_profiles": [str], "geographic_focus": str,
    "customer_needs": [str], "customer_interests": [str],
    "recommended_segments": [{"audience": str, "business_type": str, "requirement": str}]}
 }
}"""


class AnalysisFailed(Exception):
    pass


class _Cache:
    def __init__(self) -> None:
        self._d: Dict[str, Tuple[float, LinkAnalysisResponse]] = {}

    def get(self, key: str, ttl: int):
        hit = self._d.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        self._d.pop(key, None)
        return None

    def set(self, key: str, val: LinkAnalysisResponse) -> None:
        self._d[key] = (time.time(), val)


cache = _Cache()


async def _ask(content: WebsiteContent, provider: str, model: str, s: Settings, repair: str = ""):
    user = "Website content:\n\n" + content.to_prompt_text()
    if repair:
        user += f"\n\nYour previous reply was invalid ({repair}). Return ONLY the corrected JSON object."
    res = await llm_service.complete(SYSTEM_PROMPT, user, provider, model, s)
    data = llm_service.extract_json(res.text)
    return LLMOutput.model_validate(data), res


async def analyze(content: WebsiteContent, s: Settings) -> LinkAnalysisResponse:
    key = content.url.rstrip("/").lower()
    cached = cache.get(key, s.cache_ttl_seconds)
    if cached:
        out = cached.model_copy(deep=True)
        out.meta.cached = True
        return out

    start = time.perf_counter()
    attempts = [(s.llm_provider, s.llm_model), (s.llm_provider, s.llm_model),
                (s.fallback_provider, s.fallback_model)]
    repair, last_err, parsed, res = "", "", None, None
    for i, (prov, model) in enumerate(attempts):
        try:
            parsed, res = await _ask(content, prov, model, s, repair if i == 1 else "")
            break
        except (json.JSONDecodeError, ValidationError) as e:
            repair = "not valid against the required JSON schema"
            last_err = f"invalid output from {model}: {type(e).__name__}"
        except llm_service.LLMError as e:
            last_err = str(e)
            if i == 0:  # provider error: skip same-model retry, go to fallback
                attempts[1] = attempts[2]
    if parsed is None or res is None:
        raise AnalysisFailed(last_err or "LLM analysis failed.")

    parsed.analysis.competitor_analysis = clean_competitors(
        parsed.analysis.competitor_analysis, parsed.brand.name, content.url)
    parsed.analysis.target_search_queries = parsed.analysis.target_search_queries[:5]
    parsed.analysis.top_services = parsed.analysis.top_services[:5]

    out = LinkAnalysisResponse(
        website=content.url, brand=parsed.brand, analysis=parsed.analysis,
        meta=Meta(model=res.model, pages_analyzed=content.pages_analyzed,
                  content_chars=len(content.body_text),
                  elapsed_seconds=round(time.perf_counter() - start, 2),
                  input_tokens=res.input_tokens, output_tokens=res.output_tokens))
    cache.set(key, out)
    return out
