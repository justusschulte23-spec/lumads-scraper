import os
import re
import urllib.parse
from fastapi import FastAPI
from pydantic import BaseModel
import httpx
from bs4 import BeautifulSoup
import markdownify
from typing import List

app = FastAPI(title="Lumads Scraper - Firecrawl Compatible API")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
}


class ScrapeRequest(BaseModel):
    url: str
    formats: List[str] = ["markdown"]
    onlyMainContent: bool = True
    timeout: int = 30000


class SearchRequest(BaseModel):
    query: str
    limit: int = 10
    lang: str = "de"
    country: str = "de"


@app.get("/health")
def health():
    return {"status": "ok", "service": "lumads-scraper"}


@app.get("/")
def root():
    return {"status": "ok", "service": "lumads-scraper"}


@app.post("/v1/scrape")
async def scrape(req: ScrapeRequest):
    try:
        timeout_s = min(req.timeout / 1000, 45)
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True, headers=HEADERS) as client:
            resp = await client.get(req.url)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")

        for tag in soup(["script", "style", "noscript", "iframe", "svg", "canvas"]):
            tag.decompose()

        if req.onlyMainContent:
            content = (
                soup.find("main")
                or soup.find("article")
                or soup.find(id=re.compile(r"content|main", re.I))
                or soup.find("div", class_=re.compile(r"content|main|body", re.I))
                or soup.body
                or soup
            )
        else:
            content = soup

        md = markdownify.markdownify(str(content), heading_style="ATX", strip=["a", "img"])
        md = re.sub(r"\n{3,}", "\n\n", md).strip()

        title = (soup.title.string or "").strip() if soup.title else ""
        desc_meta = soup.find("meta", {"name": "description"}) or soup.find("meta", {"property": "og:description"})
        description = (desc_meta.get("content") or "").strip() if desc_meta else ""

        return {
            "success": True,
            "data": {
                "markdown": md[:40000],
                "metadata": {
                    "title": title,
                    "description": description,
                    "sourceURL": req.url,
                    "statusCode": resp.status_code,
                },
            },
        }
    except httpx.TimeoutException:
        return {"success": False, "error": "timeout"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/v1/search")
async def search(req: SearchRequest):
    """Search via DuckDuckGo HTML (no API key needed)"""
    try:
        params = urllib.parse.urlencode({
            "q": req.query,
            "kl": f"{req.lang}-{req.country.upper()}",
            "kp": "-2",
        })
        url = f"https://html.duckduckgo.com/html/?{params}"

        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=HEADERS) as client:
            resp = await client.get(url)

        soup = BeautifulSoup(resp.text, "lxml")
        results = []

        for div in soup.find_all("div", class_="result"):
            link = div.find("a", class_="result__a")
            snippet = div.find("a", class_="result__snippet")
            if not link:
                continue
            href = link.get("href", "")
            if "uddg=" in href:
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                href = qs.get("uddg", [href])[0]
            if not href or "duckduckgo.com" in href:
                continue
            results.append({
                "url": href,
                "title": link.get_text(strip=True),
                "description": snippet.get_text(strip=True) if snippet else "",
                "markdown": snippet.get_text(strip=True) if snippet else "",
            })
            if len(results) >= req.limit:
                break

        return {"success": True, "data": results}
    except Exception as e:
        return {"success": False, "error": str(e)}
