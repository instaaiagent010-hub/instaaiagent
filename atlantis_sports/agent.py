"""
Atlantis Sports — Instagram & YouTube Agent
============================================
Sports news automatically fetch karke Instagram Reels + YouTube Shorts pe post karta hai.
Same infrastructure as atlantis_wildlife — sports branding ke saath.

Run:
    python agent.py
"""

import os
import sys
import json
import time
import tempfile
import colorsys
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from dotenv import load_dotenv
from ddgs import DDGS
from PIL import Image, ImageDraw
from groq import Groq

_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(_env)

# --- Config -------------------------------------------------------------------
GROQ_API_KEY         = os.getenv("GROQ_API_KEY")
PEXELS_API_KEY       = os.getenv("PEXELS_API_KEY", "")
INSTAGRAM_TOKEN      = os.getenv("SPORTS_INSTAGRAM_ACCESS_TOKEN")
INSTAGRAM_ACCOUNT_ID = os.getenv("SPORTS_INSTAGRAM_ACCOUNT_ID")
IMGBB_API_KEY        = os.getenv("IMGBB_API_KEY")

CHANNEL_HANDLE  = "@atlantis_sports"
POST_DELAY      = 20
CAROUSEL_SLIDES = 1

LOGO_PATH       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "atlantis_sports.png")
HISTORY_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "posted_history.json")
YT_HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "youtube_history.json")

PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")

# YouTube config
YOUTUBE_CLIENT_ID     = os.getenv("YOUTUBE_CLIENT_ID", "")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET", "")
YOUTUBE_REFRESH_TOKEN = os.getenv("YOUTUBE_REFRESH_TOKEN", "")
YOUTUBE_ONLY          = os.getenv("YOUTUBE_ONLY", "false").lower() == "true"

if YOUTUBE_ONLY:
    HISTORY_FILE    = YT_HISTORY_FILE
    CAROUSEL_SLIDES = 1

SPORTS_TOPICS = [
    "cricket IPL India latest news 2026",
    "football FIFA Premier League latest news",
    "tennis Grand Slam Wimbledon latest",
    "Olympics athletics world championship 2026",
    "India sports news today kabaddi badminton",
]

SPORTS_VIDEO_KEYWORDS = {
    "cricket":    "cricket batting bowling match stadium",
    "ipl":        "cricket IPL match stadium crowd",
    "football":   "football soccer goal match stadium",
    "fifa":       "football soccer world cup match",
    "tennis":     "tennis serve match court player",
    "wimbledon":  "tennis Wimbledon grass court match",
    "badminton":  "badminton shuttle smash match court",
    "olympics":   "olympics athletics sprint jump sports",
    "hockey":     "field hockey match stick ball goal",
    "kabaddi":    "kabaddi sport India match tackle",
    "basketball": "basketball dunk court game match",
    "wrestling":  "wrestling sport athlete match arena",
    "athletics":  "running sprint athletics track field",
    "boxing":     "boxing match ring punch athlete",
    "swimming":   "swimming pool race athlete splash",
}


# --- Shared utilities ---------------------------------------------------------
def get_font(size: int):
    from PIL import ImageFont
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
        "/usr/share/fonts/noto/NotoSansDevanagari-Regular.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/Nirmala.ttf",
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


def image_palette(img: Image.Image):
    sample = img.resize((80, 80), Image.LANCZOS).convert("RGB")
    raw = sample.tobytes()
    n = 80 * 80
    avg_r = sum(raw[0::3]) // n
    avg_g = sum(raw[1::3]) // n
    avg_b = sum(raw[2::3]) // n
    h, s, v = colorsys.rgb_to_hsv(avg_r / 255, avg_g / 255, avg_b / 255)
    accent = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(h, min(s + 0.35, 1.0), 0.90))
    bar    = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(h, min(s + 0.2, 0.85), 0.18))
    return accent, bar


def clean_title(title: str) -> str:
    import re
    return re.sub(r'\s*[-–|]\s*[A-Z][A-Za-z0-9 &.]{2,40}$', '', title).strip()


# --- News Fetch ---------------------------------------------------------------
def fetch_news(topic: str, max_results: int = 5) -> list[dict]:
    print(f"\n[Fetch] Sports news: '{topic}'")
    strategies = [{"timelimit": "d"}, {"timelimit": "w"}, {}]
    for attempt, params in enumerate(strategies):
        try:
            time.sleep(attempt * 4)
            with DDGS() as ddgs:
                results = list(ddgs.news(topic, max_results=max_results * 3, **params))
            if not results:
                raise Exception("No results")
            news = []
            for n in results:
                n["title"] = clean_title(n.get("title", ""))
                news.append(n)
            news = news[:max_results]
            if news:
                print(f"      {len(news)} news mili")
                return news
        except Exception as e:
            print(f"      Attempt {attempt+1} failed: {e}")
    return []


# --- Sports RSS Sources -------------------------------------------------------

def _parse_rss(url: str, source_name: str, max_results: int = 3) -> list[dict]:
    """Generic RSS/Atom feed parser"""
    import xml.etree.ElementTree as ET
    import re as _re
    try:
        resp = requests.get(url, timeout=12, headers={"User-Agent": "AtlantisSportsBot/1.0"})
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.content)
        ns = {"media": "http://search.yahoo.com/mrss/"}
        items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        news = []
        for item in items:
            t_el = item.find("title") or item.find("{http://www.w3.org/2005/Atom}title")
            title = (t_el.text or "").strip() if t_el is not None else ""
            if not title:
                continue
            title = clean_title(title)
            d_el = (item.find("description") or
                    item.find("{http://www.w3.org/2005/Atom}summary") or
                    item.find("{http://www.w3.org/2005/Atom}content"))
            raw  = (d_el.text or "") if d_el is not None else ""
            desc = _re.sub(r'<[^>]+>', '', raw).strip()[:500]
            img  = ""
            mc   = item.find("media:content", ns)
            if mc is not None:
                img = mc.get("url", "")
            if not img:
                enc = item.find("enclosure")
                if enc is not None and "image" in enc.get("type", ""):
                    img = enc.get("url", "")
            if not img:
                m = _re.search(r'<img[^>]+src=["\']([^"\']+)["\']', raw)
                if m:
                    img = m.group(1)
            date_el = item.find("pubDate") or item.find("{http://www.w3.org/2005/Atom}published")
            date = (date_el.text or "")[:10] if date_el is not None else ""
            news.append({"title": title, "body": desc, "image": img,
                         "source": source_name, "date": date,
                         "url": ""})
            if len([n for n in news if n["image"]]) >= max_results:
                break
        result = [n for n in news if n["image"]][:max_results] or news[:max_results]
        print(f"      {source_name} RSS: {len(result)} items")
        return result
    except Exception as e:
        print(f"      {source_name} RSS error: {e}")
    return []


