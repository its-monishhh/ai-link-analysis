# AI Website Link Analysis API

FastAPI service that fetches a website, cleans its content, and uses an LLM to produce five analysis sections:
competitor comparison, top-5 search queries, SEO improvements, top-5 services, and target audience.

## Setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # add ANTHROPIC_API_KEY (and optionally GEMINI_API_KEY for fallback)
uvicorn app.main:app --host 0.0.0.0 --port 4000
```
Docs: http://localhost:4000/docs  |  Tests: `pytest`  |  Docker: `docker build -t link-analysis . && docker run -p 4000:4000 --env-file .env link-analysis`

## Usage
```bash
curl -X POST http://localhost:4000/link-analysis -H "Content-Type: application/json" -d '{"url":"https://example.com"}'
```
Response shape: `success`, `website`, `brand`, `analysis.{competitor_analysis, target_search_queries, seo_improvements, top_services, target_audience}`, `meta` (model, tokens, latency, cached).

<!-- TODO before submitting: paste a real sample request/response + screenshot of /docs here, and your Live Demo URL -->

Errors return `{"success": false, "error": {"code", "message"}}`:
`400 invalid_url`, `403 blocked_url` (SSRF), `422 invalid_request`, `502 fetch_failed / access_restricted / llm_failure`, `504 timeout`.

## Design
1. **Validate** URL (http/https only, no credentials) → **SSRF protection**: DNS is resolved and every address must be public; checked again on *every redirect hop* (redirects are followed manually). Response size capped (2 MB), timeouts enforced.
2. **Extract** (`website_extractor.py`): title, meta/OG tags, nav, headings, de-duplicated body text; scripts/styles/nav/footer removed; up to 2 extra About/Services pages fetched. Content capped at ~12k chars (~3-4k tokens).
3. **Analyse** (`llm_service.py`, `analysis_service.py`): one LLM call returning JSON for all five sections; validated with Pydantic; on invalid JSON → one repair retry → fallback model. Provider errors go straight to the fallback.
4. **Honesty rules**: no search volumes are produced (no data source); competitors are labelled `ai_generated_suggestion`; services are labelled `website` or `inferred`.
5. **Caching**: in-memory TTL cache per URL (default 1h). Keys only via env vars; `.env` is git-ignored.

Limitation: JavaScript-rendered sites (SPA with empty HTML) return a `no_content` error; a headless browser would be the next step.

## LLM comparison

Prices are USD per 1M tokens, taken from provider/press pages in Oct–Nov 2026 — **re-check the official pricing pages before relying on them**.
Speed and accuracy columns are **not measured by me yet**; run `python scripts/benchmark.py <urls>` and paste your results below.

| Provider | Model | Input / Output $ per 1M | Context | JSON | Speed | Accuracy |
|---|---|---|---|---|---|---|
| Anthropic | claude-haiku-5-5 | 0.10 / 0.50 (prompts ≤100K; 0.50 / 2.50 above) | 1M | Prompted JSON + Pydantic validation | _measure_ | _measure_ |
| Anthropic | claude-sonnet-5-5 | 2.00 / 10.00 | 1M | same | _measure_ | _measure_ |
| Google | Gemini 3.5 Flash-Lite | 0.30 / 1.50 (Vertex global list price; confirm AI Studio price) | check docs | Native `responseMimeType: application/json` | _measure_ | _measure_ |
| OpenAI | GPT-6 Luna | ~0.10 / 0.50 (single secondary source; verify) | check docs | Structured outputs | _measure_ | _measure_ |

### Recommendation (pending your benchmark)
- **Primary:** `claude-haiku-5-5` — lowest list price with a large context; the task is extraction + structured generation, which suits a small model.
- **Fallback:** Gemini Flash-Lite (different vendor → resilience) or `claude-sonnet-5-5` if quality on your test sites is lacking.
- **Estimate per analysis** (assumption: ~4k input, ~2k output tokens; real counts are returned in `meta`): Haiku 5.5 ≈ $0.0014, Gemini Flash-Lite ≈ $0.004, Sonnet 5.5 ≈ $0.028. These are estimates, not measurements. Don't claim "maximum accuracy" without running the benchmark on several sites.

### Cost optimisation implemented / recommended
Content cleaning + de-duplication and a hard char cap; compact prompt; single call for all five sections; small model by default with escalation only on failure; TTL cache to avoid repeat calls; keep prompts under Haiku's 100K tier; prompt caching for the static system prompt and batch API for bulk jobs (50% off) as next steps.
