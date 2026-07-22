"""
Instagram News Agent
====================
Roz automatically:
1. DuckDuckGo se top Indian news fetch karta hai
2. Claude AI se engaging caption + hashtags banata hai
3. Pexels se relevant free image dhundta hai
4. Instagram pe post karta hai

Setup:
    pip install duckduckgo-search requests anthropic Pillow python-dotenv

Run:
    python agent.py
"""

import os
import json
import time
import tempfile
import colorsys
import requests

from datetime import datetime
from dotenv import load_dotenv
from ddgs import DDGS
from PIL import Image, ImageDraw
from groq import Groq


def get_font(size: int):
    """Hindi + emoji support wala font load karo, fallback default"""
    from PIL import ImageFont
    candidates = [
        # GitHub Actions / Ubuntu (fonts-noto installed)
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
        "/usr/share/fonts/noto/NotoSansDevanagari-Regular.ttf",
        # Windows
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/Nirmala.ttf",  # Hindi support
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()

load_dotenv()

# --- Config -------------------------------------------------------------------
GROQ_API_KEY        = os.getenv("GROQ_API_KEY")
PEXELS_API_KEY      = os.getenv("PEXELS_API_KEY")        # free: pexels.com/api
INSTAGRAM_TOKEN     = os.getenv("INSTAGRAM_ACCESS_TOKEN") # Meta Graph API token
INSTAGRAM_ACCOUNT_ID = os.getenv("INSTAGRAM_ACCOUNT_ID") # Business account ID
IMGBB_API_KEY       = os.getenv("IMGBB_API_KEY")
HF_API_KEY          = os.getenv("HF_API_KEY")
APP_ID              = os.getenv("APP_ID")
APP_SECRET          = os.getenv("APP_SECRET")

# --- Groq model auto-select: best available model khud pick karo (future-proof) ---
GROQ_MODEL_PREFERENCES = [
    "llama-3.3-70b-versatile",  # proven — reliable content output
    "llama-3.1-8b-instant",     # fallback
    # gpt-oss-120b HATAYA: reasoning model chhote max_tokens pe EMPTY content deta hai
    # (narration 0 words → silent reels). Wapas mat daalo bina empty-output guard ke.
]

def _pick_groq_model() -> str:
    try:
        r = requests.get("https://api.groq.com/openai/v1/models",
                         headers={"Authorization": f"Bearer {GROQ_API_KEY}"}, timeout=15)
        available = {m.get("id") for m in r.json().get("data", [])}
        for _m in GROQ_MODEL_PREFERENCES:
            if _m in available:
                return _m
    except Exception:
        pass
    return "llama-3.3-70b-versatile"

GROQ_MODEL = _pick_groq_model()
print(f"🧠 Groq model: {GROQ_MODEL}")


def _groq_complete(messages, max_tokens=500, **kwargs):
    """Groq call — rate limit (429) pe agle model pe switch karo.
    Groq ke daily token limits PER MODEL hote hain, isliye dusra model abhi bhi chalega."""
    client = Groq(api_key=GROQ_API_KEY)
    models = [GROQ_MODEL] + [m for m in GROQ_MODEL_PREFERENCES if m != GROQ_MODEL]
    last_err = None
    for m in models:
        try:
            return client.chat.completions.create(
                model=m, max_tokens=max_tokens, messages=messages, **kwargs
            )
        except Exception as e:
            last_err = e
            if "rate_limit" in str(e) or "429" in str(e):
                print(f"      {m} rate-limited — agla model try kar raha hoon")
                continue
            raise
    raise last_err

POST_DELAY     = 60   # seconds between posts
CAROUSEL_SLIDES = 4   # carousel mein kitni images

# High-impact queries — 5 topics, fast fetch
NEWS_TOPICS = [
    "India breaking news today",
    "India government Parliament Supreme Court today",
    "India economy RBI cricket sports today",
    "India scam arrest CBI ED crime today",
    "India disaster accident viral news today",
]


# --- Step 1: News Fetch -------------------------------------------------------
def clean_title(title: str) -> str:
    """Title ke end mein aane wala source naam hata do — e.g. '... - NDTV'"""
    import re
    return re.sub(r'\s*[-–|]\s*[A-Z][A-Za-z0-9 &.]{2,40}$', '', title).strip()


def fetch_news(topic: str, max_results: int = 5) -> list[dict]:
    """DuckDuckGo se news fetch karo — multiple fallback strategies"""
    print(f"\n[1/4] News fetch kar raha hoon: '{topic}'")
    cutoff = datetime.now().timestamp() - 86400

    # Attempt strategies: strict → relaxed timelimit → no timelimit
    strategies = [
        {"timelimit": "d"},
        {"timelimit": "w"},
        {},
    ]

    for attempt, params in enumerate(strategies):
        try:
            time.sleep(attempt * 4)  # progressive delay: 0s, 4s, 8s
            with DDGS() as ddgs:
                results = list(ddgs.news(topic, max_results=max_results * 3, **params))

            if not results:
                raise Exception("No results found.")

            fresh = []
            for n in results:
                n["title"] = clean_title(n.get("title", ""))
                pub = n.get("date", "")
                try:
                    from datetime import datetime as dt
                    pub_ts = dt.fromisoformat(pub.replace("Z", "+00:00")).timestamp()
                    if pub_ts >= cutoff - 86400 * attempt:  # window expands with retries
                        fresh.append(n)
                except Exception:
                    fresh.append(n)

            fresh = fresh[:max_results]
            if fresh:
                print(f"      {len(fresh)} fresh news mili")
                return fresh
            raise Exception("No fresh results after filtering.")

        except Exception as e:
            print(f"      Attempt {attempt+1} failed: {e}")

    return []


# ── Copyright-Free News Sources ────────────────────────────────────────────────
def _parse_rss(url: str, source_name: str, max_results: int = 4) -> list[dict]:
    """Generic RSS/Atom parser — public domain / CC sources ke liye"""
    import xml.etree.ElementTree as ET
    import re as _re
    try:
        resp = requests.get(url, timeout=14, headers={"User-Agent": "AtlantisNewsBot/1.0"})
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.content)
        ns   = {"media": "http://search.yahoo.com/mrss/"}
        items = (root.findall(".//item") or
                 root.findall(".//{http://www.w3.org/2005/Atom}entry"))
        news = []
        for item in items:
            t_el  = item.find("title") or item.find("{http://www.w3.org/2005/Atom}title")
            title = (t_el.text or "").strip() if t_el is not None else ""
            if not title: continue
            title = clean_title(title)
            d_el  = (item.find("description") or
                     item.find("{http://www.w3.org/2005/Atom}summary") or
                     item.find("{http://www.w3.org/2005/Atom}content"))
            raw  = (d_el.text or "") if d_el is not None else ""
            desc = _re.sub(r'<[^>]+>', '', raw).strip()[:600]
            img  = ""
            mc   = item.find("media:content", ns)
            if mc is not None: img = mc.get("url", "")
            if not img:
                enc = item.find("enclosure")
                if enc is not None and "image" in enc.get("type", ""): img = enc.get("url", "")
            if not img:
                m = _re.search(r'<img[^>]+src=["\']([^"\']+)["\']', raw)
                if m: img = m.group(1)
            link_el = item.find("link") or item.find("{http://www.w3.org/2005/Atom}link")
            url_out = (link_el.text or link_el.get("href", "")) if link_el is not None else ""
            date_el = (item.find("pubDate") or item.find("{http://www.w3.org/2005/Atom}published"))
            date    = (date_el.text or "")[:10] if date_el is not None else ""
            news.append({"title": title, "body": desc, "image": img,
                         "source": source_name, "date": date, "url": url_out})
            if len(news) >= max_results * 2: break
        result = [n for n in news if n["image"]][:max_results] or news[:max_results]
        print(f"      {source_name}: {len(result)} items")
        return result
    except Exception as e:
        print(f"      {source_name} RSS error: {e}")
        return []


def fetch_pib_rss() -> list[dict]:
    """PIB — Press Information Bureau (India Govt, public domain)"""
    return _parse_rss("https://pib.gov.in/RssMain.aspx", "PIB India", 5)


def fetch_dd_news_rss() -> list[dict]:
    """DD News — Doordarshan (India Govt TV, public domain)"""
    for url in ["https://ddnews.gov.in/en/rss.xml", "https://www.ddnews.gov.in/rss.xml"]:
        result = _parse_rss(url, "DD News", 4)
        if result: return result
    return []


def fetch_newsonair_rss() -> list[dict]:
    """NewsonAir / Akashvani — India official radio (public domain)"""
    for url in [
        "https://newsonair.gov.in/NSD.aspx?Action=rss",
        "https://newsonair.gov.in/NSD_rss.aspx",
        "https://www.newsonair.gov.in/rss.xml",
    ]:
        result = _parse_rss(url, "NewsonAir", 4)
        if result: return result
    return []


def fetch_un_news_rss() -> list[dict]:
    """UN News — United Nations (CC licensed, free to republish)"""
    return _parse_rss("https://news.un.org/feed/subscribe/en/news/all/rss.xml", "UN News", 4)


def fetch_wikinews_rss() -> list[dict]:
    """Wikinews — CC BY 2.5 (fully free to republish)"""
    return _parse_rss(
        "https://en.wikinews.org/w/index.php?"
        "title=Special:NewsFeed&feed=rss&categories=&"
        "notcategories=No+publish%7CArchived%7CDisputed&"
        "namespace=0&count=20&ordermethod=categoryadd&stablepages=only",
        "Wikinews CC", 5
    )


def fetch_ein_presswire_rss() -> list[dict]:
    """EIN Presswire — free press releases, meant for publishing"""
    return _parse_rss("https://www.einpresswire.com/rss/", "EIN Presswire", 4)


def fetch_who_rss() -> list[dict]:
    """WHO — World Health Organization (CC licensed)"""
    return _parse_rss(
        "https://www.who.int/feeds/entity/mediacentre/news/en/rss.xml", "WHO News", 3
    )


# ── Token Auto-Refresh ────────────────────────────────────────────────────────
def update_github_secret(secret_name: str, secret_value: str) -> bool:
    """GitHub Actions secret ko update karo — token auto-renew ke liye"""
    github_token = os.getenv("GH_PAT")
    github_repo = os.getenv("GITHUB_REPOSITORY")  # auto-set in Actions: "owner/repo"
    if not github_token or not github_repo:
        print(f"      GH_PAT ya GITHUB_REPOSITORY nahi — secret update skip")
        return False
    try:
        from nacl import encoding, public
        import base64

        # Repo ka public key lo
        key_resp = requests.get(
            f"https://api.github.com/repos/{github_repo}/actions/public-key",
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }, timeout=10
        )
        key_data = key_resp.json()
        if "key" not in key_data or "key_id" not in key_data:
            print(f"      GitHub public key fetch fail: {key_data}")
            return False
        public_key = key_data["key"]
        key_id = key_data["key_id"]

        # Secret encrypt karo (libsodium SealedBox)
        pk = public.PublicKey(public_key.encode("utf-8"), encoding.Base64Encoder())
        box = public.SealedBox(pk)
        encrypted = box.encrypt(secret_value.encode("utf-8"))
        encrypted_b64 = base64.b64encode(encrypted).decode("utf-8")

        # Secret update karo
        resp = requests.put(
            f"https://api.github.com/repos/{github_repo}/actions/secrets/{secret_name}",
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"encrypted_value": encrypted_b64, "key_id": key_id},
            timeout=10
        )
        if resp.status_code in [201, 204]:
            print(f"      GitHub secret '{secret_name}' updated!")
            return True
        else:
            print(f"      GitHub secret update fail: {resp.status_code} {resp.text[:80]}")
            return False
    except ImportError:
        print("      PyNaCl nahi hai — pip install PyNaCl karo")
        return False
    except Exception as e:
        print(f"      GitHub secret update error: {e}")
        return False