def fetch_bbc_sport() -> list[dict]:
    return _parse_rss("https://feeds.bbci.co.uk/sport/rss.xml", "BBC Sport", 3)

def fetch_bbc_cricket() -> list[dict]:
    return _parse_rss("https://feeds.bbci.co.uk/sport/cricket/rss.xml", "BBC Cricket", 2)

def fetch_bbc_football() -> list[dict]:
    return _parse_rss("https://feeds.bbci.co.uk/sport/football/rss.xml", "BBC Football", 2)

def fetch_guardian_sport() -> list[dict]:
    return _parse_rss("https://www.theguardian.com/sport/rss", "Guardian Sport", 3)

def fetch_ndtv_sports() -> list[dict]:
    return _parse_rss("https://sports.ndtv.com/rss/all", "NDTV Sports", 3)

def fetch_espncricinfo() -> list[dict]:
    return _parse_rss("https://www.espncricinfo.com/rss/content/story/feeds/0.xml", "ESPNcricinfo", 3)

def fetch_skysports() -> list[dict]:
    return _parse_rss("https://www.skysports.com/rss/12040", "Sky Sports", 2)

def fetch_goal_football() -> list[dict]:
    return _parse_rss("https://www.goal.com/feeds/en/news", "Goal.com Football", 2)

def fetch_athletics_world() -> list[dict]:
    return _parse_rss("https://worldathletics.org/news/rss", "World Athletics", 2)


# --- History ------------------------------------------------------------------
def load_posted_history() -> set:
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("titles", []))
    except Exception:
        pass
    return set()


def load_posted_images() -> set:
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("images", []))
    except Exception:
        pass
    return set()


def save_posted_title(title: str, image_url: str = "") -> None:
    try:
        existing = {}
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)
        titles = existing.get("titles", [])
        images = existing.get("images", [])
        normalized = title.lower().strip()[:120]
        if normalized not in titles:
            titles.append(normalized)
        titles = titles[-150:]
        if image_url:
            img_key = image_url.strip()[:120]
            if img_key not in images:
                images.append(img_key)
            images = images[-150:]
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump({"titles": titles, "images": images,
                       "updated": datetime.now().isoformat()}, f, ensure_ascii=False, indent=2)
        import subprocess
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        subprocess.run(["git", "add", "posted_history.json"], cwd=repo_dir)
        result = subprocess.run(
            ["git", "commit", "-m", "chore: update sports posted history [skip ci]"],
            cwd=repo_dir, capture_output=True
        )
        if result.returncode == 0:
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=repo_dir, capture_output=True)
            subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=repo_dir)
        print(f"      History saved ({len(titles)} titles, {len(images)} images)")
    except Exception as e:
        print(f"      History save error: {e}")


def get_recently_posted_titles() -> set:
    titles = load_posted_history()
    if not INSTAGRAM_TOKEN or not INSTAGRAM_ACCOUNT_ID:
        return titles
    try:
        resp = requests.get(
            f"https://graph.facebook.com/v25.0/{INSTAGRAM_ACCOUNT_ID}/media",
            params={"fields": "caption", "limit": 20, "access_token": INSTAGRAM_TOKEN},
            timeout=10
        )
        for post in resp.json().get("data", []):
            cap = post.get("caption", "")
            if cap:
                titles.add(cap[:120].lower())
    except Exception:
        pass
    return titles


def is_duplicate(news_title: str, recent_titles: set) -> bool:
    words = set(news_title.lower().split())
    for stored in recent_titles:
        stored_words = set(stored.split())
        overlap = len(words & stored_words) / max(len(words), 1)
        if overlap >= 0.35:
            return True
    return False


def is_image_duplicate(image_url: str, recent_images: set) -> bool:
    if not image_url:
        return False
    return image_url.strip()[:120] in recent_images


# --- AI Planning --------------------------------------------------------------
def smart_plan(all_news: list[dict], count: int = CAROUSEL_SLIDES) -> list[dict]:
    print(f"\n[AI] {len(all_news)} sports items analyze kar raha hoon...")
    news_list_str = "\n".join([
        f"{i+1}. [{n.get('source','')}] {n.get('title','')[:100]}"
        for i, n in enumerate(all_news[:12])
    ])
    try:
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=500,
            messages=[{"role": "user", "content": f"""
Ye sports content hai. Visual aur excitement score do (1-10):
- 9-10: Big match result, record broken, major upset, trophy win
- 7-8: Star player news, tournament update, comeback story
- 5-6: Team selection, injury news, transfer
- 1-4: Background/opinion piece, no visual appeal

Priority: ESPNcricinfo > BBC Cricket > BBC Sport > NDTV Sports > Guardian > BBC Football > Goal.com

{news_list_str}

TOP {count} choose karo. JSON:
{{
  "plan": [
    {{"index": 0, "wow_score": 9, "reason": "why this is exciting"}}
  ],
  "strategy": "one line content strategy"
}}"""}],
            response_format={"type": "json_object"}
        )
        result = json.loads(resp.choices[0].message.content)
        print(f"      Strategy: {result.get('strategy', '')}")
        planned = []
        for item in result.get("plan", []):
            idx = item.get("index", 0)
            if 0 <= idx < len(all_news):
                news = all_news[idx].copy()
                news["_wow_score"] = item.get("wow_score", 7)
                planned.append(news)
        return planned[:count] if planned else all_news[:count]
    except Exception as e:
        print(f"      Planning error: {e}")
        return all_news[:count]


