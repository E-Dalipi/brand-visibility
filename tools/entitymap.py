"""
Entity Map Generator
====================
Crawls a website via Jina Reader, sends content to Claude to extract entities,
relations, and evidence chunks, then outputs a valid EntityMap v1.0 JSON file.

All API calls happen server-side. No keys are exposed to the browser.
"""

import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

JINA_PREFIX = "https://r.jina.ai/"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
BROWSER_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EntityMapGenerator/1.0)"}
MAX_CHARS_PER_PAGE = 40000
MAX_PAGES = 8  # crawl up to this many pages
PAUSE = 1.0


def _jina_key():
    return os.environ.get("JINA_API_KEY", "")


def _anthropic_key():
    return os.environ.get("ANTHROPIC_API_KEY", "")


def _claude_model():
    return os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")


# ---------------------------------------------------------------------------
# Page crawling
# ---------------------------------------------------------------------------

def fetch_page(url: str) -> str:
    """Read a page through Jina Reader. Returns clean text or ''."""
    headers = dict(BROWSER_HEADERS)
    jina_key = _jina_key()
    if jina_key:
        headers["Authorization"] = f"Bearer {jina_key}"
    try:
        resp = requests.get(JINA_PREFIX + url, headers=headers, timeout=45)
        if resp.status_code == 200 and resp.text.strip():
            return resp.text[:MAX_CHARS_PER_PAGE]
        return ""
    except requests.exceptions.RequestException:
        return ""


def discover_pages(base_url: str, homepage_text: str) -> list[str]:
    """Extract internal links from the homepage text to crawl more pages."""
    parsed = urlparse(base_url)
    domain = parsed.netloc.replace("www.", "")

    # Find URLs in the Jina-rendered text
    urls = re.findall(r'https?://[^\s\)\]\}\"\'<>,]+', homepage_text)
    internal = set()
    for url in urls:
        url = url.rstrip('.').rstrip(')')
        url_parsed = urlparse(url)
        url_domain = url_parsed.netloc.replace("www.", "")
        if url_domain == domain and url_parsed.path not in ("/", ""):
            # Skip common non-content paths
            path = url_parsed.path.lower()
            skip = ("/wp-content/", "/wp-admin/", "/feed", ".xml", ".json",
                    ".png", ".jpg", ".gif", ".css", ".js", "/tag/", "/category/",
                    "/cart", "/checkout", "/my-account", "/login", "/wp-login",
                    "/privacy", "/terms", "/cookie")
            if not any(s in path for s in skip):
                clean = f"{url_parsed.scheme}://{url_parsed.netloc}{url_parsed.path}"
                internal.add(clean.rstrip("/"))

    return list(internal)[:MAX_PAGES - 1]  # -1 because homepage is already fetched


def crawl_site(url: str, progress_cb=None) -> dict:
    """Crawl the homepage + discovered internal pages.
    Returns {"pages": [{"url": ..., "text": ...}, ...], "domain": ...}
    """
    if not url.startswith("http"):
        url = "https://" + url
    url = url.rstrip("/")

    parsed = urlparse(url)
    domain = parsed.netloc.replace("www.", "")

    if progress_cb:
        progress_cb(f"Reading homepage: {url}")

    homepage_text = fetch_page(url)
    if not homepage_text:
        return {"pages": [], "domain": domain, "error": "Could not read the homepage."}

    pages = [{"url": url, "text": homepage_text}]

    # Discover and crawl internal pages
    internal_urls = discover_pages(url, homepage_text)
    if progress_cb:
        progress_cb(f"Found {len(internal_urls)} internal pages to crawl")

    for i, page_url in enumerate(internal_urls):
        time.sleep(PAUSE)
        if progress_cb:
            progress_cb(f"Reading page {i+2}/{len(internal_urls)+1}: {page_url}")
        text = fetch_page(page_url)
        if text:
            pages.append({"url": page_url, "text": text})

    return {"pages": pages, "domain": domain}


