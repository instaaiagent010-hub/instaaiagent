# Instagram News Agent

Roz automatically Indian news fetch karke Instagram pe post karta hai.
Poora **free** — koi paid API nahi (sirf Claude API key chahiye).

---

## Quick Setup (5 minutes)

### Step 1 — Dependencies install karo
```bash
pip install duckduckgo-search requests anthropic Pillow python-dotenv
```

### Step 2 — .env file banao
```bash
cp .env.example .env
```
Phir `.env` file open karo aur apni keys bharo:

| Key | Kahan se milegi | Free hai? |
|-----|----------------|-----------|
| `ANTHROPIC_API_KEY` | console.anthropic.com | Haan, free tier |
| `PEXELS_API_KEY` | pexels.com/api | Haan, 100% free |
| `INSTAGRAM_ACCESS_TOKEN` | developers.facebook.com | Haan |
| `INSTAGRAM_ACCOUNT_ID` | Instagram Business account | Haan |

### Step 3 — Test run karo
```bash
python agent.py
```

---

## Roz Automatically Chalana

### Option A — Linux/Mac Cron Job (free)
```bash
# Terminal mein likho:
crontab -e

# Yeh line add karo (roz subah 8 baje chalega):
0 8 * * * cd /path/to/instagram_news_agent && python agent.py
```

### Option B — GitHub Actions (free, cloud pe)
```yaml
# .github/workflows/post.yml
name: Daily Instagram Post
on:
  schedule:
    - cron: '30 2 * * *'  # UTC 2:30 = IST 8:00
jobs:
  post:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - run: pip install duckduckgo-search requests anthropic Pillow python-dotenv
      - run: python agent.py
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          PEXELS_API_KEY: ${{ secrets.PEXELS_API_KEY }}
          INSTAGRAM_ACCESS_TOKEN: ${{ secrets.INSTAGRAM_ACCESS_TOKEN }}
          INSTAGRAM_ACCOUNT_ID: ${{ secrets.INSTAGRAM_ACCOUNT_ID }}
```

---

## Customize Karo

`agent.py` ke top mein yeh variables change karo:

```python
NEWS_TOPIC = "India today top news hindi"  # apna topic likho
MAX_NEWS   = 3   # roz kitne posts
POST_DELAY = 60  # posts ke beech kitne seconds
```

**Topic examples:**
- `"India politics news today"`
- `"Bollywood entertainment news"`
- `"IPL cricket news today"`
- `"Indian stock market news"`

---

## Instagram API Setup (important)

Instagram pe automatically post karne ke liye:
1. Facebook Developer account banao: developers.facebook.com
2. Ek App create karo
3. Instagram Basic Display API enable karo
4. **Business ya Creator account chahiye** (personal account pe nahi chalega)
5. Access token generate karo (har 60 din mein refresh karna padta hai)

---

## Files

```
instagram_news_agent/
├── agent.py          # Main agent code
├── .env.example      # Keys ka template
├── .env              # Tumhari actual keys (git mein mat daalna!)
└── README.md         # Yeh file
```
