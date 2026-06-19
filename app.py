"""
Brand Visibility Tracker — Web Interface
=========================================
A polished dashboard to track how AI engines mention your brand.

Run locally:   python app.py
Deploy:        gunicorn app:app
"""

import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse

import requests
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash

# Load .env file if present
def _load_dotenv():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ.setdefault(key.strip(), val.strip())

_load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "brand-vis-dev-key-change-me")

# ===========================================================================
# Configuration — set via environment variables or defaults
# ===========================================================================
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

DB_PATH = os.environ.get("DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "brand_visibility.db"))

PAUSE = 1.5

# API config
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_URL = "https://api.openai.com/v1/responses"
PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

CLAUDE_MODEL = "claude-sonnet-4-6"
OPENAI_MODEL = "gpt-4.1-mini"
PERPLEXITY_MODEL = "sonar-pro"

# Track background run status
run_status = {"running": False, "progress": "", "total": 0, "done": 0}


# ===========================================================================
# Database
# ===========================================================================

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    _create_tables(db)
    return db


def _create_tables(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            domain TEXT NOT NULL,
            competitors TEXT DEFAULT '[]',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS prompts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES projects(id),
            prompt_string TEXT NOT NULL,
            models TEXT DEFAULT '["chatgpt","claude","gemini","perplexity"]',
            frequency TEXT DEFAULT 'weekly',
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt_id INTEGER NOT NULL REFERENCES prompts(id),
            model TEXT NOT NULL,
            response_text TEXT,
            brand_mentioned INTEGER DEFAULT 0,
            competitor_mentions TEXT DEFAULT '{}',
            citations TEXT DEFAULT '[]',
            error TEXT,
            run_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_runs_prompt_date ON runs(prompt_id, run_at DESC);
        CREATE INDEX IF NOT EXISTS idx_runs_model_date ON runs(model, run_at DESC);
    """)
    db.commit()


# ===========================================================================
# AI API calls
# ===========================================================================

def query_chatgpt(prompt):
    if not OPENAI_API_KEY:
        return "", "no API key"
    try:
        resp = requests.post(OPENAI_URL,
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": OPENAI_MODEL,
                  "tools": [{"type": "web_search_preview"}],
                  "input": prompt}, timeout=90)
        if resp.status_code != 200:
            return "", f"HTTP {resp.status_code}"
        text = ""
        for block in resp.json().get("output", []):
            if isinstance(block, dict) and block.get("type") == "message":
                for content in block.get("content", []):
                    if isinstance(content, dict) and content.get("type") == "output_text":
                        text += content.get("text", "")
        return (text, "") if text else ("", "empty response")
    except requests.exceptions.RequestException as e:
        return "", str(e)


def query_claude(prompt):
    if not ANTHROPIC_API_KEY:
        return "", "no API key"
    try:
        resp = requests.post(ANTHROPIC_URL,
            headers={"x-api-key": ANTHROPIC_API_KEY,
                     "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": CLAUDE_MODEL, "max_tokens": 2000,
                  "messages": [{"role": "user", "content": prompt}]}, timeout=90)
        if resp.status_code != 200:
            return "", f"HTTP {resp.status_code}"
        text = "".join(b.get("text", "") for b in resp.json().get("content", []))
        return (text, "") if text else ("", "empty response")
    except requests.exceptions.RequestException as e:
        return "", str(e)


def query_gemini(prompt):
    if not GEMINI_API_KEY:
        return "", "no API key"
    try:
        resp = requests.post(f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=90)
        if resp.status_code != 200:
            return "", f"HTTP {resp.status_code}"
        candidates = resp.json().get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            text = " ".join(p.get("text", "") for p in parts)
            return (text, "") if text else ("", "empty response")
        return "", "no candidates"
    except requests.exceptions.RequestException as e:
        return "", str(e)


def query_perplexity(prompt):
    if not PERPLEXITY_API_KEY:
        return "", "no API key"
    try:
        resp = requests.post(PERPLEXITY_URL,
            headers={"Authorization": f"Bearer {PERPLEXITY_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": PERPLEXITY_MODEL,
                  "messages": [{"role": "user", "content": prompt}]}, timeout=90)
        if resp.status_code != 200:
            return "", f"HTTP {resp.status_code}"
        choices = resp.json().get("choices", [])
        if choices:
            text = choices[0].get("message", {}).get("content", "")
            return (text, "") if text else ("", "empty response")
        return "", "no choices"
    except requests.exceptions.RequestException as e:
        return "", str(e)


MODEL_FUNCTIONS = {
    "chatgpt": query_chatgpt,
    "claude": query_claude,
    "gemini": query_gemini,
    "perplexity": query_perplexity,
}


# ===========================================================================
# Mention / citation parsing
# ===========================================================================

def check_brand_mentioned(text, brand_name, domain):
    text_lower = text.lower()
    if re.search(r'\b' + re.escape(brand_name.lower()) + r'\b', text_lower):
        return True
    domain_clean = domain.lower().replace("www.", "")
    if domain_clean in text_lower:
        return True
    return False


def count_competitor_mentions(text, competitors):
    text_lower = text.lower()
    mentions = {}
    for comp in competitors:
        name = comp.get("name", "")
        domain = comp.get("domain", "").replace("www.", "")
        found = False
        if name and re.search(r'\b' + re.escape(name.lower()) + r'\b', text_lower):
            found = True
        if domain and domain.lower() in text_lower:
            found = True
        if found:
            label = name or domain
            mentions[label] = mentions.get(label, 0) + 1
    return mentions


def extract_citations(text):
    urls = re.findall(r'https?://[^\s\)\]\}\"\'<>,]+', text)
    cleaned = []
    for url in urls:
        url = url.rstrip('.')
        if len(url) > 10:
            cleaned.append(url)
    return list(set(cleaned))


# ===========================================================================
# Background run
# ===========================================================================

def _run_all_background(project_id=None):
    global run_status
    run_status = {"running": True, "progress": "Starting...", "total": 0, "done": 0}

    db = get_db()
    try:
        if project_id:
            prompts_list = db.execute(
                "SELECT * FROM prompts WHERE active=1 AND project_id=?",
                (project_id,)).fetchall()
        else:
            prompts_list = db.execute(
                "SELECT * FROM prompts WHERE active=1").fetchall()

        if not prompts_list:
            run_status = {"running": False, "progress": "No active prompts.", "total": 0, "done": 0}
            return

        # Count total model calls
        total = sum(len(json.loads(p["models"])) for p in prompts_list)
        run_status["total"] = total

        projects_cache = {}
        done_count = 0

        for prompt in prompts_list:
            pid = prompt["project_id"]
            if pid not in projects_cache:
                projects_cache[pid] = db.execute(
                    "SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
            project = projects_cache[pid]
            if not project:
                continue

            models = json.loads(prompt["models"])
            brand_name = project["name"]
            domain = project["domain"]
            competitors = json.loads(project["competitors"])

            for model in models:
                run_status["progress"] = f"Querying {model} for \"{prompt['prompt_string'][:40]}...\""
                query_fn = MODEL_FUNCTIONS.get(model)
                if not query_fn:
                    done_count += 1
                    run_status["done"] = done_count
                    continue

                text, error = query_fn(prompt["prompt_string"])
                time.sleep(PAUSE)

                if error:
                    db.execute(
                        "INSERT INTO runs (prompt_id, model, error) VALUES (?, ?, ?)",
                        (prompt["id"], model, error))
                else:
                    mentioned = check_brand_mentioned(text, brand_name, domain)
                    comp_mentions = count_competitor_mentions(text, competitors)
                    citations = extract_citations(text)
                    db.execute("""
                        INSERT INTO runs (prompt_id, model, response_text, brand_mentioned,
                                          competitor_mentions, citations)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (prompt["id"], model, text, int(mentioned),
                          json.dumps(comp_mentions), json.dumps(citations)))
                db.commit()

                done_count += 1
                run_status["done"] = done_count

        run_status = {"running": False, "progress": f"Complete. Ran {len(prompts_list)} prompts.",
                      "total": total, "done": total}
    except Exception as e:
        run_status = {"running": False, "progress": f"Error: {e}", "total": 0, "done": 0}
    finally:
        db.close()


# ===========================================================================
# Data helpers for templates
# ===========================================================================

def get_dashboard_data(db, project_id, days=7):
    """Compute all dashboard metrics."""
    project = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not project:
        return None

    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    prev_cutoff = (datetime.utcnow() - timedelta(days=days * 2)).isoformat()
    competitors = json.loads(project["competitors"])
    models = ["chatgpt", "claude", "gemini", "perplexity"]

    # Mentions by model
    model_data = []
    total_current = 0
    total_previous = 0
    for model in models:
        current = db.execute("""
            SELECT COUNT(*) FROM runs r JOIN prompts p ON r.prompt_id = p.id
            WHERE p.project_id=? AND r.model=? AND r.brand_mentioned=1
            AND r.run_at >= ? AND r.error IS NULL
        """, (project_id, model, cutoff)).fetchone()[0]
        previous = db.execute("""
            SELECT COUNT(*) FROM runs r JOIN prompts p ON r.prompt_id = p.id
            WHERE p.project_id=? AND r.model=? AND r.brand_mentioned=1
            AND r.run_at >= ? AND r.run_at < ? AND r.error IS NULL
        """, (project_id, model, prev_cutoff, cutoff)).fetchone()[0]
        model_data.append({"name": model, "current": current, "previous": previous,
                           "delta": current - previous})
        total_current += current
        total_previous += previous

    # Share of voice
    total_runs = db.execute("""
        SELECT COUNT(*) FROM runs r JOIN prompts p ON r.prompt_id = p.id
        WHERE p.project_id=? AND r.run_at >= ? AND r.error IS NULL
    """, (project_id, cutoff)).fetchone()[0]

    brand_mentions = db.execute("""
        SELECT COUNT(*) FROM runs r JOIN prompts p ON r.prompt_id = p.id
        WHERE p.project_id=? AND r.brand_mentioned=1 AND r.run_at >= ? AND r.error IS NULL
    """, (project_id, cutoff)).fetchone()[0]

    sov = [{"name": project["name"], "mentions": brand_mentions, "is_brand": True,
            "pct": round(brand_mentions / total_runs * 100, 1) if total_runs else 0}]

    comp_totals = {}
    rows = db.execute("""
        SELECT competitor_mentions FROM runs r JOIN prompts p ON r.prompt_id = p.id
        WHERE p.project_id=? AND r.run_at >= ? AND r.error IS NULL
    """, (project_id, cutoff)).fetchall()
    for row in rows:
        cm = json.loads(row["competitor_mentions"])
        for name, count in cm.items():
            comp_totals[name] = comp_totals.get(name, 0) + count
    for comp_name, count in sorted(comp_totals.items(), key=lambda x: x[1], reverse=True):
        sov.append({"name": comp_name, "mentions": count, "is_brand": False,
                     "pct": round(count / total_runs * 100, 1) if total_runs else 0})

    # Weekly trend (8 weeks)
    now = datetime.utcnow()
    trend_labels = []
    trend_brand = []
    trend_total = []
    for w in range(7, -1, -1):
        week_end = now - timedelta(weeks=w)
        week_start = week_end - timedelta(weeks=1)
        trend_labels.append(week_start.strftime("%b %d"))
        mentioned = db.execute("""
            SELECT COUNT(*) FROM runs r JOIN prompts p ON r.prompt_id = p.id
            WHERE p.project_id=? AND r.brand_mentioned=1
            AND r.run_at >= ? AND r.run_at < ? AND r.error IS NULL
        """, (project_id, week_start.isoformat(), week_end.isoformat())).fetchone()[0]
        total_week = db.execute("""
            SELECT COUNT(*) FROM runs r JOIN prompts p ON r.prompt_id = p.id
            WHERE p.project_id=? AND r.run_at >= ? AND r.run_at < ? AND r.error IS NULL
        """, (project_id, week_start.isoformat(), week_end.isoformat())).fetchone()[0]
        trend_brand.append(mentioned)
        trend_total.append(total_week)

    # Per-prompt breakdown
    prompt_data = []
    prompts_list = db.execute("SELECT * FROM prompts WHERE project_id=? AND active=1",
                              (project_id,)).fetchall()
    for prompt in prompts_list:
        p_models = []
        for model in models:
            total = db.execute("""
                SELECT COUNT(*) FROM runs WHERE prompt_id=? AND model=?
                AND run_at >= ? AND error IS NULL
            """, (prompt["id"], model, cutoff)).fetchone()[0]
            mentioned = db.execute("""
                SELECT COUNT(*) FROM runs WHERE prompt_id=? AND model=?
                AND brand_mentioned=1 AND run_at >= ? AND error IS NULL
            """, (prompt["id"], model, cutoff)).fetchone()[0]
            p_models.append({"model": model, "total": total, "mentioned": mentioned,
                             "pct": round(mentioned / total * 100) if total else None})
        prompt_data.append({"id": prompt["id"], "text": prompt["prompt_string"],
                            "models": p_models})

    # Citation domains
    cit_rows = db.execute("""
        SELECT citations FROM runs r JOIN prompts p ON r.prompt_id = p.id
        WHERE p.project_id=? AND r.run_at >= ? AND r.error IS NULL AND r.citations != '[]'
    """, (project_id, cutoff)).fetchall()
    domain_counts = {}
    my_domain = project["domain"].replace("www.", "").lower()
    for row in cit_rows:
        for url in json.loads(row["citations"]):
            d = urlparse(url).netloc.replace("www.", "").lower()
            domain_counts[d] = domain_counts.get(d, 0) + 1
    top_citations = sorted(domain_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    # Total stats
    total_all_time = db.execute("""
        SELECT COUNT(*) FROM runs r JOIN prompts p ON r.prompt_id = p.id
        WHERE p.project_id=? AND r.error IS NULL
    """, (project_id,)).fetchone()[0]
    total_mentioned_all = db.execute("""
        SELECT COUNT(*) FROM runs r JOIN prompts p ON r.prompt_id = p.id
        WHERE p.project_id=? AND r.brand_mentioned=1 AND r.error IS NULL
    """, (project_id,)).fetchone()[0]

    return {
        "project": dict(project),
        "days": days,
        "models": model_data,
        "total_current": total_current,
        "total_previous": total_previous,
        "sov": sov,
        "total_runs": total_runs,
        "trend_labels": json.dumps(trend_labels),
        "trend_brand": json.dumps(trend_brand),
        "trend_total": json.dumps(trend_total),
        "prompts": prompt_data,
        "top_citations": top_citations,
        "my_domain": my_domain,
        "total_all_time": total_all_time,
        "total_mentioned_all": total_mentioned_all,
        "mention_rate": round(total_mentioned_all / total_all_time * 100, 1) if total_all_time else 0,
    }


# ===========================================================================
# Routes
# ===========================================================================

@app.route("/")
def index():
    db = get_db()
    projects = db.execute("SELECT * FROM projects ORDER BY id").fetchall()
    db.close()
    if not projects:
        return redirect(url_for("new_project"))
    if len(projects) == 1:
        return redirect(url_for("dashboard", project_id=projects[0]["id"]))
    return render_template("index.html", projects=projects)


@app.route("/dashboard/<int:project_id>")
def dashboard(project_id):
    days = request.args.get("days", 7, type=int)
    db = get_db()
    data = get_dashboard_data(db, project_id, days)
    projects = db.execute("SELECT * FROM projects ORDER BY id").fetchall()
    db.close()
    if not data:
        flash("Project not found.", "error")
        return redirect(url_for("index"))
    return render_template("dashboard.html", d=data, projects=projects, run_status=run_status)


@app.route("/projects/new", methods=["GET", "POST"])
def new_project():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        domain = request.form.get("domain", "").strip().replace("https://", "").replace("http://", "").rstrip("/")
        comp_names = request.form.getlist("comp_name[]")
        comp_domains = request.form.getlist("comp_domain[]")
        competitors = []
        for cn, cd in zip(comp_names, comp_domains):
            cn, cd = cn.strip(), cd.strip().replace("https://", "").replace("http://", "").rstrip("/")
            if cn or cd:
                competitors.append({"name": cn, "domain": cd})
        if not name or not domain:
            flash("Name and domain are required.", "error")
            return render_template("project_form.html", projects=[])
        db = get_db()
        db.execute("INSERT INTO projects (name, domain, competitors) VALUES (?, ?, ?)",
                   (name, domain, json.dumps(competitors)))
        db.commit()
        pid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.close()
        flash(f"Project \"{name}\" created.", "success")
        return redirect(url_for("dashboard", project_id=pid))
    db = get_db()
    projects = db.execute("SELECT * FROM projects ORDER BY id").fetchall()
    db.close()
    return render_template("project_form.html", projects=projects)


@app.route("/prompts/<int:project_id>")
def prompts_page(project_id):
    db = get_db()
    project = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    prompts_list = db.execute(
        "SELECT * FROM prompts WHERE project_id=? ORDER BY active DESC, id",
        (project_id,)).fetchall()
    # Enrich with run counts
    enriched = []
    for p in prompts_list:
        total = db.execute("SELECT COUNT(*) FROM runs WHERE prompt_id=? AND error IS NULL",
                           (p["id"],)).fetchone()[0]
        enriched.append({**dict(p), "models_list": json.loads(p["models"]), "run_count": total})
    projects = db.execute("SELECT * FROM projects ORDER BY id").fetchall()
    db.close()
    return render_template("prompts.html", project=project, prompts=enriched, projects=projects)


@app.route("/prompts/<int:project_id>/add", methods=["POST"])
def add_prompt(project_id):
    prompt_string = request.form.get("prompt_string", "").strip()
    models = request.form.getlist("models[]")
    frequency = request.form.get("frequency", "weekly")
    if not prompt_string:
        flash("Prompt text is required.", "error")
        return redirect(url_for("prompts_page", project_id=project_id))
    if not models:
        models = ["chatgpt", "claude", "gemini", "perplexity"]
    db = get_db()
    db.execute("INSERT INTO prompts (project_id, prompt_string, models, frequency) VALUES (?, ?, ?, ?)",
               (project_id, prompt_string, json.dumps(models), frequency))
    db.commit()
    db.close()
    flash("Prompt added.", "success")
    return redirect(url_for("prompts_page", project_id=project_id))


@app.route("/prompts/toggle/<int:prompt_id>", methods=["POST"])
def toggle_prompt(prompt_id):
    db = get_db()
    prompt = db.execute("SELECT * FROM prompts WHERE id=?", (prompt_id,)).fetchone()
    if prompt:
        new_state = 0 if prompt["active"] else 1
        db.execute("UPDATE prompts SET active=? WHERE id=?", (new_state, prompt_id))
        db.commit()
        flash(f"Prompt {'resumed' if new_state else 'paused'}.", "success")
        pid = prompt["project_id"]
    else:
        pid = 1
    db.close()
    return redirect(url_for("prompts_page", project_id=pid))


@app.route("/prompts/delete/<int:prompt_id>", methods=["POST"])
def delete_prompt(prompt_id):
    db = get_db()
    prompt = db.execute("SELECT * FROM prompts WHERE id=?", (prompt_id,)).fetchone()
    pid = prompt["project_id"] if prompt else 1
    db.execute("DELETE FROM runs WHERE prompt_id=?", (prompt_id,))
    db.execute("DELETE FROM prompts WHERE id=?", (prompt_id,))
    db.commit()
    db.close()
    flash("Prompt deleted.", "success")
    return redirect(url_for("prompts_page", project_id=pid))


@app.route("/responses/<int:prompt_id>")
def responses_page(prompt_id):
    db = get_db()
    prompt = db.execute("SELECT * FROM prompts WHERE id=?", (prompt_id,)).fetchone()
    if not prompt:
        flash("Prompt not found.", "error")
        return redirect(url_for("index"))
    project = db.execute("SELECT * FROM projects WHERE id=?", (prompt["project_id"],)).fetchone()
    rows = db.execute("""
        SELECT * FROM runs WHERE prompt_id=? AND error IS NULL
        ORDER BY run_at DESC LIMIT 20
    """, (prompt_id,)).fetchall()
    responses = []
    for r in rows:
        responses.append({
            **dict(r),
            "citations_list": json.loads(r["citations"]),
            "comp_mentions": json.loads(r["competitor_mentions"]),
        })
    projects = db.execute("SELECT * FROM projects ORDER BY id").fetchall()
    db.close()
    return render_template("responses.html", prompt=prompt, project=project,
                           responses=responses, projects=projects)


@app.route("/run/<int:project_id>", methods=["POST"])
def trigger_run(project_id):
    if run_status["running"]:
        flash("A run is already in progress.", "warning")
        return redirect(url_for("dashboard", project_id=project_id))
    thread = threading.Thread(target=_run_all_background, args=(project_id,), daemon=True)
    thread.start()
    flash("Run started in background. Refresh to see progress.", "success")
    return redirect(url_for("dashboard", project_id=project_id))


@app.route("/api/run-status")
def api_run_status():
    return jsonify(run_status)


@app.route("/settings", methods=["GET", "POST"])
def settings():
    db = get_db()
    projects = db.execute("SELECT * FROM projects ORDER BY id").fetchall()
    db.close()
    keys = {
        "anthropic": bool(ANTHROPIC_API_KEY),
        "openai": bool(OPENAI_API_KEY),
        "perplexity": bool(PERPLEXITY_API_KEY),
        "gemini": bool(GEMINI_API_KEY),
    }
    return render_template("settings.html", keys=keys, projects=projects)


# ===========================================================================
# PUBLIC TOOLS — Entity Map Generator + Commodity Audit
# These are the client-facing tools. API keys stay server-side.
# ===========================================================================

from tools.entitymap import generate_entitymap
from tools.audit import run_audit
from tools.limiter import rate_limit, record_request, check_rate_limit

# Background task tracking for public tools
tool_jobs = {}  # {job_id: {"status": ..., "progress": ..., "result": ...}}


@app.route("/tools")
def tools_landing():
    return render_template("tools_landing.html")


# --- Entity Map Generator ---

@app.route("/tools/entitymap")
def entitymap_page():
    return render_template("tool_entitymap.html")


@app.route("/api/entitymap/generate", methods=["POST"])
def api_entitymap_generate():
    allowed, remaining = check_rate_limit("entitymap", limit=3, window=86400)
    if not allowed:
        return jsonify({"error": "Daily limit reached (3 per day). "
                        "Enter your email to get unlimited access.",
                        "requires_email": True}), 429

    data = request.get_json() or {}
    url = data.get("url", "").strip()
    publisher_name = data.get("publisher_name", "").strip()
    email = data.get("email", "").strip()

    if not url:
        return jsonify({"error": "URL is required."}), 400

    import uuid
    job_id = str(uuid.uuid4())[:8]
    tool_jobs[job_id] = {"status": "running", "progress": "Starting...",
                         "result": None, "tool": "entitymap"}

    def run_job():
        def progress(msg):
            tool_jobs[job_id]["progress"] = msg

        result = generate_entitymap(url, publisher_name or None,
                                    progress_cb=progress)
        tool_jobs[job_id]["status"] = "done"
        tool_jobs[job_id]["result"] = result

        # Log the lead if email provided
        if email:
            _log_lead(email, "entitymap", url)

    record_request("entitymap")
    thread = threading.Thread(target=run_job, daemon=True)
    thread.start()

    return jsonify({"job_id": job_id, "remaining": remaining - 1})


# --- Commodity Audit ---

@app.route("/tools/audit")
def audit_page():
    return render_template("tool_audit.html")


@app.route("/api/audit/run", methods=["POST"])
def api_audit_run():
    allowed, remaining = check_rate_limit("audit", limit=3, window=86400)
    if not allowed:
        return jsonify({"error": "Daily limit reached (3 per day). "
                        "Enter your email to get unlimited access.",
                        "requires_email": True}), 429

    data = request.get_json() or {}
    url = data.get("url", "").strip()
    keyword = data.get("keyword", "").strip()
    market = data.get("market", "us").strip()
    email = data.get("email", "").strip()

    if not url or not keyword:
        return jsonify({"error": "URL and keyword are required."}), 400

    import uuid
    job_id = str(uuid.uuid4())[:8]
    tool_jobs[job_id] = {"status": "running", "progress": "Starting...",
                         "result": None, "tool": "audit"}

    def run_job():
        def progress(msg):
            tool_jobs[job_id]["progress"] = msg

        result = run_audit(url, keyword, market, progress_cb=progress)
        tool_jobs[job_id]["status"] = "done"
        tool_jobs[job_id]["result"] = result

        if email:
            _log_lead(email, "audit", url)

    record_request("audit")
    thread = threading.Thread(target=run_job, daemon=True)
    thread.start()

    return jsonify({"job_id": job_id, "remaining": remaining - 1})


# --- Shared: job status + lead logging ---

@app.route("/api/job/<job_id>")
def api_job_status(job_id):
    job = tool_jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if job["status"] == "running":
        return jsonify({"status": "running", "progress": job["progress"]})
    return jsonify({"status": "done", "result": job["result"]})


def _log_lead(email, tool, url):
    """Log a lead to the database."""
    try:
        db = get_db()
        db.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                tool TEXT,
                url TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        db.execute("INSERT INTO leads (email, tool, url) VALUES (?, ?, ?)",
                   (email, tool, url))
        db.commit()
        db.close()
    except Exception:
        pass  # Don't break the tool if lead logging fails


@app.route("/api/leads")
def api_leads():
    """View collected leads (private — for your eyes only)."""
    db = get_db()
    try:
        leads = db.execute("SELECT * FROM leads ORDER BY created_at DESC LIMIT 100").fetchall()
        db.close()
        return jsonify([dict(l) for l in leads])
    except Exception:
        db.close()
        return jsonify([])


# --- CORS support for WordPress embedding ---

@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin", "")
    # Allow your own domain + localhost for dev
    allowed_origins = [
        "https://eddienehani.com",
        "https://www.eddienehani.com",
        "https://brand-visibility-285f.onrender.com",
        "http://localhost",
        "http://127.0.0.1",
    ]
    if any(origin.startswith(o) for o in allowed_origins) or not origin:
        response.headers["Access-Control-Allow-Origin"] = origin or "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


# ===========================================================================
# CLI mode (keep backward compat with the standalone script)
# ===========================================================================

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        project_id = None
        for i, arg in enumerate(sys.argv):
            if arg == "--project" and i + 1 < len(sys.argv):
                project_id = int(sys.argv[i + 1])
        _run_all_background(project_id)
    else:
        print("\n  SEO Tools Platform")
        print("  Starting web interface at http://localhost:5001\n")
        app.run(debug=True, port=5001)
