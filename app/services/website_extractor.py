"""Fetch + clean website content (SSRF-safe, size-capped, manual redirects)."""
import re
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.config import Settings
from app.utils.validators import assert_public_url

USER_AGENT = "Mozilla/5.0 (compatible; LinkAnalysisBot/1.0)"
MAX_REDIRECTS = 5
EXTRA_PAGE_HINTS = re.compile(r"about|service|product|solution|what-we-do|offering", re.I)


class WebsiteFetchError(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


@dataclass
class WebsiteContent:
    url: str
    title: str = ""
    meta_description: str = ""
    site_name: str = ""
    headings: List[str] = field(default_factory=list)
    nav_items: List[str] = field(default_factory=list)
    body_text: str = ""
    pages_analyzed: int = 1

    def to_prompt_text(self) -> str:
        parts = [
            f"URL: {self.url}",
            f"Title: {self.title}",
            f"Site name: {self.site_name}",
            f"Meta description: {self.meta_description}",
            f"Navigation: {', '.join(self.nav_items)}",
            "Headings:\n" + "\n".join(f"- {h}" for h in self.headings),
            "Body text:\n" + self.body_text,
        ]
        return "\n".join(parts)


async def _safe_get(client: httpx.AsyncClient, url: str, settings: Settings) -> httpx.Response:
    """GET with SSRF re-validation on every redirect hop and a body size cap."""
    for _ in range(MAX_REDIRECTS + 1):
        await assert_public_url(url)
        try:
            async with client.stream("GET", url, headers={"User-Agent": USER_AGENT}) as r:
                if r.is_redirect:
                    loc = r.headers.get("location")
                    if not loc:
                        raise WebsiteFetchError("fetch_failed", "Redirect without location.")
                    url = urljoin(url, loc)
                    continue
                if r.status_code in (401, 403, 429):
                    raise WebsiteFetchError(
                        "access_restricted",
                        f"The website blocked automated access (HTTP {r.status_code}).")
                if r.status_code >= 400:
                    raise WebsiteFetchError("fetch_failed", f"Website returned HTTP {r.status_code}.")
                ctype = r.headers.get("content-type", "")
                if "html" not in ctype and "xml" not in ctype and ctype:
                    raise WebsiteFetchError("unsupported_content", f"Not an HTML page ({ctype}).")
                chunks, size = [], 0
                async for chunk in r.aiter_bytes():
                    size += len(chunk)
                    if size > settings.max_html_bytes:
                        break  # truncate; enough to analyse
                    chunks.append(chunk)
                r.encoding = r.encoding or "utf-8"
                # bytes are already decoded by httpx, so drop encoding headers
                hdrs = {k: v for k, v in r.headers.items()
                        if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")}
                resp = httpx.Response(r.status_code, content=b"".join(chunks),
                                      headers=hdrs, request=r.request)
                resp.url_final = url  # type: ignore[attr-defined]
                return resp
        except httpx.TimeoutException:
            raise WebsiteFetchError("timeout", "The website took too long to respond.")
        except httpx.HTTPError as e:
            raise WebsiteFetchError("fetch_failed", f"Could not reach the website: {type(e).__name__}.")
    raise WebsiteFetchError("fetch_failed", "Too many redirects.")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_html(html: str, url: str, max_chars: int) -> WebsiteContent:
    soup = BeautifulSoup(html, "lxml")
    title = _clean(soup.title.get_text()) if soup.title else ""

    def meta(**attrs) -> str:
        tag = soup.find("meta", attrs=attrs)
        return _clean(tag.get("content", "")) if tag else ""

    desc = meta(name="description") or meta(property="og:description")
    site_name = meta(property="og:site_name")

    nav_items: List[str] = []
    for nav in soup.find_all("nav"):
        for a in nav.find_all("a"):
            t = _clean(a.get_text())
            if 1 < len(t) < 40 and t not in nav_items:
                nav_items.append(t)
    nav_items = nav_items[:25]

    for tag in soup(["script", "style", "noscript", "svg", "iframe", "form", "nav", "footer", "header"]):
        tag.decompose()

    headings, seen = [], set()
    for h in soup.find_all(["h1", "h2", "h3"]):
        t = _clean(h.get_text())
        if t and t.lower() not in seen:
            seen.add(t.lower())
            headings.append(t)
    headings = headings[:40]

    # de-duplicate repeated lines/blocks to save tokens
    blocks, seen_blocks = [], set()
    for el in soup.find_all(["p", "li", "td"]):
        t = _clean(el.get_text())
        if len(t) < 25 or t.lower() in seen_blocks:
            continue
        seen_blocks.add(t.lower())
        blocks.append(t)
    body = " ".join(blocks)[:max_chars]
    return WebsiteContent(url=url, title=title, meta_description=desc, site_name=site_name,
                          headings=headings, nav_items=nav_items, body_text=body)


def _candidate_links(html: str, base_url: str) -> List[str]:
    soup = BeautifulSoup(html, "lxml")
    host = urlparse(base_url).hostname
    out: List[str] = []
    for a in soup.find_all("a", href=True):
        link = urljoin(base_url, a["href"]).split("#")[0]
        p = urlparse(link)
        if p.hostname == host and p.scheme in ("http", "https") and link != base_url \
                and EXTRA_PAGE_HINTS.search(p.path) and link not in out:
            out.append(link)
    return out[:2]


async def extract_website(url: str, settings: Settings,
                          client: Optional[httpx.AsyncClient] = None) -> WebsiteContent:
    own = client is None
    client = client or httpx.AsyncClient(timeout=settings.fetch_timeout_seconds, follow_redirects=False)
    try:
        resp = await _safe_get(client, url, settings)
        html = resp.text
        final_url = getattr(resp, "url_final", url)
        half = settings.max_content_chars
        content = parse_html(html, final_url, half)
        if not (content.title or content.body_text or content.headings):
            raise WebsiteFetchError("no_content", "No readable content found (page may be JavaScript-rendered).")

        # Best-effort: pull up to 2 About/Services pages for richer context.
        budget = settings.max_content_chars - len(content.body_text)
        for link in _candidate_links(html, final_url):
            if budget < 1500:
                break
            try:
                r2 = await _safe_get(client, link, settings)
                extra = parse_html(r2.text, link, min(budget, 4000))
                content.body_text += f" [Page {link}] " + extra.body_text
                content.headings += [h for h in extra.headings if h not in content.headings][:15]
                content.pages_analyzed += 1
                budget = settings.max_content_chars - len(content.body_text)
            except Exception:
                continue  # extra pages are optional
        return content
    finally:
        if own:
            await client.aclose()