# --- Caption Generation -------------------------------------------------------
def generate_caption(news_item: dict) -> dict:
    print(f"\n[Caption] Generate kar raha hoon...")
    client = Groq(api_key=GROQ_API_KEY)
    prompt = f"""
Tu {CHANNEL_HANDLE} ka Instagram content creator hai — ye ek SPORTS channel hai.

Content:
Title: {news_item.get('title', '')}
Description: {news_item.get('body', '')[:500]}
Source: {news_item.get('source', '')}

TONE — SPORTS COMMENTATOR, EXCITING:
- Ek passionate sports commentator ki tarah likho
- "Kya shot tha!", "History ban gayi!", "Ye moment yaad rahega!"
- Readers ko feel ho ki match abhi chal raha hai — real-time energy
- Hindi+English mix (Hinglish), young Indian sports fans ke liye
- 6-8 lines, exciting but factual
- End mein ek bold prediction ya trivia fact
- CAPTION MEIN HASHTAG NAHI — sirf "hashtags" field mein

JSON:
{{
  "caption": "sports commentator style caption, no hashtags",
  "hashtags": "#Cricket #Football #Sports #IPL #India #Sportsmanship #AtlantisSports #SportNews #GameDay #ChampionsMindset #WinnersMindset #SportMotivation #SportsIndia #HindiSports #CricketLovers #FootballIndia #SportsShorts #SportHighlights #AthleteLife #Champion (20 tags)",
  "video_search_query": "3-5 word stock footage search term — exact sport + action + setting. Examples: 'cricket batsman hitting six stadium', 'football goalkeeper diving save', 'tennis player serving court', 'Olympic sprinter finish line'. NEVER generic like 'sports match' or 'cricket action'.",
  "emoji_title": "emoji + short title",
  "headline": "5-8 word Hinglish headline — SIRF confirmed facts, spelling 100% correct",
  "image_summary": "2-3 Hinglish sentences (max 35 words) — match result / key fact only"
}}
"""
    try:
        message = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=900,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        result = json.loads(message.choices[0].message.content.strip())
        import re
        caption = result.get("caption", "")
        caption = re.sub(r'\s*#\w+', '', caption).strip()
        result["caption"] = caption
        preview = result['caption'][:60].encode('ascii', errors='ignore').decode()
        print(f"      Caption ready: {preview}...")
        return result
    except Exception as e:
        print(f"      Caption error: {e}")
        return {
            "caption": news_item.get('title', 'Sports Update!'),
            "hashtags": "#Sports #Cricket #Football #India #AtlantisSports",
            "video_search_query": "sports action match",
            "emoji_title": "🏆 Sports",
            "headline": news_item.get('title', 'Sports')[:50],
            "image_summary": "",
        }


# --- Image Upload to ImgBB ----------------------------------------------------
def upload_image(file_path: str) -> str | None:
    if not IMGBB_API_KEY:
        return None
    try:
        with open(file_path, "rb") as f:
            import base64
            b64 = base64.b64encode(f.read()).decode("utf-8")
        resp = requests.post(
            "https://api.imgbb.com/1/upload",
            data={"key": IMGBB_API_KEY, "image": b64},
            timeout=30
        )
        url = resp.json().get("data", {}).get("url")
        if url:
            print(f"      ImgBB upload: {url}")
        return url
    except Exception as e:
        print(f"      ImgBB error: {e}")
        return None


