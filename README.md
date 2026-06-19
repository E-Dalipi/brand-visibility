# SEO Tools Platform

A suite of AI-powered SEO tools: Brand Visibility Tracker, Entity Map Generator, and Commodity Content Audit. Self-hosted, all API keys server-side.

![Dashboard](https://img.shields.io/badge/status-active-green) ![Python](https://img.shields.io/badge/python-3.10+-blue) ![License](https://img.shields.io/badge/license-MIT-gray)

## Tools

### 🔍 Brand Visibility Tracker (Private Dashboard)
Track how AI engines (ChatGPT, Claude, Gemini, Perplexity) mention your brand. Share of voice, weekly trends, per-prompt breakdowns.

### 🗺️ Entity Map Generator (Public Tool)
Generate a valid [EntityMap v1.0](https://entitymap.org/spec/v1.0) JSON file for any website. Crawls the site, extracts entities with Claude, outputs downloadable `entitymap.json`.

### ✅ Commodity Content Audit (Public Tool)
Judge whether a page's content is differentiated or commodity. Compares against ranking competitors and what AI engines already say.

## Quick Start

```bash
git clone https://github.com/E-Dalipi/brand-visibility.git
cd brand-visibility
pip install -r requirements.txt
cp .env.example .env   # edit with your API keys
python app.py          # open http://localhost:5001
```

## API Keys Needed

| Provider | Env Variable | Used By |
|----------|-------------|----------|
| **Anthropic** | `ANTHROPIC_API_KEY` | All tools (Claude) |
| **OpenAI** | `OPENAI_API_KEY` | Brand Visibility (ChatGPT) |
| **Perplexity** | `PERPLEXITY_API_KEY` | Brand Visibility + Audit |
| **Google AI** | `GEMINI_API_KEY` | Brand Visibility (Gemini) |
| **SerpAPI** | `SERPAPI_KEY` | Commodity Audit (ranking pages) |

All keys stay server-side in `.env`. Never exposed to the browser.

## Routes

| Route | Access | Description |
|-------|--------|-------------|
| `/tools` | Public | Tool landing page |
| `/tools/entitymap` | Public (3/day) | Entity Map Generator |
| `/tools/audit` | Public (3/day) | Commodity Content Audit |
| `/dashboard/<id>` | Private | Brand Visibility Dashboard |
| `/prompts/<id>` | Private | Prompt management |
| `/api/leads` | Private | View collected leads |

## Deploy to Railway

1. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**
2. Select the `brand-visibility` repository
3. Add environment variables in **Settings → Variables**:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   OPENAI_API_KEY=sk-proj-...
   PERPLEXITY_API_KEY=pplx-...
   GEMINI_API_KEY=AIza...
   SERPAPI_KEY=your-key
   SECRET_KEY=random-string-here
   PORT=5001
   ```
4. Railway auto-detects the `Procfile` and deploys
5. Your app is live at `https://your-app.up.railway.app`

### Custom Domain
In Railway → Settings → Domains → add `tools.eddienehani.com` (or any subdomain). Then add a CNAME record in your DNS pointing to Railway.

### Embed in WordPress
Link directly from your site, or embed via iframe:
```html
<iframe src="https://tools.eddienehani.com/tools/entitymap"
        width="100%" height="800" frameborder="0"></iframe>
```

## Scheduling (Brand Visibility)

Add a cron job for automatic runs:
```bash
0 7 * * * cd /path/to/brand-visibility && python app.py run --quiet
```

## Tech Stack

- **Backend:** Flask + SQLite
- **Frontend:** Tailwind CSS + Chart.js (CDN, no build step)
- **APIs:** Direct HTTP calls to Anthropic, OpenAI, Perplexity, Google AI, SerpAPI
- **No build step.** No Node. No npm. Just Python.

## Architecture (API Key Security)

```
Visitor's browser  →  Your server (Flask)  →  AI APIs
                      Keys live HERE          (Anthropic, OpenAI, etc.)
                      Never sent to browser
```

## License

MIT

EntityMap generation follows the [EntityMap v1.0 specification](https://entitymap.org/spec/v1.0) by Fred Laurent & Dixon Jones, licensed under CC BY 4.0.