def refresh_token() -> str | None:
    """Short-lived token ko 60-day long-lived token mein convert karo + GitHub Secret update"""
    if not APP_ID or not APP_SECRET or not INSTAGRAM_TOKEN:
        return None
    try:
        resp = requests.get(
            "https://graph.facebook.com/v25.0/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": APP_ID,
                "client_secret": APP_SECRET,
                "fb_exchange_token": INSTAGRAM_TOKEN,
            }, timeout=10
        )
        data = resp.json()
        new_token = data.get("access_token")
        if new_token and new_token != INSTAGRAM_TOKEN:
            print(f"      Token refreshed! Expires in: {data.get('expires_in', '?')}s")

            # .env file update karo (local run ke liye)
            env_path = os.path.join(os.path.dirname(__file__), ".env")
            if os.path.exists(env_path):
                with open(env_path, "r") as f:
                    content = f.read()
                import re
                content = re.sub(
                    r"INSTAGRAM_ACCESS_TOKEN=.*",
                    f"INSTAGRAM_ACCESS_TOKEN={new_token}",
                    content
                )
                with open(env_path, "w") as f:
                    f.write(content)

            # GitHub Secret bhi update karo (Actions run ke liye)
            update_github_secret("INSTAGRAM_ACCESS_TOKEN", new_token)
            return new_token
        elif new_token:
            print(f"      Token already fresh — no update needed")
            return new_token
        else:
            print(f"      Token refresh failed: {data}")
    except Exception as e:
        print(f"      Token refresh error: {e}")
    return None


# --- AI Planning Layer --------------------------------------------------------
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "posted_history.json")


def load_posted_history() -> set:
    """posted_history.json se previously posted titles load karo"""
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("titles", []))
    except Exception:
        pass
    return set()


def load_posted_videos() -> set:
    """Pehle use hui govt videos ke IDs — same footage repeat na ho"""
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f).get("videos", []))
    except Exception:
        pass
    return set()


USED_VIDEO_IDS: set = set()   # run start pe history se load hota hai
LAST_VIDEO_ID  = ""           # jo video use hui uska id — history mein save hota hai


def save_posted_title(title: str) -> None:
    """Title history mein save karo aur GitHub pe push karo"""
    try:
        import subprocess
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        # Pull latest FIRST — avoid overwriting concurrent run's history
        subprocess.run(["git", "stash"], cwd=repo_dir, capture_output=True)
        subprocess.run(["git", "pull", "origin", "main", "--no-rebase"], cwd=repo_dir, capture_output=True)
        subprocess.run(["git", "stash", "pop"], cwd=repo_dir, capture_output=True)

        existing = {}
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)
        titles = existing.get("titles", [])
        normalized = title.lower().strip()[:120]
        if normalized not in titles:
            titles.append(normalized)
        titles = titles[-300:]

        videos = existing.get("videos", [])
        if LAST_VIDEO_ID and LAST_VIDEO_ID not in videos:
            videos.append(LAST_VIDEO_ID)
        videos = videos[-200:]

        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump({"titles": titles, "videos": videos,
                       "updated": datetime.now().isoformat()}, f,
                      ensure_ascii=False, indent=2)
        subprocess.run(["git", "add", "posted_history.json"], cwd=repo_dir)
        result = subprocess.run(["git", "commit", "-m", "chore: update posted history [skip ci]"],
                                cwd=repo_dir, capture_output=True)
        if result.returncode == 0:
            subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=repo_dir)
            print(f"      History saved: {title[:60]}")
        else:
            print(f"      History commit skip (no change)")
    except Exception as e:
        print(f"      History save error: {e}")


def get_recently_posted_titles() -> set:
    """Local history + Instagram API dono se titles lao"""
    titles = load_posted_history()
    if not INSTAGRAM_TOKEN or not INSTAGRAM_ACCOUNT_ID:
        return titles
    try:
        resp = requests.get(
            f"https://graph.facebook.com/v25.0/{INSTAGRAM_ACCOUNT_ID}/media",
            params={"fields": "caption", "limit": 30, "access_token": INSTAGRAM_TOKEN},
            timeout=10
        )
        for post in resp.json().get("data", []):
            cap = post.get("caption", "")
            if cap:
                titles.add(cap[:120].lower())
    except Exception:
        pass
    return titles


STOP_WORDS = {"the","a","an","is","in","of","on","at","to","for","and","or","with",
              "his","her","its","this","that","was","has","are","were","will","been",
              "new","says","india","news","today","government","latest","update"}

def is_duplicate(news_title: str, recent_titles: set) -> bool:
    words = set(news_title.lower().split()) - STOP_WORDS
    if not words:
        return False
    for stored in recent_titles:
        stored_words = set(stored.split()) - STOP_WORDS
        if not stored_words:
            continue
        overlap = len(words & stored_words) / max(len(words), len(stored_words))
        if overlap >= 0.50:
            return True
    return False


def smart_plan(all_news: list[dict], count: int = CAROUSEL_SLIDES) -> list[dict]:
    """Groq se decide karwao — konsi news post karein, kis format mein, kis order mein"""
    print(f"\n[AI] {len(all_news)} news analyze kar raha hoon...")

    hour = datetime.now().hour
    if 6 <= hour < 12:
        time_context = "subah (morning) — motivational aur breaking news best karti hai"
    elif 12 <= hour < 17:
        time_context = "dopahar (afternoon) — entertainment aur business news best karti hai"
    else:
        time_context = "shaam/raat (evening/night) — viral, funny ya emotional content best karta hai"

    news_list_str = "\n".join([
        f"{i+1}. [{n.get('source','')}] {n.get('title','')[:100]}"
        for i, n in enumerate(all_news)
    ])

    prompt = f"""Tu ek senior Indian news editor hai jo Instagram ke liye sabse important news choose karta hai.

Abhi time hai: {time_context}

SELECTION CRITERIA — sirf wahi news choose karo jo:
1. NATIONALLY SIGNIFICANT ho — ek bade tabaqqe ke Indians ko directly affect kare
2. HIGH CREDIBILITY — government, Supreme Court, RBI, election commission, major incident, sports result
3. STRONG VISUAL — news ki actual image clearly event dikhati ho (rally, verdict, match, accident, arrest)
4. NO CLICKBAIT — "shocking", "unbelievable", gossip, rumor, low-value celebrity drama avoid karo

Har news ko importance score do (1-10):
- 9-10: National crisis, budget, election result, war/border tension, major verdict
- 7-8: Government policy, economic data, major sports result, significant arrest/scam
- 5-6: State-level news, mid-level celebrity, industry news
- 1-4: Gossip, clickbait, low-impact local news — REJECT KARO

Neeche {len(all_news)} news hain:
{news_list_str}

Sirf TOP {count} news choose karo jinka importance score 7+ ho.
Agar koi bhi 7+ nahi hai to sabse zyada important ek choose karo.
Saath mein yeh bhi check karo — "worth_posting": true/false:
- false: clickbait, rumor, gossip, sirf ek sheher tak limited, stale
- true: verified, national impact, credible source

Sirf JSON respond karo:
{{
  "plan": [
    {{"index": 0, "format": "image", "image_source": "news", "importance": 9, "worth_posting": true, "reason": "why important"}}
  ],
  "strategy": "aaj ki overall posting strategy ek line mein"
}}"""

    try:
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        result = json.loads(resp.choices[0].message.content)
        print(f"      Strategy: {result.get('strategy', '')}")
        planned = []
        for item in result.get("plan", []):
            idx = item.get("index", 0)
            if 0 <= idx < len(all_news):
                news = all_news[idx].copy()
                news["_format"] = item.get("format", "image")
                news["_image_source"] = item.get("image_source", "news")
                news["_reason"] = item.get("reason", "")
                news["_importance"] = item.get("importance", 7)
                planned.append(news)
        return planned[:count] if planned else all_news[:count]
    except Exception as e:
        print(f"      Planning error: {e} — default order use kar raha hoon")
        return all_news[:count]


# --- Content Quality Check ----------------------------------------------------
def is_worth_posting(caption: str, news_title: str) -> bool:
    """Strict quality gate — low-value ya clickbait news reject karo"""
    try:
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            max_tokens=150,
            messages=[{"role": "user", "content": f"""
Tu ek strict Indian news quality editor hai.

News headline: {news_title[:200]}
Caption: {caption[:400]}

REJECT karo agar:
- Clickbait hai ("shocking", "you won't believe", sensational without substance)
- Low national importance — sirf ek city ya state tak limited
- Pure celebrity gossip ya entertainment without real news value
- Rumor, unverified claim, opinion masquerading as news
- Repetitive/stale news (koi naya angle nahi)

APPROVE karo agar:
- National ya international significance hai
- Government, judiciary, economy, major sports, defense se related hai
- Confirmed facts hain, credible source hai
- Indian public ke liye genuinely useful ya important hai

Sirf JSON: {{"post": true/false, "reason": "ek line mein clear reason"}}
"""}],
            response_format={"type": "json_object"}
        )
        result = json.loads(resp.choices[0].message.content)
        verdict = result.get("post", True)
        print(f"      Quality check: {'PASS' if verdict else 'REJECT'} — {result.get('reason', '')[:80]}")
        return verdict
    except:
        return True


