"""
Commodity Content Audit — Server-side version
==============================================
Runs the same audit as the CLI tool but as a backend function.
No API keys touch the browser.
"""

import json
import os
import re
import time
from urllib.parse import urlparse

import requests

JINA_PREFIX = "https://r.jina.ai/"
SERPAPI_URL = "https://serpapi.com/search.json"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
BROWSER_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ContentAudit/1.0)"}
MAX_CHARS_PER_PAGE = 45000
TOP_N = 5
PAUSE = 1.0

MARKETS = {
    "uk": ("uk", "google.co.uk"), "us": ("us", "google.com"),
    "ie": ("ie", "google.ie"), "de": ("de", "google.de"),
    "fr": ("fr", "google.fr"), "es": ("es", "google.es"),
    "it": ("it", "google.it"), "nl": ("nl", "google.nl"),
    "se": ("se", "google.se"), "ca": ("ca", "google.ca"),
    "au": ("au", "google.com.au"),
}

RUBRIC = """
You are auditing a web page to judge whether its content is COMMODITY or
NON-COMMODITY. Commodity content can be reproduced from public information
alone. Non-commodity content cannot.

THE CORE QUESTION, asked of every section:
Could a competitor publish a near-identical version of this section using only
public information, without access to the author's own experience, data, or
original research?
  - If yes -> commodity
  - If no -> non-commodity (but it must prove why)

A section is NON-COMMODITY only if it contains one or more of these, AND the
element traces to a real source:
  1. A factual correction of what the AI engines or ranking pages currently state.
  2. A disambiguation of a genuine confusion no ranking page or AI answer resolves.
  3. Regulatory, standards, or technical context the competition skips.
  4. Practitioner experience from a source no public page has.
  5. A specific reframing for a particular audience, location, or situation.
  6. An original editorial judgment grounded in evidence.

A section is COMMODITY if it is reproducible from public information.

THE HONESTY CATCH: Clearer prose is NOT information gain. Non-commodity labels
require the differentiating element traces to the author's own input, primary
research, or an authority source no competitor cites.

THRESHOLD: A genuinely differentiated page has at least half its body sections
non-commodity. Below half = commodity-heavy.
"""

OUTPUT_INSTRUCTIONS = """
Return your audit as a JSON object with this exact structure:

{
  "verdict": "differentiated | borderline | commodity-heavy",
  "ratio": "X of Y sections non-commodity",
  "percentage": 38,
  "sections": [
    {
      "name": "Section heading or label",
      "status": "commodity | non-commodity",
      "reasoning": "One line explanation"
    }
  ],
  "recommendations": [
    {
      "title": "What to add",
      "description": "Specific content to create",
      "source": "Where the material comes from"
    }
  ]
}

Return ONLY valid JSON. No markdown, no explanation outside the JSON.
"""


def _fetch_page(url):
    headers = dict(BROWSER_HEADERS)
    jina_key = os.environ.get("JINA_API_KEY", "")
    if jina_key:
        headers["Authorization"] = f"Bearer {jina_key}"
    try:
        resp = requests.get(JINA_PREFIX + url, headers=headers, timeout=45)
        if resp.status_code == 200 and resp.text.strip():
            return resp.text[:MAX_CHARS_PER_PAGE]
        return ""
    except requests.exceptions.RequestException:
        return ""


def _serp_top_links(keyword, gl, google_domain):
    api_key = os.environ.get("SERPAPI_KEY", "")
    if not api_key:
        return []
    params = {"engine": "google", "q": keyword, "gl": gl, "hl": "en",
              "google_domain": google_domain, "api_key": api_key}
    try:
        resp = requests.get(SERPAPI_URL, params=params, timeout=30)
        if resp.status_code != 200:
            return []
        return [r["link"] for r in resp.json().get("organic_results", []) if r.get("link")]
    except requests.exceptions.RequestException:
        return []


def _get_perplexity_answer(keyword):
    api_key = os.environ.get("PERPLEXITY_API_KEY", "")
    if not api_key:
        return ""
    try:
        resp = requests.post(PERPLEXITY_URL,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json={"model": "sonar-pro",
                  "messages": [{"role": "user", "content": keyword}]},
            timeout=45)
        if resp.status_code != 200:
            return ""
        choices = resp.json().get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "")[:3000]
        return ""
    except requests.exceptions.RequestException:
        return ""