# ---------------------------------------------------------------------------
# Entity extraction via Claude
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """You are an entity extraction system for the EntityMap v1.0 standard.
You will receive the content of a website (multiple pages). Your job is to extract:

1. ENTITIES — the key people, organizations, services, concepts, methodologies,
   and proprietary terms this site covers with authority.
2. RELATIONS — how the entities relate to each other using EntityMap predicates.
3. CHUNKS — 1-5 evidence passages per entity, extracted verbatim from the source content.

ENTITY TYPES (use only these):
- Person, Organization, Service, SoftwareProduct, PhysicalProduct, Platform, Place
- Concept (general domain terms — add sameAs Wikidata URL if possible)
- ProprietaryTerm (publisher-coined concepts)
- Methodology (named processes/frameworks)
- Metric (measurable quantities)
- Taxonomy, Event, Standard, Regulation

PREDICATES (Tier 1 — hard, always use):
INSTANCE_OF, PART_OF, INCLUDES, DEPENDS_ON, REQUIRES, MEASURES,
PRODUCED_BY, REGULATED_BY, AUTHORED_BY, AFFILIATED_WITH, COVERS, OFFERS

PREDICATES (Tier 2 — structural):
RELATES_TO, PRECEDES, ENABLES, PREVENTS, CONFLICTS_WITH, DESCRIBED_BY

PREDICATES (Tier 3 — interpretive, MUST include "confidence": "declared" or "inferred"):
IMPROVES, DEGRADES, LEADS_TO, SUITED_FOR, TARGETS, ACHIEVES

RULES:
- Extract 5-12 entities (focus on the most important ones)
- Each entity MUST have 1-3 chunks (evidence passages from the actual content)
- Chunks MUST be extractive (real text from the page), max 500 characters each
- Each chunk MUST include the sourceUrl it came from and the page title
- The "publisher" field on every chunk MUST be exactly: "{publisher_name}"
- Use AFFILIATED_WITH only from Person entities
- Use MEASURES only from Metric entities
- Tier 3 predicates MUST have a "confidence" field
- Prefer specific predicates over RELATES_TO

OUTPUT FORMAT:
Return ONLY a valid JSON object matching this structure (no markdown, no explanation):

{{
  "entities": [
    {{
      "entityId": "e_001",
      "@type": "Person",
      "name": "...",
      "description": "1-3 sentence definition as this publisher uses the concept.",
      "alternateName": "...",  // optional
      "sameAs": "https://www.wikidata.org/wiki/Q...",  // optional, for Concept type
      "relations": [
        {{
          "predicate": "AFFILIATED_WITH",
          "targetId": "e_002",
          "targetName": "..."
        }}
      ],
      "hasChunks": [
        {{
          "chunkId": "c_001",
          "text": "Extractive passage from the actual page content.",
          "sourceUrl": "https://...",
          "pageTitle": "...",
          "publisher": "{publisher_name}",
          "contentType": "definition"  // definition, evidence, example, statistic, procedure
        }}
      ]
    }}
  ]
}}
"""