# --- Step 2: Caption + Image Keyword ------------------------------------------
def generate_caption(news_item: dict) -> dict:
    """Groq se Instagram caption banao — image ke saath match kare"""
    print(f"\n[2/4] Caption generate kar raha hoon...")

    client = Groq(api_key=GROQ_API_KEY)
    image_url = news_item.get("image", "")

    prompt = f"""
Tu ek Instagram news page ka content writer hai jo Indian audience ke liye likhta hai.

Hum yeh news article ki THUMBNAIL IMAGE Instagram pe post kar rahe hain:
News Title: {news_item.get('title', '')}
News Body: {news_item.get('body', '')[:500]}
Source: {news_item.get('source', '')}
Published: {news_item.get('date', 'aaj')[:10]}

SABSE IMPORTANT RULE — FACT ACCURACY:
- headline aur image_summary mein SIRF wahi facts likho jo upar "News Title" ya "News Body" mein clearly likhe hain
- Apni taraf se koi bhi fact, number, naam, ya detail ADD MAT KARO — ye galat information spread karega
- Agar koi fact clear nahi hai to us cheez ko mat likho

Caption ke baaki rules:
- YE EK PHOTO POST HAI — "video", "clip", "watch", "reel" bilkul mat likho
- "tasveer", "photo", "image" — yahi words use karo
- Pehli line mein photo acknowledge karo (e.g., "Ye tasveer kaafi kuch kehti hai...")
- Hinglish mein likho (Hindi + English mix)
- 6-8 lines, emotional aur conversational tone
- End mein strong question ya call-to-action
- CAPTION MEIN KOI HASHTAG NAHI — sirf "hashtags" field mein daalo

Sirf JSON format mein respond karo:
{{
  "caption": "caption text ONLY — no hashtags here",
  "hashtags": "#tag1 #tag2 #tag3 ... (15-20 Hindi+English hashtags)",
  "image_keyword": "2-3 word English description of what image likely shows",
  "emoji_title": "emoji + short title",
  "headline": "5-8 word Hinglish headline — SIRF news title ke facts use karo, kuch add mat karo. Spelling 100% correct ho — double check karo likhne se pehle",
  "image_summary": "2-3 Hinglish sentences (max 35 words) — SIRF news body ke confirmed facts likho. Spelling 100% correct ho, koi typo nahi"
}}
"""

    try:
        message = client.chat.completions.create(
            model=GROQ_MODEL,
            max_tokens=900,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        raw = message.choices[0].message.content.strip()
        result = json.loads(raw)

        import re
        caption = result.get("caption", "")
        # Strip hashtags from caption if Groq slips them in
        caption = re.sub(r'\s*#\w+', '', caption).strip()
        # Replace video-related words
        caption = re.sub(r'\b(video|reel|clip)\b', 'photo', caption, flags=re.IGNORECASE)
        caption = re.sub(r'dekho video', 'dekho ye tasveer', caption, flags=re.IGNORECASE)
        caption = re.sub(r'ye video', 'ye tasveer', caption, flags=re.IGNORECASE)
        result["caption"] = caption

        # Spell-check headline and summary (image pe dikhne wala text)
        headline = result.get("headline", "")
        summary  = result.get("image_summary", "")
        if headline or summary:
            try:
                fix_resp = client.chat.completions.create(
                    model=GROQ_MODEL,
                    max_tokens=200,
                    messages=[{"role": "user", "content": f"""Fix spelling mistakes only. Do NOT change meaning, words, or language. Return corrected versions only.

Headline: {headline}
Summary: {summary}

JSON: {{"headline": "corrected headline", "summary": "corrected summary"}}"""}],
                    response_format={"type": "json_object"}
                )
                fixed = json.loads(fix_resp.choices[0].message.content)
                if fixed.get("headline"):
                    result["headline"] = fixed["headline"]
                if fixed.get("summary"):
                    result["image_summary"] = fixed["summary"]
            except Exception:
                pass

        preview = result['caption'][:60].encode('ascii', errors='ignore').decode()
        print(f"      Caption ready: {preview}...")
        return result
    except Exception as e:
        print(f"      Error: {e}")
        return {
            "caption": news_item.get('title', 'Breaking News!'),
            "hashtags": "#India #News #BreakingNews #IndianNews",
            "image_keyword": "India news",
            "emoji_title": "Breaking News"
        }


# --- Step 3: Image Search -----------------------------------------------------
def generate_ai_image(news_title: str, keyword: str) -> str | None:
    """Hugging Face FLUX se free AI image banao — high quality, copyright-free"""
    if not HF_API_KEY:
        return None
    print(f"\n[3/4] FLUX AI image generate kar raha hoon: '{keyword}'")
    try:
        prompt = (
            f"dramatic professional news thumbnail, topic: {keyword}, "
            f"{news_title[:80]}, bold vibrant colors, cinematic lighting, "
            f"photorealistic, high quality, no text, no watermark"
        )
        resp = requests.post(
            "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell",
            headers={"Authorization": f"Bearer {HF_API_KEY}"},
            json={"inputs": prompt},
            timeout=60
        )
        if resp.status_code == 200 and "image" in resp.headers.get("content-type", ""):
            path = os.path.join(tempfile.gettempdir(), f"ai_image_{int(time.time())}.png")
            with open(path, "wb") as f:
                f.write(resp.content)
            print(f"      FLUX image bani! Upload kar raha hoon...")
            return upload_image(path)
        else:
            print(f"      HF error: {resp.status_code} {resp.text[:100]}")
    except Exception as e:
        print(f"      AI image error: {e}")
    return None


def fetch_image_pexels(keyword: str) -> str | None:
    """Pexels se free image dhundo aur download karo"""
    print(f"\n[3/4] Image dhund raha hoon: '{keyword}'")

    if not PEXELS_API_KEY:
        print("      Pexels key nahi hai, news card bana raha hoon...")
        return None

    try:
        headers = {"Authorization": PEXELS_API_KEY}
        url = f"https://api.pexels.com/v1/search?query={keyword}&per_page=5&orientation=square"
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()

        if data.get("photos"):
            photo = data["photos"][0]
            img_url = photo["src"]["large"]
            print(f"      Pexels image URL mili: {img_url[:60]}...")
            return img_url
    except Exception as e:
        print(f"      Pexels error: {e}")

    # Fallback: DuckDuckGo image search
    return fetch_image_duckduckgo(keyword)


def fetch_image_duckduckgo(keyword: str) -> str | None:
    """DuckDuckGo se image search — completely free fallback"""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.images(keyword, max_results=5,
                                        license_image="Share"))
        if results:
            img_url = results[0]["image"]
            print(f"      DDG image URL mili: {img_url[:60]}...")
            return img_url
    except Exception as e:
        print(f"      DDG image error: {e}")
    return None


def generate_ai_video(keyword: str, news_title: str) -> str | None:
    """HuggingFace se AI video generate karo — free tier"""
    if not HF_API_KEY:
        return None
    print(f"\n[3/4] AI video generate kar raha hoon: '{keyword}'")
    try:
        prompt = f"cinematic news video, {keyword}, {news_title[:60]}, professional broadcast style"
        # LTX-Video model — fastest free video generation on HF
        resp = requests.post(
            "https://router.huggingface.co/hf-inference/models/Lightricks/LTX-Video",
            headers={"Authorization": f"Bearer {HF_API_KEY}"},
            json={"inputs": prompt},
            timeout=120
        )
        if resp.status_code == 200 and "video" in resp.headers.get("content-type", ""):
            path = os.path.join(tempfile.gettempdir(), f"ai_video_{int(time.time())}.mp4")
            with open(path, "wb") as f:
                f.write(resp.content)
            public_url = upload_image(path)  # ImgBB video bhi host karta hai
            if public_url:
                print(f"      AI video ready: {public_url[:60]}")
                return public_url
        else:
            print(f"      AI video error: {resp.status_code} {resp.text[:80]}")
    except Exception as e:
        print(f"      AI video error: {e}")
    return None


def fetch_video(keyword: str) -> str | None:
    """Pexels se free stock video dhundo — same API key, direct MP4 URL"""
    if not PEXELS_API_KEY:
        return None
    print(f"\n[3/4] Video dhund raha hoon: '{keyword}'")
    try:
        headers = {"Authorization": PEXELS_API_KEY}
        resp = requests.get(
            f"https://api.pexels.com/videos/search?query={keyword}&per_page=5&orientation=portrait",
            headers=headers, timeout=10
        )
        videos = resp.json().get("videos", [])
        for video in videos:
            # HD ya SD MP4 file dhundo
            for vf in video.get("video_files", []):
                if vf.get("file_type") == "video/mp4" and vf.get("height", 0) >= 720:
                    url = vf["link"]
                    print(f"      Pexels video mili: {url[:60]}...")
                    return url
    except Exception as e:
        print(f"      Pexels video error: {e}")
    return None


# Public-domain / government YouTube channels — sab channel_id se (legacy user= dead ho chuke)
# NOTE: private news channels (Aaj Tak, NDTV, India Today, ANI...) yahan KABHI mat daalna — copyright.
GOVT_CHANNELS = {
    "PIB India":            "UCGn6a5SI8SNlj7WylmPD6GQ",   # Press Information Bureau
    "PMO India":            "UCDS9hpqUEXsXUIcf0qDcBIA",   # Prime Minister's Office
    "DD News":              "UCKwucPzHZ7zCUIf7If-Wo1g",   # Doordarshan News
    "Doordarshan National": "UCSjPe5kinQtwcyHcFJyyMfw",   # DD National
    "MEA India":            "UCJyP-OtuzlRV-r-3vR9qQTg",   # Ministry of External Affairs
    "MyGov India":          "UCQTQ_iXM32kU7GfIcsEBaYw",   # MyGov
    "NASA":                 "UC9SM7V7J1pAhPabOUST01fw",   # public domain
    "United Nations":       "UCCKy7J6X7ofXXpNnlXfVGlw",   # UN
    "WHO":                  "UCT7a_fVlSrjOs9jyvtH-uhA",   # World Health Organization
}

# Matching mein ye words ignore — inse fake match banta hai
_RSS_STOP = {
    "the", "a", "an", "in", "on", "of", "and", "to", "for", "with", "is", "are",
    "news", "india", "indian", "today", "latest", "new", "video", "live", "update",
}


def fetch_rss_video(keyword: str) -> str | None:
    """Govt/public-domain YouTube channels se news video — best keyword match wali choose karo"""
    import subprocess, xml.etree.ElementTree as ET
    global LAST_VIDEO_ID

    yt_cookies = os.getenv("YT_COOKIES", "")
    cookies_path = None
    if yt_cookies:
        cookies_path = os.path.join(tempfile.gettempdir(), "yt_cookies.txt")
        with open(cookies_path, "w") as f:
            f.write(yt_cookies)

    kw_words = {w for w in keyword.lower().split() if w not in _RSS_STOP and len(w) > 2}
    out_dir  = tempfile.gettempdir()
    ns = {"atom": "http://www.w3.org/2005/Atom",
          "yt":   "http://www.youtube.com/xml/schemas/2015"}

    # Step 1: saare channels se candidates lo aur keyword-overlap se score karo
    candidates = []   # (score, channel, video_id, title)
    for ch_name, ch_id in GOVT_CHANNELS.items():
        try:
            resp = requests.get(
                f"https://www.youtube.com/feeds/videos.xml?channel_id={ch_id}",
                timeout=10, headers={"User-Agent": "Mozilla/5.0"}
            )
            if resp.status_code != 200:
                continue
            entries = ET.fromstring(resp.content).findall("atom:entry", ns)
            for rank, entry in enumerate(entries[:15]):
                vid_el   = entry.find("yt:videoId", ns)
                title_el = entry.find("atom:title", ns)
                if vid_el is None:
                    continue
                vid   = vid_el.text
                title = title_el.text if title_el is not None else ""
                if f"yt:{vid}" in USED_VIDEO_IDS:
                    continue   # ye video pehle post ho chuki
                t_words = {w for w in title.lower().split() if w not in _RSS_STOP and len(w) > 2}
                overlap = len(kw_words & t_words)
                # freshness bonus — feed mein upar wali videos nayi hoti hain
                score   = overlap * 10 + max(0, 15 - rank)
                candidates.append((score, ch_name, vid, title))
        except Exception as e:
            print(f"      [{ch_name}] RSS error: {e}")

    if not candidates:
        print("      Govt RSS: koi naya video nahi mila")
        return None

    candidates.sort(key=lambda c: c[0], reverse=True)

    # Step 2: top matches try karo jab tak koi download na ho jaye
    # Format selectors — sakht se dheela. Cookies ke saath YouTube kam formats deta hai,
    # isliye ek fail ho to agla try karo.
    FORMAT_CHAIN = [
        "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720][ext=mp4]",
        "bv*[height<=720]+ba/b[height<=720]",
        "best",
    ]

    for score, ch_name, video_id, video_title in candidates[:5]:
        print(f"      [{ch_name}] match(score {score}): {video_title[:50]}")
        out_template = os.path.join(out_dir, f"rss_{video_id}.%(ext)s")
        last_err = ""
        for fmt in FORMAT_CHAIN:
            cmd = [
                "yt-dlp",
                f"https://www.youtube.com/watch?v={video_id}",
                "-f", fmt,
                "--merge-output-format", "mp4",
                # Reel ko sirf ~90s chahiye — pura 1GB video download karna waste hai
                "--download-sections", "*0-90",
                "-o", out_template,
                "--no-playlist", "--no-warnings",
                "--sleep-interval", "2"
            ]
            if cookies_path:
                cmd += ["--cookies", cookies_path]
            try:
                dl = subprocess.run(cmd, capture_output=True, timeout=180)
                for fname in os.listdir(out_dir):
                    if fname.startswith(f"rss_{video_id}") and fname.endswith(".mp4"):
                        path = os.path.join(out_dir, fname)
                        LAST_VIDEO_ID = f"yt:{video_id}"
                        USED_VIDEO_IDS.add(LAST_VIDEO_ID)
                        print(f"      [{ch_name}] Downloaded: {os.path.getsize(path)//1024//1024}MB ✓ (fmt: {fmt[:22]})")
                        return path
                last_err = dl.stderr[-90:].decode(errors="ignore")
            except Exception as e:
                last_err = str(e)[:90]
        print(f"      [{ch_name}] saare formats fail: {last_err}")

    return None