# --- Image Overlay ------------------------------------------------------------
def add_watermark(image_url: str, title: str = "", source: str = "", summary: str = "") -> str | None:
    try:
        import io
        resp = requests.get(image_url, timeout=15, headers={"User-Agent": "AtlantisSportsBot/1.0"})
        if resp.status_code != 200:
            print(f"      Image download failed: {resp.status_code}")
            return None
        news_img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
        w, h = news_img.size
        side = min(w, h)
        news_img = news_img.crop(((w-side)//2, (h-side)//2, (w+side)//2, (h+side)//2))
        news_img = news_img.resize((1080, 1080), Image.LANCZOS)
        draw = ImageDraw.Draw(news_img)
        accent_color, bar_base = image_palette(news_img)
        bar_top = int(1080 * 0.62)
        overlay = Image.new("RGBA", (1080, 1080), (0, 0, 0, 0))
        ov_draw = ImageDraw.Draw(overlay)
        for i in range(1080 - bar_top):
            alpha = int(220 * (i / (1080 - bar_top)))
            ov_draw.line([(0, bar_top + i), (1080, bar_top + i)], fill=(*bar_base, alpha))
        news_img = Image.alpha_composite(news_img, overlay)
        draw = ImageDraw.Draw(news_img)
        draw.rectangle([0, 0, 1080, 10], fill=(*accent_color, 255))
        font_title   = get_font(52)
        font_summary = get_font(32)
        font_source  = get_font(32)
        date_str  = datetime.now().strftime("%d %b %Y")
        src_color = tuple(min(255, int(c * 1.4 + 60)) for c in accent_color)
        src_label = f"{source}  •  " if source else ""
        draw.text((30, bar_top + 18), f"{src_label}{date_str}  •  {CHANNEL_HANDLE}",
                  font=font_source, fill=(*src_color, 255))
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
        if os.path.exists(LOGO_PATH):
            logo = Image.open(LOGO_PATH).convert("RGB")
            logo_w = int(1080 * 0.10)
            logo_h = int(logo.height * (logo_w / logo.width))
            logo = logo.resize((logo_w, logo_h), Image.LANCZOS)
            pad = 4
            lx, ly = 1080 - logo_w - 20, 1080 - logo_h - 20
            draw.rectangle([lx-pad, ly-pad, lx+logo_w+pad, ly+logo_h+pad], fill=(255, 255, 255, 255))
            news_img.paste(logo, (lx, ly))
        final = news_img.convert("RGB")
        path = os.path.join(tempfile.gettempdir(), f"sports_{int(time.time())}.jpg")
        final.save(path, "JPEG", quality=92)
        url = upload_image(path)
        try:
            os.remove(path)
        except:
            pass
        if not url:
            print(f"      ImgBB upload failed — skipping post")
        return url
    except Exception as e:
        print(f"      Overlay error: {e}")
        return None


# --- Instagram Post -----------------------------------------------------------
def post_to_instagram(image_url: str, caption: str) -> str | None:
    print(f"\n[Instagram] Post kar raha hoon...")
    if not INSTAGRAM_TOKEN or not INSTAGRAM_ACCOUNT_ID:
        print("      Dry run — credentials nahi hain")
        return "dry_run"
    try:
        upload = requests.post(
            f"https://graph.facebook.com/v25.0/{INSTAGRAM_ACCOUNT_ID}/media",
            data={"image_url": image_url, "caption": caption, "access_token": INSTAGRAM_TOKEN},
            timeout=15
        )
        container_id = upload.json().get("id")
        if not container_id:
            print(f"      Upload error: {upload.json()}")
            return None
        time.sleep(3)
        pub = requests.post(
            f"https://graph.facebook.com/v25.0/{INSTAGRAM_ACCOUNT_ID}/media_publish",
            data={"creation_id": container_id, "access_token": INSTAGRAM_TOKEN},
            timeout=15
        )
        media_id = pub.json().get("id")
        if media_id:
            print(f"      Post successful! ID: {media_id}")
            return media_id
        print(f"      Publish error: {pub.json()}")
        return None
    except Exception as e:
        print(f"      Instagram error: {e}")
        return None


# --- Reel / Video Pipeline ---------------------------------------------------

def _download_video(url: str, prefix: str, min_size: int = 500_000) -> str | None:
    try:
        r = requests.get(url, timeout=90, stream=True,
                         headers={"User-Agent": "AtlantisSportsBot/1.0"})
        if r.status_code != 200:
            return None
        path = os.path.join(tempfile.gettempdir(), f"{prefix}_{int(time.time())}.mp4")
        with open(path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        size = os.path.getsize(path)
        if size >= min_size:
            print(f"      Downloaded {size//1024//1024}MB → {prefix}")
            return path
        os.remove(path)
    except Exception as e:
        print(f"      Download error ({prefix}): {e}")
    return None


def _yt_dlp(url: str, prefix: str) -> str | None:
    import subprocess
    try:
        path = os.path.join(tempfile.gettempdir(), f"{prefix}_{int(time.time())}.mp4")
        result = subprocess.run([
            "yt-dlp", url,
            "-f", "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format", "mp4",
            "-o", path, "--no-playlist", "--quiet", "--no-warnings",
        ], capture_output=True, timeout=120)
        if result.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 500_000:
            print(f"      yt-dlp OK: {os.path.getsize(path)//1024//1024}MB")
            return path
    except Exception as e:
        print(f"      yt-dlp error ({prefix}): {e}")
    return None


# ── Wikimedia Commons (sports footage) ────────────────────────────────────────
def fetch_wikimedia_video(keyword: str) -> tuple[str | None, str]:
    """Wikimedia Commons — CC-licensed sports footage"""
    import re as _re, subprocess as _sp, random
    try:
        search = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query", "list": "search", "format": "json",
                "srsearch": f"{keyword} sport filetype:webm OR filetype:ogv OR filetype:mp4",
                "srnamespace": "6", "srlimit": 12,
            }, timeout=10
        )
        results = search.json().get("query", {}).get("search", [])
        video_titles = [
            r["title"] for r in results
            if any(r["title"].lower().endswith(e) for e in (".webm", ".ogv", ".mp4"))
        ]
        if not video_titles:
            return None, ""

        random.shuffle(video_titles)
        for vtitle in video_titles[:6]:
            info = requests.get(
                "https://commons.wikimedia.org/w/api.php",
                params={"action": "query", "titles": vtitle,
                        "prop": "imageinfo", "iiprop": "url|size|mediatype",
                        "format": "json"},
                timeout=10
            )
            pages = info.json().get("query", {}).get("pages", {})
            for page in pages.values():
                ii   = page.get("imageinfo", [{}])[0]
                url  = ii.get("url", "")
                size = ii.get("size", 0)
                if not url or size < 500_000:
                    continue
                ext = url.rsplit(".", 1)[-1].lower().split("?")[0]
                if ext not in ("webm", "ogv", "mp4", "ogg"):
                    continue
                print(f"      Wikimedia video ({ext}): {vtitle[:50]}")
                tmp_path = os.path.join(tempfile.gettempdir(),
                                        f"sv_{int(time.time())}.{ext}")
                dl = requests.get(url, timeout=90, stream=True,
                                  headers={"User-Agent": "AtlantisSportsBot/1.0"})
                with open(tmp_path, "wb") as f:
                    for chunk in dl.iter_content(8192):
                        f.write(chunk)
                if os.path.getsize(tmp_path) < 500_000:
                    try: os.remove(tmp_path)
                    except: pass
                    continue
                if ext != "mp4":
                    mp4_path = tmp_path.rsplit(".", 1)[0] + ".mp4"
                    conv = _sp.run([
                        "ffmpeg", "-y", "-i", tmp_path,
                        "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-preset", "fast", "-crf", "22",
                        mp4_path
                    ], capture_output=True, timeout=120)
                    try: os.remove(tmp_path)
                    except: pass
                    if conv.returncode != 0 or not os.path.exists(mp4_path):
                        continue
                    tmp_path = mp4_path
                clean_title = vtitle.replace("File:", "").rsplit(".", 1)[0]
                print(f"      Wikimedia ready: {os.path.getsize(tmp_path)//1024//1024}MB")
                return tmp_path, clean_title
    except Exception as e:
        print(f"      Wikimedia video error: {e}")
    return None, ""


# ── Internet Archive (sports documentaries) ───────────────────────────────────
def fetch_archive_video(keyword: str) -> tuple[str | None, str]:
    """Internet Archive — CC0/public domain sports footage"""
    import random
    try:
        r = requests.get(
            "https://archive.org/advancedsearch.php",
            params={
                "q":      f"({keyword} sport) AND mediatype:movies",
                "fl[]":   ["identifier", "title"],
                "rows":   10,
                "output": "json",
            },
            timeout=12,
            headers={"User-Agent": "AtlantisSportsBot/1.0"}
        )
        docs = r.json().get("response", {}).get("docs", [])
        random.shuffle(docs)
        for doc in docs[:6]:
            identifier = doc.get("identifier", "")
            title      = doc.get("title", keyword)
            if not identifier:
                continue
            files_r = requests.get(
                f"https://archive.org/metadata/{identifier}/files",
                timeout=10
            )
            files = files_r.json().get("result", [])
            mp4_files = [
                f for f in files
                if f.get("name", "").lower().endswith(".mp4")
                and f.get("size", "0") != "0"
            ]
            mp4_files.sort(key=lambda f: int(f.get("size", 0) or 0), reverse=True)
            for f in mp4_files[:3]:
                url  = f"https://archive.org/download/{identifier}/{f['name']}"
                path = _download_video(url, "archive", min_size=300_000)
                if path:
                    print(f"      Archive.org sports: {title[:50]}")
                    return path, title
    except Exception as e:
        print(f"      Archive.org error: {e}")
    return None, ""


# ── Pixabay (CC0 sports stock) ────────────────────────────────────────────────
def fetch_pixabay_video(keyword: str) -> tuple[str | None, str]:
    if not PIXABAY_API_KEY:
        return None, ""
    try:
        r = requests.get(
            "https://pixabay.com/api/videos/",
            params={
                "key":        PIXABAY_API_KEY,
                "q":          keyword,
                "video_type": "film",
                "per_page":   10,
                "safesearch": "true",
            },
            timeout=10
        )
        hits = r.json().get("hits", [])
        import random
        random.shuffle(hits)
        for hit in hits[:5]:
            videos = hit.get("videos", {})
            for quality in ("large", "medium", "small"):
                url = videos.get(quality, {}).get("url", "")
                if url:
                    path = _download_video(url, "pixabay")
                    if path:
                        print(f"      Pixabay video: id={hit.get('id')}")
                        return path, keyword
    except Exception as e:
        print(f"      Pixabay error: {e}")
    return None, ""


# ── Pexels (primary stock footage — best keyword relevance) ────────────────────
def fetch_pexels_video(keyword: str) -> tuple[str | None, str]:
    if not PEXELS_API_KEY:
        return None, ""
    try:
        headers = {"Authorization": PEXELS_API_KEY}
        for orientation in ("portrait", "landscape"):
            resp = requests.get(
                "https://api.pexels.com/videos/search",
                params={"query": keyword, "per_page": 10, "orientation": orientation},
                headers=headers, timeout=10
            )
            videos = resp.json().get("videos", [])
            for video in videos:
                title = video.get("url", "").rstrip("/").split("/")[-1].replace("-", " ")
                for vf in sorted(video.get("video_files", []),
                                 key=lambda x: x.get("height", 0), reverse=True):
                    if vf.get("file_type") == "video/mp4" and vf.get("height", 0) >= 720:
                        url  = vf["link"]
                        path = _download_video(url, "pexels")
                        if path:
                            print(f"      Pexels: {title[:50]}")
                            return path, title or keyword
    except Exception as e:
        print(f"      Pexels video error: {e}")
    return None, ""


def fetch_article_video(article_url: str) -> str | None:
    """Try to extract direct MP4 from news article"""
    if not article_url or not article_url.startswith("http"):
        return None
    import re as _re
    try:
        resp = requests.get(article_url, timeout=10,
                            headers={"User-Agent": "Mozilla/5.0 AtlantisSportsBot"})
        html = resp.text
        m = _re.search(r'<meta[^>]+property=["\']og:video["\'][^>]+content=["\']([^"\']+)["\']', html)
        if not m:
            m = _re.search(r'<meta[^>]+content=["\']([^"\']+\.mp4[^"\']*)["\']', html)
        if not m:
            m = _re.search(r'["\']([^"\']+\.mp4)["\']', html)
        url = m.group(1) if m else ""
        if url and url.startswith("http"):
            path = _download_video(url, "article")
            if path:
                return path
    except Exception as e:
        print(f"      Article video error: {e}")
    return None


def fetch_sports_video(keyword: str, source: str = "", article_url: str = "") -> tuple[str | None, str]:
    """
    Video priority — specific AI keyword used throughout, NO source override.
      1. Article direct MP4
      2. Pexels     (best keyword relevance — specific sport + action)
      3. Pixabay    (CC0, good keyword matching)
      4. Wikimedia  (CC-licensed sports footage)
      5. Archive.org (CC0 sports documentaries)
      6. Last resort: Pexels + Archive with generic "sports" keyword
    """
    print(f"\n      [Video] '{keyword}' | source: {source}")

    # 1. Article direct MP4
    if article_url:
        path = fetch_article_video(article_url)
        if path:
            return path, keyword

    # 2. Pexels — best keyword-to-footage relevance
    if PEXELS_API_KEY:
        print(f"      Trying Pexels...")
        path, title = fetch_pexels_video(keyword)
        if path:
            return path, title or keyword

    # 3. Pixabay — CC0 sports stock
    if PIXABAY_API_KEY:
        print(f"      Trying Pixabay...")
        path, title = fetch_pixabay_video(keyword)
        if path:
            return path, title or keyword

    # 4. Wikimedia Commons — CC licensed sports clips
    print(f"      Trying Wikimedia...")
    path, title = fetch_wikimedia_video(keyword)
    if path:
        return path, title or keyword

    # 5. Internet Archive — sports documentaries
    print(f"      Trying Internet Archive...")
    path, title = fetch_archive_video(keyword)
    if path:
        return path, title or keyword

    # Last resort: retry with generic sports keyword
    if PEXELS_API_KEY:
        path, title = fetch_pexels_video("sports athlete action")
        if path:
            return path, title or "sports"
    path, title = fetch_archive_video("sports athletics")
    if path:
        return path, title or "sports"

    print(f"      No video found for '{keyword}'")
    return None, ""


# --- Narration Generation -----------------------------------------------------
def generate_narration(news_item: dict, headline: str, summary: str,
                       video_topic: str = "") -> str:
    """Sports commentary narration — Hindi, energetic, 30-45 seconds"""
    source = news_item.get("source", "")
    title  = news_item.get("title", "")
    body   = news_item.get("body", "")[:500]

    if video_topic and video_topic not in ("sports action match", ""):
        visual_context = (
            f"\nVIDEO MEIN KYA DIKH RAHA HAI: '{video_topic}'\n"
            f"Narration ISKO describe kare — viewer yahi dekh raha hai screen pe.\n"
            f"Sport ke action ko describe karo — jo video mein ho raha hai.\n"
        )
    else:
        visual_context = (
            "\nVideo mein sports action footage hai.\n"
            "Narration mein sports energy aur excitement capture karo.\n"
        )

    try:
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=420,
            messages=[{"role": "user", "content": f"""
Tu ek passionate Hindi sports commentator hai — jaise Ravi Shastri ya Harsha Bhogle ka Hindi version.
Ek 30-second sports Reel narration likho — energetic, dramatic, awe-inspiring.

News Topic: {title}
Details: {body}
Summary: {summary}
{visual_context}

SPORTS COMMENTATOR STYLE — STRICT:
- HEADLINE BILKUL MAT PADHO — screen pe already dikh raha hai
- ~90-100 words — exactly 30 seconds ke liye
- Action se shuru karo — "Aur woh shot!", "Ye moment history mein darz ho gaya!", "Dekho..."
- Player/team ko hero ki tarah present karo — unki mehnat, dedication, achievement
- Ek shocking stat ya record — poetic style mein
- End mein ek powerful line — fans ke liye motivation
- Hindi dominant, English sirf proper nouns ke liye (player names, team names)
- FORBIDDEN: "yaar", "sun", "bhai", "dosto", "chaliye", "dekhte hain"
- "..." = dramatic pause — use karo wisely
- Sirf bolne wala text — koi heading, bullet, asterisk nahi

Narration:"""}]
        )
        narration = resp.choices[0].message.content.strip()
        import re
        narration = re.sub(r'\*+', '', narration).strip()
        wc = len(narration.split())
        print(f"      Narration ({wc} words, commentator style)")
        return narration
    except Exception as e:
        print(f"      Narration error: {e}")
        return summary


