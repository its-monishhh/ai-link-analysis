import json
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import analysis_service, llm_service
from app.services.website_extractor import WebsiteContent, parse_html
from app.utils.validators import BlockedURLError, InvalidURLError, assert_public_url, normalize_url

client = TestClient(app)

VALID_LLM_JSON = {
    "brand": {"name": "Acme Web", "category": "Web Development", "summary": "Builds websites."},
    "analysis": {
        "competitor_analysis": {
            "brand_overview": "Small web agency.",
            "competitors": [{"name": "Acme Web", "website": None},
                            {"name": "Rival A"}, {"name": "Rival B"}, {"name": "Rival C"}],
            "strengths": ["Affordable"], "opportunities": ["SEO"]},
        "target_search_queries": [
            {"query": f"q{i}", "platforms": ["Google"], "intent": "Service Search"} for i in range(6)],
        "seo_improvements": {"primary_keyword": "web development", "recommended_title": "T",
                             "meta_description": "D"},
        "top_services": [{"name": f"s{i}", "description": "d"} for i in range(6)],
        "target_audience": {"primary_audience": ["SMBs"]},
    },
}


# ---- validators / SSRF ----
def test_normalize_adds_scheme():
    assert normalize_url("example.com") == "https://example.com"


@pytest.mark.parametrize("bad", ["", "ftp://example.com", "https://user:pw@example.com", "not a url"])
def test_normalize_rejects(bad):
    with pytest.raises(InvalidURLError):
        normalize_url(bad)


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://127.0.0.1", "http://localhost", "http://10.0.0.5",
                                 "http://169.254.169.254/latest/meta-data", "http://[::1]/",
                                 "http://192.168.1.1"])
async def test_ssrf_blocked(url):
    with pytest.raises(BlockedURLError):
        await assert_public_url(url)


# ---- API behaviour ----
def test_invalid_url_400():
    r = client.post("/link-analysis", json={"url": "ftp://x.com"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_url"


def test_private_url_403():
    r = client.post("/link-analysis", json={"url": "http://169.254.169.254"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "blocked_url"


def test_missing_body_422():
    r = client.post("/link-analysis", json={})
    assert r.status_code == 422 and r.json()["success"] is False


def test_success_flow(monkeypatch):
    async def fake_assert(url): return None
    async def fake_extract(url, s):
        return WebsiteContent(url=url, title="Acme Web", body_text="We build websites.")
    async def fake_complete(system, user, provider, model, s):
        return llm_service.LLMResult("```json\n" + json.dumps(VALID_LLM_JSON) + "\n```", model, 1000, 800)
    monkeypatch.setattr("app.main.assert_public_url", fake_assert)
    monkeypatch.setattr("app.main.extract_website", fake_extract)
    monkeypatch.setattr(llm_service, "complete", fake_complete)
    analysis_service.cache._d.clear()

    r = client.post("/link-analysis", json={"url": "https://acme.test"})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] and body["brand"]["name"] == "Acme Web"
    a = body["analysis"]
    assert len(a["target_search_queries"]) == 5 and len(a["top_services"]) == 5
    names = [c["name"] for c in a["competitor_analysis"]["competitors"]]
    assert "Acme Web" not in names and len(names) == 3   # self removed
    assert body["meta"]["cached"] is False

    r2 = client.post("/link-analysis", json={"url": "https://acme.test"})
    assert r2.json()["meta"]["cached"] is True


def test_llm_failure_502(monkeypatch):
    async def fake_assert(url): return None
    async def fake_extract(url, s): return WebsiteContent(url=url, title="x", body_text="y")
    async def boom(*a, **k): raise llm_service.LLMError("down")
    monkeypatch.setattr("app.main.assert_public_url", fake_assert)
    monkeypatch.setattr("app.main.extract_website", fake_extract)
    monkeypatch.setattr(llm_service, "complete", boom)
    analysis_service.cache._d.clear()
    r = client.post("/link-analysis", json={"url": "https://fail.test"})
    assert r.status_code == 502 and r.json()["error"]["code"] == "llm_failure"


# ---- extraction / json ----
def test_parse_html_strips_noise():
    html = """<html><head><title>Acme</title><meta name="description" content="We build."></head>
    <body><nav><a href="/a">Services</a></nav><script>evil()</script>
    <h1>Web design</h1><p>We design beautiful, fast websites for small businesses.</p>
    <p>We design beautiful, fast websites for small businesses.</p></body></html>"""
    c = parse_html(html, "https://acme.test", 5000)
    assert c.title == "Acme" and c.meta_description == "We build."
    assert "evil" not in c.body_text and c.body_text.count("beautiful") == 1
    assert "Services" in c.nav_items and "Web design" in c.headings


def test_extract_json_tolerant():
    assert llm_service.extract_json('Here: {"a": 1} done') == {"a": 1}