def extract_entities(pages: list[dict], publisher_name: str, domain: str,
                     progress_cb=None) -> dict:
    """Send crawled pages to Claude for entity extraction."""
    api_key = _anthropic_key()
    if not api_key:
        return {"error": "Anthropic API key not configured."}

    # Build the content block
    pages_text = "\n\n".join(
        f"=== PAGE: {p['url']} ===\n{p['text']}"
        for p in pages
    )

    # Trim if too long (keep within Claude's context)
    if len(pages_text) > 180000:
        pages_text = pages_text[:180000]

    prompt = EXTRACTION_PROMPT.replace("{publisher_name}", publisher_name)

    user_content = f"""{prompt}

PUBLISHER NAME (use exactly this in all chunk publisher fields): {publisher_name}
PUBLISHER DOMAIN: {domain}

============================================================
WEBSITE CONTENT ({len(pages)} pages)
============================================================
{pages_text}
"""

    if progress_cb:
        progress_cb("Extracting entities with Claude...")

    try:
        resp = requests.post(
            ANTHROPIC_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": _claude_model(),
                "max_tokens": 8000,
                "messages": [{"role": "user", "content": user_content}],
            },
            timeout=180,
        )
        if resp.status_code != 200:
            return {"error": f"Claude API error: HTTP {resp.status_code}"}

        text = "".join(b.get("text", "") for b in resp.json().get("content", []))

        # Parse JSON from response (handle markdown code blocks)
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'\s*```$', '', text)

        # Fix common JSON issues from LLMs
        # Remove trailing commas before } or ]
        text = re.sub(r',\s*([}\]])', r'\1', text)
        # Fix single quotes to double quotes (careful approach)
        # Remove any non-JSON text after the closing brace
        brace_depth = 0
        end_pos = len(text)
        for i, ch in enumerate(text):
            if ch == '{':
                brace_depth += 1
            elif ch == '}':
                brace_depth -= 1
                if brace_depth == 0:
                    end_pos = i + 1
                    break
        text = text[:end_pos]

        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            # Last resort: try to extract just the entities array
            match = re.search(r'"entities"\s*:\s*(\[.*\])', text, re.DOTALL)
            if match:
                try:
                    entities_text = re.sub(r',\s*([}\]])', r'\1', match.group(1))
                    entities = json.loads(entities_text)
                    result = {"entities": entities}
                except json.JSONDecodeError as e2:
                    return {"error": f"Could not parse Claude's response as JSON: {e2}"}
            else:
                return {"error": "Could not parse Claude's response as JSON."}

        return result

    except json.JSONDecodeError as e:
        return {"error": f"Could not parse Claude's response as JSON: {e}"}
    except requests.exceptions.RequestException as e:
        return {"error": f"API request failed: {e}"}


# ---------------------------------------------------------------------------
# Assemble the EntityMap
# ---------------------------------------------------------------------------

