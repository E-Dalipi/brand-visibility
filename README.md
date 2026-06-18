# Brand Visibility Tracker

Track how AI engines (ChatGPT, Claude, Gemini, Perplexity) mention your brand — like Waikay, but self-hosted and free.

![Dashboard](https://img.shields.io/badge/status-active-green) ![Python](https://img.shields.io/badge/python-3.10+-blue) ![License](https://img.shields.io/badge/license-MIT-gray)

## What it does

1. **Sends tracked prompts** to ChatGPT, Claude, Gemini, and Perplexity
2. **Detects brand mentions** and competitor mentions in every response
3. **Extracts citation URLs** to see which domains AI models reference
4. **Stores everything** in SQLite with timestamps
5. **Shows dashboards** with share of voice, weekly trends, and per-prompt breakdowns

## Quick Start

```bash
# Clone
git clone https://github.com/YOUR_USERNAME/brand-visibility.git
cd brand-visibility

# Install
pip install -r requirements.txt

# Configure API keys
cp .env.example .env
# Edit .env with your API keys

# Run
python app.py
# Open http://localhost:5001
```

## API Keys Needed

| Provider | Key | Free Tier | Used For |
|----------|-----|-----------|----------|
| **Anthropic** | `ANTHROPIC_API_KEY` | Pay-per-use | Claude responses |
| **OpenAI** | `OPENAI_API_KEY` | Pay-per-use | ChatGPT responses |
| **Perplexity** | `PERPLEXITY_API_KEY` | Pay-per-use | Perplexity responses |
| **Google AI** | `GEMINI_API_KEY` | ✅ Free (1500/day) | Gemini responses |

## Usage

### Web Interface
```bash
python app.py              # Start at http://localhost:5001
```

### CLI (for cron jobs)
```bash
python app.py run                  # Run all active prompts
python app.py run --project 1      # Run for one project
```

### Deploy
```bash
gunicorn app:app                   # Production server
```

## Scheduling

Add a cron job for automatic daily runs:

```bash
crontab -e
# Add:
0 7 * * * cd /path/to/brand-visibility && python app.py run
```

## Tech Stack

- **Backend:** Flask + SQLite
- **Frontend:** Tailwind CSS + Chart.js (CDN, no build step)
- **APIs:** Direct HTTP calls (no SDKs needed)

## License

MIT