# --- TTS (same as wildlife — AnanyaNeural primary) ----------------------------
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
    r = _sp.run(
        ["ffmpeg", "-y", "-i", path, "-af", filters, norm],
        capture_output=True, timeout=30
    )
    if r.returncode == 0 and os.path.exists(norm):
        os.replace(norm, path)


def _tts_edge(text: str, out_path: str) -> bool:
    import asyncio, edge_tts, re as _re
    VOICES = [
        ("hi-IN-AnanyaNeural", "-3%", "-1Hz", "+15%"),
        ("hi-IN-MadhurNeural", "-5%", "+0Hz", "+12%"),
        ("hi-IN-SwaraNeural",  "-5%", "-2Hz", "+15%"),
    ]
    clean = _re.sub(r'[*_`#~\[\]{}|<>\\]', '', text).strip()
    for voice, rate, pitch, vol in VOICES:
        try:
            comm = edge_tts.Communicate(clean, voice=voice,
                                        rate=rate, pitch=pitch, volume=vol)
            asyncio.run(comm.save(out_path))
            if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
                _normalize_audio(out_path)
                print(f"      TTS: {voice}")
                return True
        except Exception:
            continue
    return False


def generate_tts(text: str, out_path: str) -> bool:
    """Edge TTS — Hindi neural voice with audio normalization"""
    import re as _re
    clean = _re.sub(r'[*_`#~\[\]{}|<>\\]', '', text).strip()
    if not clean:
        return False
    if _tts_edge(clean, out_path):
        return True
    # gTTS last resort
    try:
        from gtts import gTTS
        gTTS(text=clean, lang="hi", slow=False).save(out_path)
        if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
            print(f"      TTS: gTTS fallback")
            return True
    except Exception as e:
        print(f"      gTTS error: {e}")
    return False