def fetch_pib_video(keyword: str) -> str | None:
    """PIB India YouTube channel se latest video download karo — public domain"""
    import subprocess
    print(f"\n[PIB] Video dhund raha hoon: '{keyword}'")

    # PIB India ke official YouTube channels
    PIB_CHANNELS = [
        "https://www.youtube.com/@pibindia/videos",
        "https://www.youtube.com/user/pibindia/videos",
    ]

    out_dir = tempfile.gettempdir()

    # YouTube cookies (GitHub Secret YT_COOKIES se)
    yt_cookies = os.getenv("YT_COOKIES", "")
    cookies_path = None
    if yt_cookies:
        cookies_path = os.path.join(out_dir, "yt_cookies.txt")
        with open(cookies_path, "w") as f:
            f.write(yt_cookies)
        print("      YouTube cookies loaded")

    for channel_url in PIB_CHANNELS:
        try:
            out_template = os.path.join(out_dir, "pib_%(id)s.%(ext)s")

            base_cmd = ["yt-dlp", "--no-warnings"]
            if cookies_path:
                base_cmd += ["--cookies", cookies_path]

            # Step 1: Channel se latest 5 video IDs list karo
            list_result = subprocess.run(
                base_cmd + [channel_url, "--flat-playlist",
                            "--playlist-start", "1", "--playlist-end", "5",
                            "--print", "%(id)s\t%(title)s"],
                capture_output=True, text=True, timeout=60
            )

            lines = [l for l in list_result.stdout.strip().split('\n') if '\t' in l]
            if not lines:
                print(f"      List fail ({channel_url[-20:]}): {list_result.stderr[-80:]}")
                continue

            kw_lower = keyword.lower()
            selected = lines[0]
            for line in lines:
                if any(w in line.lower() for w in kw_lower.split()[:3]):
                    selected = line
                    break

            video_id, video_title = selected.split('\t', 1)
            print(f"      Video selected: {video_title[:60]}")

            dl = subprocess.run(
                base_cmd + [
                    f"https://www.youtube.com/watch?v={video_id}",
                    "-f", "mp4[height<=720]/best[height<=720]/best",
                    "-o", out_template, "--no-playlist"
                ],
                capture_output=True, timeout=180
            )

            if dl.returncode != 0:
                err = dl.stderr[-200:].decode(errors='ignore')
                print(f"      Download fail: {err}")
                if "cookies" in err.lower() or "sign in" in err.lower():
                    print("      YouTube cookies ki zaroorat hai — YT_COOKIES secret set karo")
                    break
                continue

            for fname in os.listdir(out_dir):
                if fname.startswith(f"pib_{video_id}") and fname.endswith(".mp4"):
                    path = os.path.join(out_dir, fname)
                    size_mb = os.path.getsize(path) // 1024 // 1024
                    print(f"      Downloaded: {fname} ({size_mb}MB)")
                    return path

        except subprocess.TimeoutExpired:
            print(f"      Timeout")
        except Exception as e:
            print(f"      Error: {e}")

    # Fallback: Pexels se topic-related stock video
    print("      PIB unavailable — Pexels video try kar raha hoon...")
    return fetch_video_pexels_mp4(keyword)


def fetch_video_pexels_mp4(keyword: str) -> str | None:
    """Pexels se stock video download karo — MP4 local file return"""
    if not PEXELS_API_KEY:
        return None
    try:
        headers = {"Authorization": PEXELS_API_KEY}
        resp = requests.get(
            f"https://api.pexels.com/videos/search?query={keyword}&per_page=5&orientation=portrait",
            headers=headers, timeout=10
        )
        videos = resp.json().get("videos", [])
        for video in videos:
            for vf in video.get("video_files", []):
                if vf.get("file_type") == "video/mp4" and vf.get("height", 0) >= 720:
                    url = vf["link"]
                    print(f"      Pexels video: {url[:60]}")
                    # Download to temp file
                    r = requests.get(url, timeout=60, stream=True)
                    path = os.path.join(tempfile.gettempdir(), f"pexels_{int(time.time())}.mp4")
                    with open(path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)
                    size_mb = os.path.getsize(path) // 1024 // 1024
                    print(f"      Pexels downloaded: {size_mb}MB")
                    return path
    except Exception as e:
        print(f"      Pexels video error: {e}")
    return None


def fetch_wikimedia_video(keyword: str) -> str | None:
    """Wikimedia Commons MediaWiki API se free India news video dhundo"""
    print(f"      [Wikimedia] Searching: {keyword}")
    try:
        search = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query", "list": "search", "format": "json",
                "srsearch": f"{keyword} India",
                "srnamespace": "6", "srlimit": 8,
                "srqiprofile": "classic_noboostlinks"
            },
            timeout=10
        )
        results = search.json().get("query", {}).get("search", [])
        video_titles = [r["title"] for r in results
                        if any(r["title"].lower().endswith(ext)
                               for ext in [".webm", ".ogv", ".mp4"])]
        if not video_titles:
            print("      [Wikimedia] Koi video nahi mili")
            return None

        # Pehli valid video ka URL lo
        info = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query", "titles": "|".join(video_titles[:3]),
                "prop": "imageinfo", "iiprop": "url|size|mediatype",
                "format": "json"
            },
            timeout=10
        )
        pages = info.json().get("query", {}).get("pages", {})
        for page in pages.values():
            ii = page.get("imageinfo", [{}])[0]
            url = ii.get("url", "")
            size = ii.get("size", 0)
            if url and size < 80 * 1024 * 1024:  # 80MB limit
                ext = ".webm" if ".webm" in url else ".mp4"
                path = os.path.join(tempfile.gettempdir(), f"wiki_{int(time.time())}{ext}")
                print(f"      [Wikimedia] Downloading: {page.get('title','')[:50]}")
                r = requests.get(url, timeout=120, stream=True)
                with open(path, "wb") as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
                size_mb = os.path.getsize(path) // 1024 // 1024
                if size_mb > 0:
                    print(f"      [Wikimedia] Downloaded: {size_mb}MB")
                    return path
    except Exception as e:
        print(f"      [Wikimedia] Error: {e}")
    return None


def fetch_archive_video(keyword: str) -> str | None:
    """Internet Archive se India/DD News video dhundo — no blocking"""
    print(f"      [Archive.org] Searching: {keyword}")
    try:
        search = requests.get(
            "https://archive.org/advancedsearch.php",
            params={
                "q": f'({keyword}) AND (subject:"India" OR subject:"DD News" OR subject:"Doordarshan") AND mediatype:movies',
                "fl": "identifier,title",
                "rows": 5, "output": "json", "sort": "date desc"
            },
            timeout=15
        )
        docs = search.json().get("response", {}).get("docs", [])
        if not docs:
            print("      [Archive.org] Koi result nahi")
            return None

        for doc in docs:
            identifier = doc["identifier"]
            files_resp = requests.get(
                f"https://archive.org/metadata/{identifier}/files",
                timeout=10
            )
            files = files_resp.json().get("result", [])
            for finfo in files:
                fname = finfo.get("name", "")
                fsize = int(finfo.get("size", 0))
                if fname.endswith(".mp4") and 0 < fsize < 80 * 1024 * 1024:
                    url = f"https://archive.org/download/{identifier}/{fname}"
                    print(f"      [Archive.org] {doc.get('title','')[:50]}")
                    path = os.path.join(tempfile.gettempdir(), f"archive_{int(time.time())}.mp4")
                    r = requests.get(url, timeout=120, stream=True)
                    with open(path, "wb") as f:
                        for chunk in r.iter_content(8192):
                            f.write(chunk)
                    size_mb = os.path.getsize(path) // 1024 // 1024
                    if size_mb > 0:
                        print(f"      [Archive.org] Downloaded: {size_mb}MB")
                        return path
    except Exception as e:
        print(f"      [Archive.org] Error: {e}")
    return None


def fetch_news_video_free(keyword: str) -> str | None:
    """Multi-source free video: RSS(Sansad/DD) → Wikimedia → Archive.org → Pexels"""
    print(f"\n[Video] '{keyword}' ke liye video dhund raha hoon...")

    # Source 1: Sansad TV / DD News / PIB — YouTube RSS (real-time, govt content)
    path = fetch_rss_video(keyword)
    if path:
        return path

    # Source 2: Wikimedia Commons (public domain, direct MP4)
    path = fetch_wikimedia_video(keyword)
    if path:
        return path

    # Source 3: Internet Archive (DD News, Doordarshan collections)
    path = fetch_archive_video(keyword)
    if path:
        return path

    # Source 4: Pexels stock video (always works, copyright-free)
    print("      Pexels stock video try kar raha hoon...")
    return fetch_video_pexels_mp4(keyword)


def _normalize_audio(path: str) -> None:
    import subprocess as _sp
    norm = path.replace(".mp3", "_norm.mp3")
    filters = (
        "highpass=f=85,"
        "lowpass=f=13000,"
        "acompressor=threshold=-18dB:ratio=4:attack=5:release=50:makeup=2dB,"
        "equalizer=f=250:t=q:w=2:g=2,"
        "equalizer=f=3500:t=q:w=1.5:g=3,"
        "equalizer=f=7500:t=q:w=2:g=1,"
        "loudnorm=I=-14:TP=-1.5:LRA=7"
    )
    try:
        r = _sp.run(
            ["ffmpeg", "-y", "-i", path, "-af", filters, norm],
            capture_output=True, timeout=30
        )
        if r.returncode == 0 and os.path.exists(norm):
            os.replace(norm, path)
    except Exception:
        pass  # ffmpeg na mile to bhi voice zinda rahe (bina polish ke)