def run_audit(url: str, keyword: str, market: str = "us",
              progress_cb=None) -> dict:
    """Run a commodity content audit. Returns structured results.

    Returns {
        "verdict": "...",
        "percentage": int,
        "sections": [...],
        "recommendations": [...],
        "stats": {...},
        "error": str or None
    }
    """
    if not url.startswith("http"):
        url = "https://" + url

    gl, google_domain = MARKETS.get(market.lower(), (market.lower(), "google.com"))

    # Step 1: Find ranking pages
    if progress_cb:
        progress_cb(f"Finding pages ranking for \"{keyword}\"...")

    links = _serp_top_links(keyword, gl, google_domain)
    your_domain = urlparse(url).netloc.replace("www.", "")
    ranker_urls = [l for l in links
                   if urlparse(l).netloc.replace("www.", "") != your_domain][:TOP_N]

    # Step 2: Get AI answer
    if progress_cb:
        progress_cb("Checking what AI already says...")
    ppx = _get_perplexity_answer(keyword)
    ai_answers = [("Perplexity", ppx)] if ppx else []

    # Step 3: Read pages
    if progress_cb:
        progress_cb("Reading your page...")
    your_text = _fetch_page(url)
    if not your_text:
        return {"error": "Could not read the page. Check the URL and try again.",
                "verdict": None, "sections": [], "recommendations": [], "stats": {}}

    ranker_texts = []
    for i, rurl in enumerate(ranker_urls):
        if progress_cb:
            progress_cb(f"Reading ranking page {i+1}/{len(ranker_urls)}...")
        txt = _fetch_page(rurl)
        if txt:
            ranker_texts.append((rurl, txt))
        time.sleep(PAUSE)

    # Step 4: Judge with Claude
    if progress_cb:
        progress_cb("Analyzing against the commodity rubric...")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"error": "Anthropic API key not configured.",
                "verdict": None, "sections": [], "recommendations": [], "stats": {}}

    rankers_block = "\n\n".join(
        f"--- RANKING PAGE {i+1}: {rurl} ---\n{txt}"
        for i, (rurl, txt) in enumerate(ranker_texts)
    ) or "(no ranking pages could be read)"

    ai_block = "\n\n".join(
        f"--- {name} ---\n{txt}" for name, txt in ai_answers if txt
    ) or "(AI answers not available)"

    user_content = f"""{RUBRIC}

TARGET KEYWORD: {keyword}
MARKET: {market.upper()}

============================================================
THE PAGE TO AUDIT: {url}
============================================================
{your_text}

============================================================
RANKING PAGES (reference point 1)
============================================================
{rankers_block}

============================================================
AI ANSWERS (reference point 2)
============================================================
{ai_block}

{OUTPUT_INSTRUCTIONS}"""

    try:
        resp = requests.post(
            ANTHROPIC_URL,
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6"),
                  "max_tokens": 3000,
                  "messages": [{"role": "user", "content": user_content}]},
            timeout=180,
        )
        if resp.status_code != 200:
            return {"error": f"Claude API error: HTTP {resp.status_code}",
                    "verdict": None, "sections": [], "recommendations": [], "stats": {}}

        text = "".join(b.get("text", "") for b in resp.json().get("content", []))

        # Parse JSON
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'\s*```$', '', text)

        # Fix trailing commas
        text = re.sub(r',\s*([}\]])', r'\1', text)

        # Truncate after the top-level closing brace
        brace_depth = 0
        for i, ch in enumerate(text):
            if ch == '{':
                brace_depth += 1
            elif ch == '}':
                brace_depth -= 1
                if brace_depth == 0:
                    text = text[:i + 1]
                    break

        result = json.loads(text)

        result["stats"] = {
            "ranking_pages_read": len(ranker_texts),
            "ranking_pages_found": len(ranker_urls),
            "ai_answers_used": sum(1 for _, a in ai_answers if a),
        }
        result["error"] = None

        return result

    except json.JSONDecodeError:
        return {"error": "Could not parse the audit results.",
                "verdict": None, "sections": [], "recommendations": [], "stats": {}}
    except requests.exceptions.RequestException as e:
        return {"error": f"API request failed: {e}",
                "verdict": None, "sections": [], "recommendations": [], "stats": {}}