def build_entitymap(publisher_name: str, publisher_url: str,
                    entities_data: dict) -> dict:
    """Assemble a complete EntityMap v1.0 JSON from extracted entities."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    entities = entities_data.get("entities", [])

    # Add retrieved timestamps and ensure publisher consistency
    for entity in entities:
        for chunk in entity.get("hasChunks", []):
            chunk["publisher"] = publisher_name  # enforce exact match
            if "retrieved" not in chunk:
                chunk["retrieved"] = now

    entitymap = {
        "version": "1.0",
        "schema": "https://entitymap.org/spec/v1.0",
        "publisher": {
            "name": publisher_name,
            "url": publisher_url,
        },
        "generated": now,
        "verificationStatus": "generator-draft",
        "entities": entities,
    }

    return entitymap


def validate_entitymap(entitymap: dict) -> list[str]:
    """Basic validation against the EntityMap v1.0 spec. Returns list of issues."""
    issues = []

    # Root fields
    for field in ("version", "schema", "publisher", "generated", "entities"):
        if field not in entitymap:
            issues.append(f"Missing required root field: {field}")

    publisher = entitymap.get("publisher", {})
    if not publisher.get("name"):
        issues.append("Missing publisher.name")
    if not publisher.get("url"):
        issues.append("Missing publisher.url")

    pub_name = publisher.get("name", "")

    entities = entitymap.get("entities", [])
    if not entities:
        issues.append("Must have at least 1 entity")

    valid_types = {"Concept", "ProprietaryTerm", "Methodology", "Metric", "Taxonomy",
                   "Person", "Organization", "SoftwareProduct", "PhysicalProduct",
                   "Service", "Platform", "Place", "Event", "Standard", "Regulation"}
    tier3 = {"IMPROVES", "DEGRADES", "LEADS_TO", "SUITED_FOR", "TARGETS", "ACHIEVES"}

    for entity in entities:
        eid = entity.get("entityId", "?")
        for field in ("entityId", "@type", "name", "description", "hasChunks"):
            if field not in entity:
                issues.append(f"Entity {eid}: missing required field '{field}'")

        etype = entity.get("@type", "")
        if etype and etype not in valid_types and ":" not in etype:
            issues.append(f"Entity {eid}: invalid type '{etype}'")

        chunks = entity.get("hasChunks", [])
        if not chunks:
            issues.append(f"Entity {eid}: must have at least 1 chunk")
        if len(chunks) > 5:
            issues.append(f"Entity {eid}: max 5 chunks allowed, has {len(chunks)}")

        for chunk in chunks:
            for field in ("chunkId", "text", "sourceUrl", "pageTitle", "publisher"):
                if field not in chunk:
                    issues.append(f"Entity {eid}, chunk: missing '{field}'")
            if chunk.get("publisher") != pub_name:
                issues.append(f"Entity {eid}, chunk {chunk.get('chunkId', '?')}: "
                              f"publisher mismatch ('{chunk.get('publisher')}' vs '{pub_name}')")
            if len(chunk.get("text", "")) > 600:
                issues.append(f"Entity {eid}, chunk {chunk.get('chunkId', '?')}: "
                              f"text exceeds 600 characters")

        for rel in entity.get("relations", []):
            pred = rel.get("predicate", "")
            if not pred:
                issues.append(f"Entity {eid}: relation missing predicate")
            if not rel.get("targetName"):
                issues.append(f"Entity {eid}: relation missing targetName")
            if pred in tier3 and "confidence" not in rel:
                issues.append(f"Entity {eid}: Tier 3 predicate '{pred}' requires 'confidence' field")
            if pred == "AFFILIATED_WITH" and etype != "Person":
                issues.append(f"Entity {eid}: AFFILIATED_WITH requires Person source, got {etype}")
            if pred == "MEASURES" and etype != "Metric":
                issues.append(f"Entity {eid}: MEASURES requires Metric source, got {etype}")

    return issues


# ---------------------------------------------------------------------------
# Main generation pipeline
# ---------------------------------------------------------------------------

def generate_entitymap(url: str, publisher_name: str = None,
                       progress_cb=None) -> dict:
    """Full pipeline: crawl → extract → assemble → validate.

    Returns {
        "entitymap": {...},       # the valid EntityMap JSON
        "validation": [...],      # list of issues (empty if valid)
        "stats": {...},           # pages crawled, entities found, etc.
        "error": "..." or None
    }
    """
    if not url.startswith("http"):
        url = "https://" + url
    url = url.rstrip("/")

    parsed = urlparse(url)
    domain = parsed.netloc.replace("www.", "")
    publisher_url = f"{parsed.scheme}://{parsed.netloc}"

    if not publisher_name:
        # Use domain as fallback
        publisher_name = domain.split(".")[0].title()

    # Step 1: Crawl
    crawl_result = crawl_site(url, progress_cb=progress_cb)
    if crawl_result.get("error"):
        return {"entitymap": None, "error": crawl_result["error"],
                "validation": [], "stats": {}}

    pages = crawl_result["pages"]
    if not pages:
        return {"entitymap": None, "error": "No pages could be read.",
                "validation": [], "stats": {}}

    # Step 2: Extract entities
    entities_data = extract_entities(pages, publisher_name, domain,
                                     progress_cb=progress_cb)
    if entities_data.get("error"):
        return {"entitymap": None, "error": entities_data["error"],
                "validation": [], "stats": {}}

    # Step 3: Assemble
    if progress_cb:
        progress_cb("Assembling EntityMap...")
    entitymap = build_entitymap(publisher_name, publisher_url, entities_data)

    # Step 4: Validate
    issues = validate_entitymap(entitymap)

    stats = {
        "pages_crawled": len(pages),
        "entities_found": len(entitymap.get("entities", [])),
        "total_chunks": sum(len(e.get("hasChunks", []))
                           for e in entitymap.get("entities", [])),
        "total_relations": sum(len(e.get("relations", []))
                              for e in entitymap.get("entities", [])),
        "validation_issues": len(issues),
    }

    if progress_cb:
        progress_cb(f"Done! {stats['entities_found']} entities, "
                    f"{stats['total_chunks']} chunks, "
                    f"{stats['total_relations']} relations")

    return {
        "entitymap": entitymap,
        "validation": issues,
        "stats": stats,
        "error": None,
    }