def generate_tts_audio(text: str, out_path: str) -> bool:
    """Edge TTS (hourly rotation) → gTTS fallback"""
    import re as _re
    clean = _re.sub(r'[*_`#~\[\]{}|<>\\]', '', text).strip()
    if not clean:
        return False

    # Verified working voices (AnanyaNeural Edge TTS pe exist nahi karti — hata di)
    VOICES = [
        ("hi-IN-MadhurNeural",           "-5%", "+0Hz", "+12%"),  # male, deep Hindi
        ("hi-IN-SwaraNeural",            "-4%", "-2Hz", "+15%"),  # female, clear Hindi
        ("en-IN-NeerjaExpressiveNeural", "-2%", "+0Hz", "+15%"),  # female, energetic
        ("en-IN-PrabhatNeural",          "-4%", "+0Hz", "+15%"),  # male, crisp
        ("en-IN-NeerjaNeural",           "-3%", "+0Hz", "+15%"),  # female, smooth
    ]
    voice_idx = (int(time.time()) // 3600) % len(VOICES)
    ordered   = VOICES[voice_idx:] + VOICES[:voice_idx]

    try:
        import asyncio, edge_tts
        try:
            # kuch systems pe aiodns broken hota hai — system DNS resolver force karo
            import aiohttp.resolver, aiohttp.connector
            aiohttp.resolver.DefaultResolver = aiohttp.resolver.ThreadedResolver
            aiohttp.connector.DefaultResolver = aiohttp.resolver.ThreadedResolver
        except Exception:
            pass
        for voice, rate, pitch, vol in ordered:
            try:
                async def _speak(v=voice, r=rate, p=pitch, vl=vol):
                    comm = edge_tts.Communicate(clean, voice=v, rate=r, pitch=p, volume=vl)
                    await comm.save(out_path)
                asyncio.run(_speak())
                if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
                    _normalize_audio(out_path)
                    print(f"      TTS: {voice} (slot {voice_idx})")
                    return True
            except Exception:
                continue
    except Exception as e:
        print(f"      Edge TTS error: {e}")

    # gTTS last resort
    try:
        from gtts import gTTS
        gTTS(text=clean, lang="hi", slow=False).save(out_path)
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            _normalize_audio(out_path)
            print(f"      TTS: gTTS fallback")
            return True
    except Exception as e:
        print(f"      gTTS error: {e}")
    return False


def generate_narration(news_item: dict, headline: str, summary: str) -> str:
    """Groq se 30-second news Reel narration — 6 rotating hooks"""
    title = news_item.get("title", headline)
    body  = news_item.get("body", summary)[:500]

    import random as _rand
    narration_styles = [
        "BREAKING NEWS ENERGY: Jaise LIVE bulletin chal raha ho — urgency, speed, impact.\n"
        "'Abhi-abhi khabar aayi hai...' / 'Is pal poori duniya ki nazar...' — listener ko screen se chipka do.",

        "IMPACT FIRST: Seedha consequence se shuru karo — ye news logon ki zindagi pe kya asar dalegi?\n"
        "'Iska matlab hai ke agle 6 mahine...' / 'Ye faisla 140 crore logon ko...' — personal connection banao.",

        "HISTORICAL CONTEXT: Ek shocking historical fact se shuru karo.\n"
        "'Aakhri baar aisa tab hua tha jab...' / '50 saal mein pehli baar...' — gravity feel karao.",

        "NUMBERS HOOK: Ek mind-blowing stat ya number se shuru karo.\n"
        "'Sirf 72 ghante mein...' / 'Rs. 40,000 crore ka sawaal...' — number drop karo, phir context do.",

        "COMMON MAN ANGLE: Common Indian ke perspective se — unka kya hoga?\n"
        "'Agar tum aam aadmi ho...' / 'Ghar ka budget, petrol ka rate, ek naukri...' — relatable real-life impact.",

        "QUESTION HOOK: Ek thought-provoking question se shuru karo.\n"
        "'Kya tum jaante ho...?' / 'Socho agar kal se...' — curiosity jagao, phir jawab do.",
    ]
    style_idx     = (int(time.time()) // 3600) % len(narration_styles)
    chosen_style  = narration_styles[style_idx]

    try:
        resp = _groq_complete(max_tokens=420, messages=[{"role": "user", "content": f"""
Tu @atlantis_news_ai ka Hindi news anchor hai — clear, credible, impactful.
Ek 30-second news Reel narration likho.

News: {title}
Details: {body}
Summary: {summary}

STYLE THIS POST: {chosen_style}

STRICT RULES:
- NEWS KI STORY sunao — facts, impact, significance
- HEADLINE MAT PADHO — screen pe already dikh raha hai
- ~90-100 words — exactly 30 seconds ke liye
- Ek concrete fact ya stat zaroor ho
- End mein ek line jo viewer ko sochne pe majboor kare
- Hindi dominant, English sirf proper nouns ke liye
- FORBIDDEN: "yaar", "sun", "bhai", "dosto", "chaliye", "dekhte hain"
- Sirf bolne wala text — koi heading, bullet, asterisk nahi

Narration:"""}])
        narration = resp.choices[0].message.content.strip()
        import re as _re
        narration = _re.sub(r'\*+', '', narration).strip()
        words = narration.split()
        # Zyada lambi narration = 80s+ ki boring reel. ~45s (110 words) pe cap karo.
        if len(words) > 110:
            trimmed = " ".join(words[:110])
            cut = max(trimmed.rfind("."), trimmed.rfind("!"), trimmed.rfind("?"))
            narration = trimmed[:cut + 1] if cut > 200 else trimmed + "."
            print(f"      Narration {len(words)} words thi — {len(narration.split())} pe trim ki")
            return narration
        if len(words) >= 20:
            print(f"      Narration ({len(words)} words, style {style_idx+1})")
            return narration
        print(f"      Narration bahut chhoti ({len(words)} words) — fallback use kar raha hoon")
    except Exception as e:
        print(f"      Narration error: {e}")

    # Fallback — AI fail ho to bhi 25-30s ka bolne layak text bane (5s ki reel na bane)
    body_txt = (news_item.get("body", "") if news_item else "") or summary
    parts = [p.strip() for p in [headline, summary, body_txt] if p and p.strip()]
    seen, out = set(), []
    for p in parts:
        if p.lower() not in seen:
            seen.add(p.lower())
            out.append(p if p.endswith((".", "!", "?")) else p + ".")
    fallback = " ".join(out)[:900]
    print(f"      Fallback narration ({len(fallback.split())} words)")
    return fallback


def process_video_for_reel(video_path: str, headline: str, summary: str,
                           narration: str = "", news_item: dict = None) -> str | None:
    """Video ko 9:16 Reel format mein trim karo + text overlay + Hindi TTS audio"""
    import subprocess
    try:
        tmp_dir     = tempfile.gettempdir()
        ts          = int(time.time())
        base_path   = os.path.join(tmp_dir, f"reel_base_{ts}.mp4")
        overlay_png = os.path.join(tmp_dir, f"overlay_{ts}.png")
        audio_path  = os.path.join(tmp_dir, f"narration_{ts}.mp3")
        out_path    = os.path.join(tmp_dir, f"reel_{ts}.mp4")

        # Step 1: TTS pehle banao — video ki length isi pe decide hogi
        tts_text  = narration if narration else f"{headline}. {summary}"
        has_audio = generate_tts_audio(tts_text, audio_path)
        if has_audio:
            print("      TTS narration ready")

        # Audio duration detect karo — video utna hi loop hoga (bolna beech mein na kate)
        reel_dur = 30.0
        if has_audio and os.path.exists(audio_path):
            try:
                probe = subprocess.run([
                    "ffprobe", "-v", "quiet", "-print_format", "json",
                    "-show_streams", audio_path
                ], capture_output=True, timeout=10)
                streams  = json.loads(probe.stdout).get("streams", [{}])
                reel_dur = float(streams[0].get("duration", 0)) or 0
                if reel_dur <= 0:
                    raise ValueError("ffprobe se duration nahi mili")
                reel_dur = min(reel_dur + 0.5, 88.0)   # thoda buffer, Instagram limit 90s
                print(f"      Audio duration: {reel_dur:.1f}s — video isi tak loop hoga")
            except Exception as pe:
                # ffprobe na ho to word-count se estimate (Hindi TTS ~2.3 words/sec)
                reel_dur = min(max(len(tts_text.split()) / 2.3 + 2.0, 15.0), 88.0)
                print(f"      ffprobe unavailable ({pe}) — estimated {reel_dur:.1f}s")

        # Step 2: Video crop + resize — narration khatam hone tak loop
        crop = subprocess.run([
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", video_path,
            "-t", str(reel_dur),
            "-vf", "crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=720:1280",
            "-r", "30",
            "-c:v", "libx264", "-an", "-preset", "fast", "-crf", "28",
            base_path
        ], capture_output=True, timeout=240)

        if crop.returncode != 0 or not os.path.exists(base_path):
            print(f"      Crop fail: {crop.stderr[-100:].decode(errors='ignore')}")
            return None

        # Step 3: Full-frame overlay — logo top-left + text bar bottom
        FRAME_W, FRAME_H, BAR_H = 720, 1280, 310
        overlay = Image.new("RGBA", (FRAME_W, FRAME_H), (0, 0, 0, 0))
        ov_draw = ImageDraw.Draw(overlay)

        # Logo — top-left (baaki channels jaisa)
        if os.path.exists(LOGO_PATH):
            try:
                logo_img = Image.open(LOGO_PATH).convert("RGB")
                logo_w = 120
                logo_h = int(logo_img.height * (logo_w / logo_img.width))
                logo_img = logo_img.resize((logo_w, logo_h), Image.LANCZOS)
                lx, ly, pad = 28, 40, 8
                ov_draw.rounded_rectangle(
                    [lx - pad, ly - pad, lx + logo_w + pad, ly + logo_h + pad],
                    radius=10, fill=(255, 255, 255, 255)
                )
                overlay.paste(logo_img, (lx, ly))
            except Exception as le:
                print(f"      Logo error: {le}")

        # Text ko frame ke andar wrap karo — warna headline right side se kat jati hai
        PAD, MAX_W = 22, FRAME_W - 44

        def wrap_px(text, font, max_px):
            lines, line = [], ""
            for word in text.split():
                test = f"{line} {word}".strip()
                if ov_draw.textlength(test, font=font) > max_px and line:
                    lines.append(line)
                    line = word
                else:
                    line = test
            if line:
                lines.append(line)
            return lines

        font_head, font_body = get_font(44), get_font(28)
        head_lines = wrap_px(headline, font_head, MAX_W)[:2]
        body_lines = wrap_px(summary,  font_body, MAX_W)[:3]

        bar_y = FRAME_H - BAR_H
        ov_draw.rectangle([0, bar_y, FRAME_W, FRAME_H], fill=(0, 0, 0, 190))

        y = bar_y + 20
        for line in head_lines:
            ov_draw.text((PAD, y), line, font=font_head, fill=(255, 255, 255, 255))
            y += 54
        y += 6
        for line in body_lines:
            ov_draw.text((PAD, y), line, font=font_body, fill=(220, 220, 220, 245))
            y += 38

        date_str = datetime.now().strftime("%d %b %Y")
        ov_draw.text((PAD, FRAME_H - 38), f"@atlantis_news_ai  •  {date_str}",
                     font=get_font(24), fill=(170, 170, 170, 230))
        overlay.save(overlay_png, "PNG")

        # Step 4: ffmpeg — video + overlay + audio combine (narration pura chalega)
        if has_audio:
            result = subprocess.run([
                "ffmpeg", "-y",
                "-i", base_path, "-i", overlay_png, "-i", audio_path,
                "-filter_complex",
                "[0:v][1:v]overlay=0:0[vout];"
                "[2:a]volume=1.5[aout]",
                "-map", "[vout]", "-map", "[aout]",
                "-c:v", "libx264", "-c:a", "aac",
                "-preset", "fast", "-crf", "28",
                out_path   # no -shortest, no atrim — narration kabhi kate nahi
            ], capture_output=True, timeout=240)
        else:
            result = subprocess.run([
                "ffmpeg", "-y",
                "-i", base_path, "-i", overlay_png,
                "-filter_complex", "[0:v][1:v]overlay=0:0[out]",
                "-map", "[out]", "-c:v", "libx264",
                "-preset", "fast", "-crf", "28", out_path
            ], capture_output=True, timeout=180)

        for p in [base_path, overlay_png, audio_path]:
            try: os.remove(p)
            except: pass

        if result.returncode == 0 and os.path.exists(out_path):
            size_bytes = os.path.getsize(out_path)
            size_kb    = size_bytes // 1024
            print(f"      Reel ready: {size_kb}KB {'(with audio)' if has_audio else '(silent)'}")
            if size_bytes < 10_000:
                print(f"      WARNING: reel too small ({size_bytes}B) — skipping")
                return None
            return out_path
        print(f"      ffmpeg error: {result.stderr[-300:].decode(errors='ignore')}")
    except Exception as e:
        print(f"      Reel process error: {e}")
    return None


def upload_video_to_github(video_path: str) -> str | None:
    """Video GitHub Contents API pe upload karo (wildlife agent style — reliable)"""
    gh_token = os.getenv("GH_PAT") or os.getenv("GITHUB_TOKEN")
    repo     = os.getenv("GITHUB_REPOSITORY")
    if not gh_token or not repo:
        print("      GitHub token ya repo missing")
        return None
    try:
        import base64
        with open(video_path, "rb") as f:
            content = base64.b64encode(f.read()).decode()
        filename = f"news_reel_{int(time.time())}.mp4"
        api_url  = f"https://api.github.com/repos/{repo}/contents/reels/{filename}"
        size_mb  = os.path.getsize(video_path) // 1024 // 1024
        print(f"      GitHub upload ({size_mb}MB)...")
        resp = requests.put(
            api_url,
            headers={"Authorization": f"token {gh_token}",
                     "Content-Type": "application/json"},
            json={"message": f"reel: {filename}", "content": content, "branch": "main"},
            timeout=300
        )
        url = resp.json().get("content", {}).get("download_url")
        if url:
            print(f"      GitHub URL: {url[:80]}")
            return url
        print(f"      GitHub upload error: {resp.json()}")
    except Exception as e:
        print(f"      GitHub upload error: {e}")
    return None


def upload_video_free(video_path: str) -> str | None:
    """GitHub Release pe upload — most reliable from GH Actions"""
    url = upload_video_to_github(video_path)
    if url:
        return url
    print("      GitHub upload fail — koi URL nahi mili")
    return None


def post_reel_to_instagram(video_url: str, caption: str) -> str | None:
    """Instagram pe Reel post karo — media_id return karo"""
    print(f"\n[4/4] Reel post kar raha hoon...")
    if not INSTAGRAM_TOKEN or not INSTAGRAM_ACCOUNT_ID:
        return "dry_run"
    try:
        resp = requests.post(
            f"https://graph.facebook.com/v25.0/{INSTAGRAM_ACCOUNT_ID}/media",
            data={"video_url": video_url, "caption": caption,
                  "media_type": "REELS", "access_token": INSTAGRAM_TOKEN},
            timeout=20
        )
        container_id = resp.json().get("id")
        if not container_id:
            print(f"      Reel container error: {resp.json()}")
            return None

        for _ in range(12):
            time.sleep(8)
            status = requests.get(
                f"https://graph.facebook.com/v25.0/{container_id}",
                params={"fields": "status_code", "access_token": INSTAGRAM_TOKEN},
                timeout=10
            ).json()
            code = status.get("status_code", "")
            print(f"      Status: {code}")
            if code == "FINISHED":
                break
            if code == "ERROR":
                print(f"      Reel processing error")
                return None

        pub = requests.post(
            f"https://graph.facebook.com/v25.0/{INSTAGRAM_ACCOUNT_ID}/media_publish",
            data={"creation_id": container_id, "access_token": INSTAGRAM_TOKEN},
            timeout=15
        )
        media_id = pub.json().get("id")
        if media_id:
            print(f"      Reel posted! ID: {media_id}")
            return media_id
        print(f"      Reel publish error: {pub.json()}")
    except Exception as e:
        print(f"      Reel error: {e}")
    return None


def post_video_to_instagram(video_url: str, caption: str, hashtags: str) -> bool:
    """Legacy wrapper — naya post_reel_to_instagram use karo"""
    return post_reel_to_instagram(video_url, f"{caption}\n\n{hashtags}") is not None


def upload_image(image_path: str) -> str | None:
    """Local image ko ImgBB pe upload karo — free"""
    try:
        import base64
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")
        resp = requests.post("https://api.imgbb.com/1/upload", data={
            "key": IMGBB_API_KEY,
            "image": img_b64,
        }, timeout=30)
        url = resp.json()["data"]["url"]
        print(f"      ImgBB upload: {url}")
        return url
    except Exception as e:
        print(f"      Upload error: {e}")
    return None


# --- Step 3b: News Card Banana (agar image na mile) ---------------------------
def create_news_card(title: str, source: str, emoji_title: str = "Breaking News") -> str:
    """Pillow se ek clean news card image banao — 100% free, no copyright"""
    print("      News card bana raha hoon (Pillow)...")

    width, height = 1080, 1080
    img = Image.new("RGB", (width, height), color=(15, 15, 30))
    draw = ImageDraw.Draw(img)

    # Background gradient effect (simple rectangles)
    for i in range(20):
        draw.rectangle([0, height - (i * 55), width, height], fill=(26, 26, 60))

    # Top accent bar
    draw.rectangle([0, 0, width, 8], fill=(255, 80, 80))

    # Source
    draw.text((54, 40), "@atlantis_news_ai",
              fill=(180, 180, 180))

    # Emoji title
    draw.text((54, 110), emoji_title, fill=(255, 80, 80))

    # Main headline — word wrap manually
    words = title.split()
    lines, line = [], ""
    for w in words:
        test = f"{line} {w}".strip()
        if len(test) > 28:
            lines.append(line)
            line = w
        else:
            line = test
    if line:
        lines.append(line)

    y = 220
    for l in lines[:6]:
        draw.text((54, y), l, fill=(255, 255, 255))
        y += 80

    # Bottom bar
    draw.rectangle([0, height - 70, width, height], fill=(255, 80, 80))
    draw.text((54, height - 48), "Follow for daily news updates", fill=(255, 255, 255))

    path = os.path.join(tempfile.gettempdir(), f"news_card_{int(time.time())}.jpg")
    img.save(path, "JPEG", quality=95)
    print(f"      Card saved: {path}")
    return upload_image(path)


def image_palette(img: Image.Image):
    """Image ke dominant hue se accent aur bar colors nikalo"""
    sample = img.resize((80, 80), Image.LANCZOS).convert("RGB")
    pixels = list(sample.getdata())
    n = len(pixels)
    avg_r = sum(p[0] for p in pixels) // n
    avg_g = sum(p[1] for p in pixels) // n
    avg_b = sum(p[2] for p in pixels) // n
    h, s, v = colorsys.rgb_to_hsv(avg_r / 255, avg_g / 255, avg_b / 255)
    # Accent: same hue, vivid & bright
    accent = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(h, min(s + 0.35, 1.0), 0.90))
    # Bar base: same hue, very dark (gradient stays readable)
    bar    = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(h, min(s + 0.2, 0.85), 0.18))
    return accent, bar


