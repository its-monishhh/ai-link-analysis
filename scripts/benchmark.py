"""Measure latency, tokens and cost per model on real URLs.
Usage: python scripts/benchmark.py https://site1.com https://site2.com
Fill PRICES with the CURRENT numbers from each provider's pricing page first."""
import asyncio, sys, time, json
from app.config import get_settings
from app.services import llm_service
from app.services.analysis_service import SYSTEM_PROMPT
from app.services.website_extractor import extract_website

# (provider, model): (input $/1M, output $/1M)  -- verify before running
PRICES = {
    ("anthropic", "claude-haiku-5-5"): (0.10, 0.50),
    ("anthropic", "claude-sonnet-5-5"): (2.00, 10.00),
    ("gemini", "gemini-3.5-flash-lite"): (0.30, 1.50),
}


async def main(urls):
    s = get_settings()
    rows = []
    for url in urls:
        content = await extract_website(url, s)
        user = "Website content:\n\n" + content.to_prompt_text()
        for (prov, model), (pin, pout) in PRICES.items():
            t = time.perf_counter()
            try:
                r = await llm_service.complete(SYSTEM_PROMPT, user, prov, model, s)
                dt = time.perf_counter() - t
                try:
                    llm_service.extract_json(r.text); valid = True
                except Exception:
                    valid = False
                cost = ((r.input_tokens or 0) * pin + (r.output_tokens or 0) * pout) / 1e6
                rows.append(dict(url=url, model=model, seconds=round(dt, 2), in_tok=r.input_tokens,
                                 out_tok=r.output_tokens, cost_usd=round(cost, 5), valid_json=valid))
            except Exception as e:
                rows.append(dict(url=url, model=model, error=str(e)))
    print(json.dumps(rows, indent=2))

asyncio.run(main(sys.argv[1:]))