# --- Process Reel -------------------------------------------------------------
def process_reel(video_path: str, headline: str, summary: str,
                 narration: str = "", source: str = "") -> str | None:
    """Sports video ko Reel format mein convert karo"""
    import subprocess
    try:
        ts          = int(time.time())
        tmp         = tempfile.gettempdir()
        base_path   = os.path.join(tmp, f"sbase_{ts}.mp4")
        overlay_png = os.path.join(tmp, f"sovl_{ts}.png")
        audio_path  = os.path.join(tmp, f"stts_{ts}.mp3")
        out_path    = os.path.join(tmp, f"sreel_{ts}.mp4")

        # Step 1: TTS pehle — audio duration detect karo
        tts_text  = narration if narration else summary
        has_audio = generate_tts(tts_text, audio_path)

        reel_dur = 30.0
        if has_audio and os.path.exists(audio_path):
            try:
                import json as _json
                probe = subprocess.run([
                    "ffprobe", "-v", "quiet", "-print_format", "json",
                    "-show_streams", audio_path
                ], capture_output=True, timeout=10)
                streams = _json.loads(probe.stdout).get("streams", [{}])
                reel_dur = float(streams[0].get("duration", 30.0))
                reel_dur = min(reel_dur + 0.3, 58.0)
                print(f"      Audio duration: {reel_dur:.1f}s")
            except Exception:
                reel_dur = 30.0

        # Step 1b: Video → 1080x1920, hold last frame to match audio
        vf_main = (
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,"
            "tpad=stop=-1:stop_mode=clone"
        )
        crop = subprocess.run([
            "ffmpeg", "-y", "-i", video_path,
            "-t", str(reel_dur),
            "-vf", vf_main, "-r", "30",
            "-c:v", "libx264", "-profile:v", "high", "-level:v", "4.0",
            "-pix_fmt", "yuv420p", "-an", "-preset", "fast", "-crf", "22",
            base_path
        ], capture_output=True, timeout=180)

        if crop.returncode != 0 or not os.path.exists(base_path):
            vf_blur = (
                "[0:v]split=2[bg][fg];"
                "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,boxblur=30:3[bg_blur];"
                "[fg]scale=1080:608:force_original_aspect_ratio=decrease,"
                "pad=1080:608:(ow-iw)/2:(oh-ih)/2:black[fg_pad];"
                "[bg_blur][fg_pad]overlay=(W-w)/2:(H-h)/2,tpad=stop=-1:stop_mode=clone"
            )
            crop = subprocess.run([
                "ffmpeg", "-y", "-i", video_path,
                "-t", str(reel_dur), "-vf", vf_blur, "-r", "30",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-an", "-preset", "fast", "-crf", "22",
                base_path
            ], capture_output=True, timeout=180)

        if crop.returncode != 0 or not os.path.exists(base_path):
            print(f"      Crop fail: {crop.stderr[-200:].decode(errors='ignore')}")
            return None

        # Step 2: Overlay PNG (1080x1920) — sports theme (red/orange accent)
        FRAME_W  = 1080
        FRAME_H  = 1920
        BAR_H    = 460
        PAD_LEFT = 40
        PAD_RIGHT = 150
        MAX_W    = FRAME_W - PAD_LEFT - PAD_RIGHT
        font_head = get_font(52)
        font_body = get_font(33)
        font_foot = get_font(27)

        def wrap_px(text, font, max_px, draw_obj):
            words = text.split()
            lines, line = [], ""
            for word in words:
                test = f"{line} {word}".strip()
                if draw_obj.textlength(test, font=font) > max_px and line:
                    lines.append(line)
                    line = word
                else:
                    line = test
            if line:
                lines.append(line)
            return lines

        overlay = Image.new("RGBA", (FRAME_W, FRAME_H), (0, 0, 0, 0))
        ov_draw = ImageDraw.Draw(overlay)

        # Logo — top-left
        if os.path.exists(LOGO_PATH):
            try:
                logo_img = Image.open(LOGO_PATH).convert("RGB")
                logo_w = 160
                logo_h = int(logo_img.height * (logo_w / logo_img.width))
                logo_img = logo_img.resize((logo_w, logo_h), Image.LANCZOS)
                lx, ly = 40, 60
                pad = 10
                ov_draw.rounded_rectangle(
                    [lx - pad, ly - pad, lx + logo_w + pad, ly + logo_h + pad],
                    radius=12, fill=(255, 255, 255, 255)
                )
                overlay.paste(logo_img, (lx, ly))
            except Exception as le:
                print(f"      Logo error: {le}")

        # Bottom text bar — sports red/dark tint
        bar_y = FRAME_H - BAR_H
        for i in range(BAR_H):
            alpha = int(170 * (i / BAR_H) + 60)
            ov_draw.line([(0, bar_y + i), (FRAME_W, bar_y + i)],
                         fill=(20, 0, 0, min(alpha, 245)))   # dark red tint
        ov_draw.rectangle([0, bar_y, FRAME_W, bar_y + 6],
                          fill=(220, 30, 30, 255))            # red accent line

        y = bar_y + 24
        for line in wrap_px(headline, font_head, MAX_W, ov_draw)[:2]:
            ov_draw.text((PAD_LEFT, y), line, font=font_head, fill=(255, 255, 255, 255))
            y += 66

        y += 10
        for line in wrap_px(summary, font_body, MAX_W, ov_draw)[:3]:
            ov_draw.text((PAD_LEFT, y), line, font=font_body, fill=(255, 220, 200, 240))
            y += 44

        date_str = datetime.now().strftime("%d %b %Y")
        ov_draw.text((PAD_LEFT, FRAME_H - 44),
                     f"{CHANNEL_HANDLE}  •  {date_str}",
                     font=font_foot, fill=(220, 140, 130, 210))
        if source:
            font_src = get_font(22)
            src_text = f"© {source}"
            src_w    = ov_draw.textlength(src_text, font=font_src)
            ov_draw.text((FRAME_W - src_w - PAD_RIGHT - 10, FRAME_H - 40),
                         src_text, font=font_src, fill=(220, 160, 150, 180))

        overlay.save(overlay_png, "PNG")

        # Step 3: FFmpeg combine
        common = [
            "-c:v", "libx264", "-profile:v", "high", "-level:v", "4.0",
            "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "22",
            "-movflags", "+faststart"
        ]
        if has_audio:
            result = subprocess.run([
                "ffmpeg", "-y",
                "-i", base_path, "-i", overlay_png, "-i", audio_path,
                "-filter_complex",
                "[0:v][1:v]overlay=0:0[vout];[2:a]volume=1.5[aout]",
                "-map", "[vout]", "-map", "[aout]",
                "-c:a", "aac", "-b:a", "128k",
                *common, out_path
            ], capture_output=True, timeout=180)
        else:
            result = subprocess.run([
                "ffmpeg", "-y",
                "-i", base_path, "-i", overlay_png,
                "-filter_complex", "[0:v][1:v]overlay=0:0[out]",
                "-map", "[out]", *common, out_path
            ], capture_output=True, timeout=180)

        for p in [base_path, overlay_png, audio_path]:
            try: os.remove(p)
            except: pass

        if result.returncode == 0 and os.path.exists(out_path):
            size_mb = os.path.getsize(out_path) // 1024 // 1024
            print(f"      Reel ready: {size_mb}MB {'(with audio)' if has_audio else ''}")
            return out_path
        print(f"      FFmpeg error: {result.stderr[-150:].decode(errors='ignore')}")
    except Exception as e:
        print(f"      Reel process error: {e}")
    return None