# --- Logo Watermark -----------------------------------------------------------
LOGO_PATH = os.path.join(os.path.dirname(__file__), "atlantis_news_ai.png")

def add_logo_watermark(image_url: str, title: str = "", source: str = "", summary: str = "") -> str | None:  # source kept for API compat
    """News image pe text overlay + logo — Indian news page style"""
    try:
        import io

        resp = requests.get(image_url, timeout=15)
        if resp.status_code != 200:
            return image_url

        news_img = Image.open(io.BytesIO(resp.content)).convert("RGBA")

        # 1080x1080 square crop
        w, h = news_img.size
        side = min(w, h)
        news_img = news_img.crop(((w-side)//2, (h-side)//2,
                                   (w+side)//2, (h+side)//2))
        news_img = news_img.resize((1080, 1080), Image.LANCZOS)

        draw = ImageDraw.Draw(news_img)

        # Image ka dominant palette
        accent_color, bar_base = image_palette(news_img)

        # --- Gradient bar — bottom 38%, image-matched color ---
        bar_top = int(1080 * 0.62)
        overlay = Image.new("RGBA", (1080, 1080), (0, 0, 0, 0))
        ov_draw = ImageDraw.Draw(overlay)
        for i in range(1080 - bar_top):
            alpha = int(220 * (i / (1080 - bar_top)))
            ov_draw.line([(0, bar_top + i), (1080, bar_top + i)],
                         fill=(*bar_base, alpha))
        news_img = Image.alpha_composite(news_img, overlay)
        draw = ImageDraw.Draw(news_img)

        # --- Accent top bar (image-matched color) ---
        draw.rectangle([0, 0, 1080, 10], fill=(*accent_color, 255))

        # --- Source + date (small) ---
        font_title   = get_font(52)
        font_summary = get_font(32)
        font_source  = get_font(32)

        date_str = datetime.now().strftime("%d %b %Y")
        src_color = tuple(min(255, int(c * 1.4 + 60)) for c in accent_color)
        src_label = f"{source}  •  " if source else ""
        draw.text((30, bar_top + 18), f"{src_label}{date_str}  •  @atlantis_news_ai",
                  font=font_source, fill=(*src_color, 255))

        # --- Headline word-wrap (max 2 lines) ---
        y = bar_top + 68
        if title:
            words = title.split()
            lines, line = [], ""
            for w_word in words:
                test = f"{line} {w_word}".strip()
                if len(test) > 28:
                    lines.append(line)
                    line = w_word
                else:
                    line = test
            if line:
                lines.append(line)
            for l in lines[:2]:
                draw.text((30, y), l, font=font_title, fill=(255, 255, 255, 255))
                y += 62

        # --- Summary (1-2 lines below headline) ---
        if summary:
            y += 8
            words = summary.split()
            lines, line = [], ""
            for w_word in words:
                test = f"{line} {w_word}".strip()
                if len(test) > 38:
                    lines.append(line)
                    line = w_word
                else:
                    line = test
            if line:
                lines.append(line)
            for l in lines[:3]:
                draw.text((30, y), l, font=font_summary, fill=(230, 230, 230, 245))
                y += 40

        # --- Logo (bottom-right, inside bar) ---
        if os.path.exists(LOGO_PATH):
            logo = Image.open(LOGO_PATH).convert("RGBA")

            # White/light background transparent karo
            r, g, b, a = logo.split()
            pixels = list(logo.getdata())
            new_pixels = [
                (pr, pg, pb, 0) if pr > 220 and pg > 220 and pb > 220 else (pr, pg, pb, pa)
                for pr, pg, pb, pa in pixels
            ]
            logo.putdata(new_pixels)

            logo_w = int(1080 * 0.10)
            logo_h = int(logo.height * (logo_w / logo.width))
            logo = logo.resize((logo_w, logo_h), Image.LANCZOS)
            pad = 4
            lx = 1080 - logo_w - 20
            ly = 1080 - logo_h - 20
            draw.rectangle([lx - pad, ly - pad, lx + logo_w + pad, ly + logo_h + pad],
                           fill=(255, 255, 255, 230))
            news_img.paste(logo, (lx, ly), logo)

        final = news_img.convert("RGB")
        path = os.path.join(tempfile.gettempdir(), f"styled_{int(time.time())}.jpg")
        final.save(path, "JPEG", quality=92)
        new_url = upload_image(path)
        return new_url if new_url else image_url

    except Exception as e:
        print(f"      Overlay error: {e} — original image use kar raha hoon")
        return image_url


# --- Story Card + Story Post --------------------------------------------------
def generate_story_question(news_title: str) -> str:
    """News ke baare mein ek engaging poll-style question banao"""
    try:
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            max_tokens=60,
            messages=[{"role": "user", "content": f"""
News: {news_title[:150]}

Ek short Hinglish question banao (max 8 words) jo readers ko comment karne pe encourage kare.
Format: "Aapka kya kehna hai?" style — yes/no ya opinion type.
Sirf question do, koi explanation nahi.
"""}]
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return "Aapka kya kehna hai? Comment karo!"


def create_question_story(question: str, news_title: str) -> str | None:
    """Sirf question wali alag story — bright design, comment encourage karo"""
    print("      Question story bana raha hoon...")
    try:
        width, height = 1080, 1920
        img = Image.new("RGB", (width, height), color=(220, 40, 40))
        draw = ImageDraw.Draw(img)

        # Diagonal stripe pattern background
        for i in range(0, width + height, 60):
            draw.line([(i, 0), (0, i)], fill=(200, 30, 30), width=30)

        # White center card
        card_y1, card_y2 = 400, 1400
        draw.rectangle([60, card_y1, width - 60, card_y2],
                       fill=(255, 255, 255), outline=(220, 40, 40), width=6)

        font_big   = get_font(80)
        font_mid   = get_font(50)
        font_small = get_font(38)

        # "Aapka Opinion?" header
        draw.text((80, card_y1 + 40), "Aapka Opinion?", font=font_mid, fill=(220, 40, 40))
        draw.line([(80, card_y1 + 110), (width - 80, card_y1 + 110)], fill=(220, 40, 40), width=3)

        # News context (small)
        ctx = news_title[:60] + "..." if len(news_title) > 60 else news_title
        draw.text((80, card_y1 + 130), ctx, font=font_small, fill=(120, 120, 120))

        # Big question text
        words = question.split()
        lines, line = [], ""
        for w in words:
            test = f"{line} {w}".strip()
            if len(test) > 16:
                lines.append(line)
                line = w
            else:
                line = test
        if line:
            lines.append(line)

        y = card_y1 + 280
        for l in lines[:5]:
            draw.text((80, y), l, font=font_big, fill=(20, 20, 20))
            y += 110

        # Comment CTA
        draw.rectangle([80, card_y2 - 160, width - 80, card_y2 - 60],
                       fill=(220, 40, 40))
        draw.text((100, card_y2 - 135), "Comment mein batao!  👇",
                  font=font_mid, fill=(255, 255, 255))

        # Bottom branding
        draw.text((80, card_y2 + 30), "@atlantis_news_ai",
                  font=font_small, fill=(255, 255, 255))

        path = os.path.join(tempfile.gettempdir(), f"qstory_{int(time.time())}.jpg")
        img.save(path, "JPEG", quality=95)
        url = upload_image(path)
        if url:
            print(f"      Question story ready!")
        return url
    except Exception as e:
        print(f"      Question story error: {e}")
        return None


def create_story_card(title: str, source: str,
                      emoji_title: str = "Breaking News") -> str | None:
    """1080x1920 vertical story card banao"""
    print("      Story card bana raha hoon...")
    try:
        width, height = 1080, 1920
        img = Image.new("RGB", (width, height), color=(8, 8, 20))
        draw = ImageDraw.Draw(img)

        # Gradient background
        for i in range(height):
            shade = int(8 + (i / height) * 30)
            draw.line([(0, i), (width, i)], fill=(shade, shade, shade + 20))

        # Top red accent
        draw.rectangle([0, 0, width, 12], fill=(220, 40, 40))

        font_big   = get_font(72)
        font_mid   = get_font(44)
        font_small = get_font(34)

        # BREAKING label
        draw.rectangle([60, 180, 520, 260], fill=(220, 40, 40))
        draw.text((80, 188), "  BREAKING NEWS  ", font=font_small, fill=(255, 255, 255))

        # Emoji title
        draw.text((60, 300), emoji_title, font=font_mid, fill=(255, 80, 80))

        # Source
        draw.text((60, 380), "@atlantis_news_ai",
                  font=font_small, fill=(160, 160, 160))

        # Headline word-wrap
        words = title.split()
        lines, line = [], ""
        for w in words:
            test = f"{line} {w}".strip()
            if len(test) > 20:
                lines.append(line)
                line = w
            else:
                line = test
        if line:
            lines.append(line)

        y = 520
        for l in lines[:8]:
            draw.text((60, y), l, font=font_big, fill=(255, 255, 255))
            y += 100

        # Bottom bar
        draw.rectangle([0, height - 120, width, height], fill=(220, 40, 40))
        draw.text((60, height - 85), "@atlantis_news_ai  •  Daily News Updates",
                  font=font_mid, fill=(255, 255, 255))

        path = os.path.join(tempfile.gettempdir(), f"story_{int(time.time())}.jpg")
        img.save(path, "JPEG", quality=95)
        url = upload_image(path)
        if url:
            print(f"      Story card ready!")
        return url
    except Exception as e:
        print(f"      Story card error: {e}")
        return None


def post_story(image_url: str) -> bool:
    """Instagram Story post karo"""
    if not INSTAGRAM_TOKEN or not INSTAGRAM_ACCOUNT_ID:
        return False
    print(f"      Story post kar raha hoon...")
    try:
        resp = requests.post(
            f"https://graph.facebook.com/v25.0/{INSTAGRAM_ACCOUNT_ID}/media",
            data={"image_url": image_url, "media_type": "STORIES",
                  "access_token": INSTAGRAM_TOKEN},
            timeout=15
        )
        container_id = resp.json().get("id")
        if not container_id:
            print(f"      Story container error: {resp.json()}")
            return False

        time.sleep(3)
        pub = requests.post(
            f"https://graph.facebook.com/v25.0/{INSTAGRAM_ACCOUNT_ID}/media_publish",
            data={"creation_id": container_id, "access_token": INSTAGRAM_TOKEN},
            timeout=15
        )
        if pub.json().get("id"):
            print(f"      Story posted! ID: {pub.json()['id']}")
            return True
        else:
            print(f"      Story publish error: {pub.json()}")
            return False
    except Exception as e:
        print(f"      Story error: {e}")
        return False


# --- Step 4: Instagram Post ---------------------------------------------------
def post_to_instagram(image_path: str, caption: str) -> str | None:
    """Meta Graph API se Instagram pe post karo — returns media_id"""
    print(f"\n[4/4] Instagram pe post kar raha hoon...")

    if not INSTAGRAM_TOKEN or not INSTAGRAM_ACCOUNT_ID:
        print("      Dry run — credentials nahi hain")
        return "dry_run"

    try:
        upload_resp = requests.post(
            f"https://graph.facebook.com/v25.0/{INSTAGRAM_ACCOUNT_ID}/media",
            data={"image_url": image_path, "caption": caption, "access_token": INSTAGRAM_TOKEN},
            timeout=15
        )
        container_id = upload_resp.json().get("id")
        if not container_id:
            print(f"      Upload error: {upload_resp.json()}")
            return None

        time.sleep(3)
        pub_resp = requests.post(
            f"https://graph.facebook.com/v25.0/{INSTAGRAM_ACCOUNT_ID}/media_publish",
            data={"creation_id": container_id, "access_token": INSTAGRAM_TOKEN},
            timeout=15
        )
        media_id = pub_resp.json().get("id")
        if media_id:
            print(f"      Post successful! ID: {media_id}")
            return media_id
        else:
            print(f"      Publish error: {pub_resp.json()}")
            return None

    except Exception as e:
        print(f"      Instagram error: {e}")
        return None


def post_carousel_to_instagram(image_urls: list, caption: str) -> str | None:
    """Multiple images ka carousel post karo — max 10 slides"""
    print(f"\n[4/4] Carousel post kar raha hoon ({len(image_urls)} slides)...")
    if not INSTAGRAM_TOKEN or not INSTAGRAM_ACCOUNT_ID:
        return "dry_run"
    try:
        # Step 1: har image ka carousel item container banao
        child_ids = []
        for url in image_urls[:10]:
            resp = requests.post(
                f"https://graph.facebook.com/v25.0/{INSTAGRAM_ACCOUNT_ID}/media",
                data={"image_url": url, "is_carousel_item": "true",
                      "access_token": INSTAGRAM_TOKEN},
                timeout=15
            )
            cid = resp.json().get("id")
            if cid:
                child_ids.append(cid)
                time.sleep(1)

        if len(child_ids) < 2:
            print(f"      Carousel ke liye kam images — single post karunga")
            return None

        # Step 2: carousel container banao
        carousel_resp = requests.post(
            f"https://graph.facebook.com/v25.0/{INSTAGRAM_ACCOUNT_ID}/media",
            data={"media_type": "CAROUSEL", "children": ",".join(child_ids),
                  "caption": caption, "access_token": INSTAGRAM_TOKEN},
            timeout=15
        )
        container_id = carousel_resp.json().get("id")
        if not container_id:
            print(f"      Carousel container error: {carousel_resp.json()}")
            return None

        time.sleep(3)

        # Step 3: publish
        pub = requests.post(
            f"https://graph.facebook.com/v25.0/{INSTAGRAM_ACCOUNT_ID}/media_publish",
            data={"creation_id": container_id, "access_token": INSTAGRAM_TOKEN},
            timeout=15
        )
        media_id = pub.json().get("id")
        if media_id:
            print(f"      Carousel posted! ID: {media_id}")
            return media_id
        print(f"      Carousel publish error: {pub.json()}")
        return None
    except Exception as e:
        print(f"      Carousel error: {e}")
        return None


def auto_first_comment(media_id: str, hashtags: str) -> None:
    """Post ke baad hashtags first comment mein daalo — caption clean dikhti hai"""
    if not INSTAGRAM_TOKEN or media_id == "dry_run":
        return
    if not hashtags:
        print(f"      First comment skip — hashtags empty")
        return
    for attempt in range(3):
        try:
            resp = requests.post(
                f"https://graph.facebook.com/v25.0/{media_id}/comments",
                data={"message": hashtags, "access_token": INSTAGRAM_TOKEN},
                timeout=15
            )
            data = resp.json()
            if data.get("id"):
                print(f"      First comment (hashtags) posted!")
                return
            else:
                print(f"      First comment attempt {attempt+1} error: {data}")
                if attempt < 2:
                    time.sleep(6)
        except Exception as e:
            print(f"      First comment attempt {attempt+1} exception: {e}")
            if attempt < 2:
                time.sleep(6)


def generate_reply(comment_text: str, post_caption: str) -> str:
    """Groq se comment ka friendly Hinglish reply banao"""
    try:
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            max_tokens=80,
            messages=[{"role": "user", "content": f"""
Tu ek Indian Instagram news page ka community manager hai.

Post context: {post_caption[:150]}
Comment: {comment_text[:200]}

1-2 line ka short, genuine Hinglish reply likho:
- Warm aur friendly tone
- Agar question hai to brief answer do
- Agar opinion/praise hai to acknowledge karo
- 1-2 emojis use kar sakte ho
- "Thanks for watching" jaisa generic bilkul mat likho

Sirf reply text do.
"""}]
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return "Shukriya! Aisi news ke liye follow karte rahiye. 🙏"


def reply_to_recent_comments() -> None:
    """Last 5 posts ke unanswered comments pe AI se auto-reply karo"""
    if not INSTAGRAM_TOKEN or not INSTAGRAM_ACCOUNT_ID:
        print("      [Comments] Token ya Account ID missing — skip")
        return
    print(f"\n[Comments] Naye comments check kar raha hoon...")

    try:
        from datetime import timezone
        media_resp = requests.get(
            f"https://graph.facebook.com/v25.0/{INSTAGRAM_ACCOUNT_ID}/media",
            params={"fields": "id,caption,timestamp", "limit": 5,
                    "access_token": INSTAGRAM_TOKEN},
            timeout=10
        )
        media_data = media_resp.json()
        if "error" in media_data:
            print(f"      [Comments] Media fetch failed: {media_data['error']}")
            return

        posts = media_data.get("data", [])
        print(f"      [Comments] {len(posts)} recent posts mili")
        replied = 0

        for post in posts:
            post_id = post["id"]
            comments_resp = requests.get(
                f"https://graph.facebook.com/v25.0/{post_id}/comments",
                params={"fields": "id,text,username,timestamp,replies{id}",
                        "access_token": INSTAGRAM_TOKEN},
                timeout=10
            )
            comments_data = comments_resp.json()
            if "error" in comments_data:
                print(f"      [Comments] Post {post_id} comments fetch failed: {comments_data['error']}")
                continue

            all_comments = comments_data.get("data", [])
            print(f"      [Comments] Post {post_id}: {len(all_comments)} comments")

            for comment in all_comments:
                # Skip if already replied to
                if comment.get("replies", {}).get("data"):
                    print(f"        Skip (already replied): {comment.get('text','')[:40]}")
                    continue
                # Skip comments older than 48 hours
                try:
                    now_ts = datetime.now(timezone.utc).timestamp()
                    comment_ts = datetime.fromisoformat(
                        comment["timestamp"].replace("Z", "+00:00")
                    ).timestamp()
                    age_hrs = (now_ts - comment_ts) / 3600
                    if age_hrs > 48:
                        print(f"        Skip (too old {age_hrs:.0f}h): {comment.get('text','')[:40]}")
                        continue
                except Exception as te:
                    print(f"        Timestamp parse error: {te}")

                text = comment.get("text", "")
                username = comment.get("username", "")
                print(f"      Replying to @{username}: {text[:60]}")

                reply = generate_reply(text, post.get("caption", ""))
                reply_resp = requests.post(
                    f"https://graph.facebook.com/v25.0/{comment['id']}/replies",
                    data={"message": reply, "access_token": INSTAGRAM_TOKEN},
                    timeout=10
                )
                reply_data = reply_resp.json()
                if reply_data.get("id"):
                    print(f"        Replied: {reply[:60]}")
                    replied += 1
                    time.sleep(3)
                else:
                    print(f"        Reply failed: {reply_data}")

        print(f"      [Comments] Total replied: {replied}")
    except Exception as e:
        import traceback
        print(f"      [Comments] Exception: {e}")
        print(traceback.format_exc())


# --- Main Agent Loop ----------------------------------------------------------
def run_agent():
    print("=" * 55)
    print("  Instagram News Agent Starting...")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 55)

    # Token refresh karo (60-day long-lived token)
    refresh_token()

    # 1a. Copyright-free official sources — PRIMARY (PIB, DD News, UN, Wikinews etc.)
    from concurrent.futures import ThreadPoolExecutor, as_completed
    all_news = []
    official_sources = [
        fetch_pib_rss,
        fetch_dd_news_rss,
        fetch_newsonair_rss,
        fetch_un_news_rss,
        fetch_wikinews_rss,
        fetch_ein_presswire_rss,
        fetch_who_rss,
    ]
    print("\n[Fetch] Copyright-free sources se news fetch kar raha hoon...")
    with ThreadPoolExecutor(max_workers=7) as ex:
        futs = {ex.submit(fn): fn.__name__ for fn in official_sources}
        for fut in as_completed(futs):
            try:
                result = fut.result()
                if isinstance(result, list): all_news.extend(result)
            except Exception as e:
                print(f"      Source error: {e}")
    print(f"      Official sources: {len(all_news)} items")

    # 1b. DuckDuckGo — SIRF FALLBACK (facts rewrite, fair use) — agar < 6 items mile
    if len(all_news) < 6:
        print("[Fetch] DuckDuckGo fallback (facts rewrite)...")
        for topic in NEWS_TOPICS[:3]:
            results = fetch_news(topic, max_results=3)
            all_news.extend(results)

    # Sirf wahi news jisme image ho
    all_news = [n for n in all_news if n.get("image")]
    print(f"      Image wali news: {len(all_news)}")

    if not all_news:
        print("Koi image wali news nahi mili. Agent band ho raha hai.")
        return

    # 2. Duplicate check — recently posted titles fetch karo
    print(f"\n[Duplicate Check] Recent posts check kar raha hoon...")
    recent_titles = get_recently_posted_titles()
    USED_VIDEO_IDS.update(load_posted_videos())   # pehle use hui videos repeat na hon
    all_news = [n for n in all_news
                if not is_duplicate(n.get("title", ""), recent_titles)]
    print(f"      Duplicate hataane ke baad: {len(all_news)} news")

    if not all_news:
        print("Sab news already post ho chuki hai. Skip.")
        return

    # 3. AI se top CAROUSEL_SLIDES news select karo
    news_list = smart_plan(all_news, count=CAROUSEL_SLIDES)

    # 4. Har news ko alag post karo
    posted = 0
    first_news = None
    first_content = None

    for news in news_list:
        print(f"\n{'-'*50}")
        print(f"News: {news.get('title', '')[:70]}...")
        print(f"Importance: {news.get('_importance', '?')}/10")

        content = generate_caption(news)

        img_url = add_logo_watermark(
            news.get("image"),
            title=content.get("headline") or news.get("title", ""),
            source=news.get("source", ""),
            summary=content.get("image_summary", "")
        )
        if not img_url:
            continue

        # 5. Government news → PIB Reel try karo, else photo post
        GOVT_KEYWORDS = ["government", "ministry", "modi", "parliament", "cabinet",
                         "scheme", "policy", "budget", "rbi", "supreme court",
                         "defence", "defense", "army", "election", "commission"]
        is_govt = any(k in news.get("title", "").lower() for k in GOVT_KEYWORDS)

        media_id = None
        if is_govt:
            print(f"      Government news — PIB Reel try kar raha hoon...")
            pib_path = fetch_news_video_free(content.get("image_keyword", news.get("title", "")[:30]))
            if pib_path:
                narration = generate_narration(
                    news,
                    content.get("headline") or news.get("title", ""),
                    content.get("image_summary", "")
                )
                reel_path = process_video_for_reel(
                    pib_path,
                    content.get("headline") or news.get("title", ""),
                    content.get("image_summary", ""),
                    narration=narration
                )
                if reel_path:
                    video_url = upload_video_free(reel_path)
                    if video_url:
                        media_id = post_reel_to_instagram(video_url, content.get("caption", ""))
                    try:
                        os.remove(pib_path)
                        os.remove(reel_path)
                    except Exception:
                        pass

        if not media_id:
            media_id = post_to_instagram(img_url, content.get("caption", ""))

        if media_id:
            save_posted_title(news.get("title", ""))
            time.sleep(8)
            hashtags = content.get("hashtags", "#India #News #BreakingNews")
            auto_first_comment(media_id, hashtags)
            print(f"      Post ho gaya!")
            posted += 1
            if first_news is None:
                first_news = news
                first_content = content
            time.sleep(POST_DELAY)

    # Comments check — posting ho ya na ho, purane posts pe reply karo
    reply_to_recent_comments()

    if not posted:
        print("Koi post nahi ho saka.")
        return

    # Story sirf 8am aur 6pm run pe (2 stories daily)
    hour = datetime.now().hour
    if hour in (8, 18) and first_news and first_content:
        story_url = create_story_card(
            first_news.get("title", ""),
            first_news.get("source", ""),
            first_content.get("emoji_title", "Breaking News")
        )
        if story_url:
            post_story(story_url)
            time.sleep(3)

        question = generate_story_question(first_news.get("title", ""))
        q_story_url = create_question_story(question, first_news.get("title", ""))
        if q_story_url:
            post_story(q_story_url)

    print(f"\n{'='*55}")
    print(f"  Agent complete! {posted} post kiya gaya.")
    print("=" * 55)


# --- Breaking News Checker ----------------------------------------------------
def check_breaking_news() -> None:
    """Har 30 min run hota hai — sirf importance 9-10 news turant post karo"""
    print("=" * 55)
    print("  Breaking News Check...")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 55)

    # Last 2 ghante mein koi post hua? Recent check
    recent_titles = get_recently_posted_titles()
    USED_VIDEO_IDS.update(load_posted_videos())   # pehle use hui videos repeat na hon

    # Fresh news fetch — last 1 hour
    breaking_news = []
    for topic in ["India breaking news urgent today", "India major incident just now"]:
        try:
            with DDGS() as ddgs:
                results = list(ddgs.news(topic, max_results=5, timelimit="d"))
            breaking_news.extend(results)
        except Exception:
            pass

    breaking_news = [n for n in breaking_news if n.get("image")]
    breaking_news = [n for n in breaking_news
                     if not is_duplicate(n.get("title", ""), recent_titles)]

    if not breaking_news:
        print("  Koi breaking news nahi — skip.")
        return

    # Quick importance check via Groq
    news_str = "\n".join([
        f"{i+1}. {n.get('title', '')[:100]}"
        for i, n in enumerate(breaking_news[:8])
    ])
    try:
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": f"""
Ye Indian news headlines hain. Kaunsa sabse breaking/critical hai?
Sirf wahi select karo jiska importance 9 ya 10 ho (national crisis level).
Agar koi 9+ nahi hai, index -1 do.