# --- GitHub Video Upload ------------------------------------------------------
def upload_video_github(video_path: str) -> str | None:
    gh_token = (os.getenv("GH_PAT") or os.getenv("GITHUB_TOKEN") or "").strip()
    repo = os.getenv("GITHUB_REPOSITORY")
    if not gh_token or not repo:
        return None
    headers = {
        "Authorization": f"token {gh_token}",
        "Accept": "application/vnd.github.v3+json"
    }
    filename = f"sports_reel_{int(time.time())}.mp4"
    try:
        releases = requests.get(
            f"https://api.github.com/repos/{repo}/releases",
            headers=headers, timeout=10
        ).json()
        upload_url = None
        for rel in (releases if isinstance(releases, list) else []):
            if rel.get("tag_name") == "media-assets":
                upload_url = rel["upload_url"].split("{")[0]
                break
        if not upload_url:
            create = requests.post(
                f"https://api.github.com/repos/{repo}/releases",
                headers=headers,
                json={"tag_name": "media-assets", "name": "Media Assets",
                      "draft": False, "body": "Auto-generated sports reels"},
                timeout=10
            ).json()
            upload_url = create.get("upload_url", "").split("{")[0]
        if not upload_url:
            return None
        size_mb = os.path.getsize(video_path) // 1024 // 1024
        print(f"      GitHub upload ({size_mb}MB)...")
        with open(video_path, "rb") as f:
            up = requests.post(
                f"{upload_url}?name={filename}",
                headers={**headers, "Content-Type": "video/mp4"},
                data=f, timeout=300
            ).json()
        url = up.get("browser_download_url", "")
        if url:
            print(f"      Video URL: {url[:80]}")
            return url
    except Exception as e:
        print(f"      GitHub upload error: {e}")
    return None


# --- Instagram Reel -----------------------------------------------------------
def post_reel(video_url: str, caption: str) -> str | None:
    print(f"\n[Reel] Instagram pe post kar raha hoon...")
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
        time.sleep(5)
        for i in range(15):
            time.sleep(5 if i < 3 else 8)
            status = requests.get(
                f"https://graph.facebook.com/v25.0/{container_id}",
                params={"fields": "status_code", "access_token": INSTAGRAM_TOKEN},
                timeout=10
            ).json()
            code = status.get("status_code", "")
            print(f"      Reel status: {code}")
            if code == "FINISHED":
                break
            if code == "ERROR":
                return None
        pub = requests.post(
            f"https://graph.facebook.com/v25.0/{INSTAGRAM_ACCOUNT_ID}/media_publish",
            data={"creation_id": container_id, "access_token": INSTAGRAM_TOKEN},
            timeout=15
        )
        media_id = pub.json().get("id")
        if not media_id:
            print(f"      Reel publish error: {pub.json()}")
            return None
        time.sleep(4)
        verify = requests.get(
            f"https://graph.facebook.com/v25.0/{media_id}",
            params={"fields": "id,media_type,permalink", "access_token": INSTAGRAM_TOKEN},
            timeout=10
        ).json()
        if verify.get("id"):
            print(f"      Reel verified! {verify.get('permalink','')}")
            return media_id
        else:
            print(f"      Reel rejected silently: {verify}")
            return None
    except Exception as e:
        print(f"      Reel error: {e}")
    return None


# --- YouTube Upload -----------------------------------------------------------
def get_youtube_token() -> str | None:
    if not all([YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN]):
        return None
    try:
        resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id":     YOUTUBE_CLIENT_ID,
                "client_secret": YOUTUBE_CLIENT_SECRET,
                "refresh_token": YOUTUBE_REFRESH_TOKEN,
                "grant_type":    "refresh_token"
            },
            timeout=15
        )
        token = resp.json().get("access_token")
        if token:
            print(f"      YouTube token OK")
        return token
    except Exception as e:
        print(f"      YouTube token error: {e}")
    return None


def upload_youtube_short(video_path: str, title: str, description: str) -> str | None:
    token = get_youtube_token()
    if not token:
        return None
    try:
        video_size  = os.path.getsize(video_path)
        short_title = (title[:90] + " #Shorts") if len(title) <= 90 else (title[:87] + "... #Shorts")
        date_str    = datetime.now().strftime("%d %b %Y")
        body = {
            "snippet": {
                "title":       short_title,
                "description": (
                    f"{description}\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🏆 Atlantis Sports — Sports News in Hindi\n"
                    f"Subscribe for daily Sports Shorts!\n\n"
                    f"📅 {date_str}\n"
                    f"© Sources: BBC Sport, ESPNcricinfo, NDTV Sports, Guardian Sport\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"#Sports #Cricket #Football #Shorts #AtlantisSports "
                    f"#SportsShorts #CricketShorts #IndianSports #HindiSports"
                ),
                "tags": [
                    "Sports", "Cricket", "Football", "Shorts", "AtlantisSports",
                    "SportsShorts", "CricketShorts", "IndianSports", "HindiSports",
                    "IPL", "Tennis", "Olympics", "SportNews", "Champion"
                ],
                "categoryId":           "17",   # Sports
                "defaultLanguage":      "hi",
                "defaultAudioLanguage": "hi"
            },
            "status": {
                "privacyStatus":           "public",
                "selfDeclaredMadeForKids": False,
                "madeForKids":             False,
                "containsSyntheticMedia":  True
            }
        }
        init_resp = requests.post(
            "https://www.googleapis.com/upload/youtube/v3/videos"
            "?uploadType=resumable&part=snippet,status",
            headers={
                "Authorization":           f"Bearer {token}",
                "Content-Type":            "application/json",
                "X-Upload-Content-Type":   "video/mp4",
                "X-Upload-Content-Length": str(video_size)
            },
            json=body, timeout=30
        )
        upload_url = init_resp.headers.get("Location")
        if not upload_url:
            print(f"      YouTube init error: {init_resp.text[:200]}")
            return None
        print(f"      YouTube upload ({video_size // 1024 // 1024}MB)...")
        with open(video_path, "rb") as f:
            up_resp = requests.put(
                upload_url,
                headers={"Content-Type": "video/mp4", "Content-Length": str(video_size)},
                data=f, timeout=300
            )
        video_id = up_resp.json().get("id")
        if video_id:
            print(f"      YouTube Short: https://youtube.com/shorts/{video_id}")
            return video_id
        print(f"      YouTube error: {up_resp.text[:200]}")
    except Exception as e:
        print(f"      YouTube upload error: {e}")
    return None


def auto_first_comment(media_id: str, hashtags: str) -> None:
    if not INSTAGRAM_TOKEN or not hashtags or (media_id or "").startswith("yt_"):
        return
    if media_id in ("dry_run",):
        return
    for attempt in range(3):
        try:
            resp = requests.post(
                f"https://graph.facebook.com/v25.0/{media_id}/comments",
                data={"message": hashtags, "access_token": INSTAGRAM_TOKEN},
                timeout=15
            )
            if resp.json().get("id"):
                print(f"      Hashtag comment posted!")
                return
            if attempt < 2:
                time.sleep(6)
        except Exception:
            if attempt < 2:
                time.sleep(6)


# --- Main Agent ---------------------------------------------------------------
def run_agent():
    print("=" * 55)
    print(f"  Atlantis Sports Agent Starting...")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 55)

    from concurrent.futures import ThreadPoolExecutor, as_completed

    all_news = []

    rss_sources = [
        fetch_bbc_sport,
        fetch_bbc_cricket,
        fetch_bbc_football,
        fetch_guardian_sport,
        fetch_ndtv_sports,
        fetch_espncricinfo,
        fetch_skysports,
        fetch_goal_football,
        fetch_athletics_world,
    ]

    print("\n[Fetch] Parallel fetching all sports sources...")
    with ThreadPoolExecutor(max_workers=9) as ex:
        rss_futures = {ex.submit(fn): fn.__name__ for fn in rss_sources}
        for fut in as_completed(rss_futures):
            try:
                result = fut.result()
                if isinstance(result, list):
                    all_news.extend(result)
                elif isinstance(result, dict) and result:
                    all_news.append(result)
            except Exception as e:
                print(f"      Source error: {e}")

    # DuckDuckGo fallback if few results
    if len(all_news) < 3:
        for topic in SPORTS_TOPICS[:2]:
            results = fetch_news(topic, max_results=4)
            all_news.extend(results)

    all_news = [n for n in all_news if n.get("image")]
    print(f"      Image wali news: {len(all_news)}")

    if not all_news:
        print("Koi sports news nahi mili.")
        return

    all_news_raw   = all_news.copy()
    recent_titles  = get_recently_posted_titles()
    recent_images  = load_posted_images()
    all_news = [
        n for n in all_news
        if not is_duplicate(n.get("title", ""), recent_titles)
        and not is_image_duplicate(n.get("image", ""), recent_images)
    ]
    print(f"      Duplicate hataane ke baad: {len(all_news)}")

    if not all_news:
        print("      Sab duplicate — force post...")
        all_news = [n for n in all_news_raw
                    if n.get("source", "") in {"ESPNcricinfo", "BBC Cricket", "BBC Sport"}]
        if not all_news:
            all_news = all_news_raw[:CAROUSEL_SLIDES]

    news_list = smart_plan(all_news, count=CAROUSEL_SLIDES)
    posted = 0

    for i, news in enumerate(news_list):
        print(f"\n{'-'*50}")
        print(f"News: {news.get('title', '')[:70]}...")

        content  = generate_caption(news)
        headline = content.get("headline") or news.get("title", "")
        summary  = content.get("image_summary", "")
        hashtags = content.get("hashtags", "#Sports #Cricket #Football #India #AtlantisSports")
        caption  = content.get("caption", "")

        media_id = None
        keyword  = content.get("video_search_query", content.get("image_keyword", "sports action match"))

        # Fetch video FIRST — then narrate about what's in the video
        video_path, video_topic = fetch_sports_video(
            keyword, source=news.get("source", ""), article_url=news.get("url", "")
        )
        narration = generate_narration(news, headline, summary, video_topic=video_topic)

        if video_path:
            reel_path = process_reel(video_path, headline, summary, narration,
                                     source=news.get("source", ""))
            try:
                os.remove(video_path)
            except:
                pass

            if reel_path:
                # --- YouTube DISABLED — channel set up hone par uncomment karo ---
                # yt_id = None
                # if YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET and YOUTUBE_REFRESH_TOKEN:
                #     yt_id = upload_youtube_short(reel_path, headline, caption)
                # ------------------------------------------------------------------

                # Instagram only
                video_url = upload_video_github(reel_path)
                try:
                    os.remove(reel_path)
                except:
                    pass

                if video_url:
                    media_id = post_reel(video_url, caption)

        if not media_id:
                print("      Reel fail — photo post pe fallback")
                img_url = add_watermark(
                    news.get("image"),
                    title=headline,
                    source=news.get("source", ""),
                    summary=summary
                )
                if img_url:
                    media_id = post_to_instagram(img_url, caption)

        if media_id:
            save_posted_title(news.get("title", ""), image_url=news.get("image", ""))
            time.sleep(8)
            auto_first_comment(media_id, hashtags)
            print(f"      Post ho gaya!")
            posted += 1
            time.sleep(POST_DELAY)

    print(f"\n{'='*55}")
    print(f"  Agent complete! {posted}/{CAROUSEL_SLIDES} posts. (9 sources, runs/day)")
    print("=" * 55)


if __name__ == "__main__":
    run_agent()