{news_str}

JSON: {{"index": 0, "importance": 9, "reason": "why"}}
"""}],
            response_format={"type": "json_object"}
        )
        result = json.loads(resp.choices[0].message.content)
        idx = result.get("index", -1)
        importance = result.get("importance", 0)

        if idx < 0 or importance < 9 or idx >= len(breaking_news):
            print(f"  Koi 9+ importance news nahi mili — skip.")
            return

        news = breaking_news[idx]
        print(f"  BREAKING ({importance}/10): {news.get('title', '')[:70]}")

        # Post karo
        content = generate_caption(news)
        img_url = add_logo_watermark(
            news.get("image"),
            title=content.get("headline") or news.get("title", ""),
            source=news.get("source", ""),
            summary=content.get("image_summary", "")
        )
        if not img_url:
            return

        media_id = post_to_instagram(img_url, content.get("caption", ""))
        if media_id:
            save_posted_title(news.get("title", ""))
            time.sleep(8)
            auto_first_comment(media_id, content.get("hashtags", "#India #BreakingNews #IndianNews"))
            print("  Breaking news post ho gaya!")

    except Exception as e:
        print(f"  Breaking check error: {e}")

    # Har 30 min breaking check ke saath comments bhi check karo
    reply_to_recent_comments()


def post_pib_reel() -> None:
    """PIB YouTube se news video download karke Instagram Reel post karo"""
    print("=" * 55)
    print("  PIB Reel Agent Starting...")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 55)

    # Aaj ke top government news topics
    topics = [
        "India government scheme today",
        "PM Modi announcement today",
        "India Parliament news today",
        "India economy RBI today",
        "India defense ministry today",
    ]

    recent_titles = get_recently_posted_titles()
    USED_VIDEO_IDS.update(load_posted_videos())   # pehle use hui videos repeat na hon

    for topic in topics:
        print(f"\n[PIB] Topic: {topic}")
        pib_path = fetch_news_video_free(topic)
        if not pib_path:
            continue

        # Caption ke liye Groq se title generate karo
        try:
            client = Groq(api_key=GROQ_API_KEY)
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                max_tokens=300,
                messages=[{"role": "user", "content": f"""
Ye ek PIB India (Press Information Bureau) ka official government video hai.
Topic: {topic}

Instagram Reel ke liye JSON banao:
{{
  "headline": "5-7 word Hinglish headline — punchy, bold",
  "summary": "1-2 sentence Hinglish summary (max 20 words) — kya hai ye video",
  "caption": "4-6 line Hinglish caption — informative, engaging, no hashtags",
  "hashtags": "#India #Government #PIB #IndiaNews #BreakingNews #ModiGovernment (15 hashtags)"
}}
"""}],
                response_format={"type": "json_object"}
            )
            content = json.loads(resp.choices[0].message.content)
        except Exception:
            content = {
                "headline": "India Government Update",
                "summary": "PIB India ki taraf se official government news.",
                "caption": "India mein aaj ka government update. PIB India se latest khabar.",
                "hashtags": "#India #Government #PIBIndia #IndiaNews #BreakingNews"
            }

        if is_duplicate(content.get("headline", topic), recent_titles):
            print(f"      Duplicate — skip")
            try:
                os.remove(pib_path)
            except Exception:
                pass
            continue

        narration = generate_narration(
            {"title": content.get("headline", topic), "body": content.get("summary", "")},
            content.get("headline", "India Government News"),
            content.get("summary", "")
        )
        reel_path = process_video_for_reel(
            pib_path,
            content.get("headline", "India Government News"),
            content.get("summary", ""),
            narration=narration
        )
        try:
            os.remove(pib_path)
        except Exception:
            pass

        if not reel_path:
            continue

        video_url = upload_video_free(reel_path)
        try:
            os.remove(reel_path)
        except Exception:
            pass

        if not video_url:
            continue

        media_id = post_reel_to_instagram(video_url, content.get("caption", ""))
        if media_id:
            save_posted_title(content.get("headline", topic))
            time.sleep(8)
            auto_first_comment(media_id, content.get("hashtags", "#India #PIBIndia #Government"))
            print(f"  Reel post ho gaya!")
            break  # Ek reel kaafi hai per run

    print("\n  PIB Reel Agent done.")


if __name__ == "__main__":
    import sys
    if "--breaking" in sys.argv:
        check_breaking_news()
    elif "--reel" in sys.argv:
        post_pib_reel()
    else:
        run_agent()
