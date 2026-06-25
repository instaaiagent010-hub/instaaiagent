"""
Atlantis Wildlife — Instagram & YouTube Agent
==============================================
Wildlife, nature, animals content automatically fetch karke Instagram + YouTube pe post karta hai.
Same infrastructure as atlantis_space — wildlife branding ke saath.

Run:
    python atlantis_wildlife/agent.py
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
PEXELS_API_KEY       = os.getenv("PEXELS_API_KEY")
INSTAGRAM_TOKEN      = os.getenv("WILDLIFE_INSTAGRAM_ACCESS_TOKEN")
INSTAGRAM_ACCOUNT_ID = os.getenv("WILDLIFE_INSTAGRAM_ACCOUNT_ID")
IMGBB_API_KEY        = os.getenv("IMGBB_API_KEY")

CHANNEL_HANDLE  = "@atlantis_wildlife"
POST_DELAY      = 45
CAROUSEL_SLIDES = 2    # 2 posts/run × 5 runs = 10 posts/day

LOGO_PATH     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "atlantis_wildlife.png")
HISTORY_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "posted_history.json")
YT_HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "youtube_history.json")

# YouTube config
YOUTUBE_CLIENT_ID     = os.getenv("YOUTUBE_CLIENT_ID", "")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET", "")
YOUTUBE_REFRESH_TOKEN = os.getenv("YOUTUBE_REFRESH_TOKEN", "")
YOUTUBE_ONLY          = os.getenv("YOUTUBE_ONLY", "false").lower() == "true"

if YOUTUBE_ONLY:
    HISTORY_FILE    = YT_HISTORY_FILE
    CAROUSEL_SLIDES = 1

WILDLIFE_TOPICS = [
    "rare wildlife animal discovery 2025",
    "endangered species conservation news",
    "tiger lion leopard wildlife India",
    "marine life ocean deep sea creature",
    "bird migration butterfly insect nature",
]

WILDLIFE_VIDEO_KEYWORDS = {
    "iucn":           "endangered animal wildlife",
    "wwf":            "wildlife conservation nature",
    "national geo":   "wild animal predator prey",
    "bbc wildlife":   "wildlife documentary nature",
    "inaturalist":    "wildlife observation animals nature",
    "bird":           "bird flight wildlife nature",
    "marine":         "ocean fish shark whale dolphins",
    "insect":         "butterfly insect macro nature",
    "tiger":          "tiger wildlife India forest",
    "elephant":       "elephant wildlife Africa India",
    "pexels":         "wildlife animal nature",
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
    print(f"\n[Fetch] Wildlife news: '{topic}'")
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


# --- Wildlife Sources ---------------------------------------------------------

def _parse_rss(url: str, source_name: str, max_results: int = 3) -> list[dict]:
    """Generic RSS/Atom feed parser"""
    import xml.etree.ElementTree as ET
    import re as _re
    try:
        resp = requests.get(url, timeout=12, headers={"User-Agent": "AtlantisWildlifeBot/1.0"})
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
            raw = (d_el.text or "") if d_el is not None else ""
            desc = _re.sub(r'<[^>]+>', '', raw).strip()[:500]
            img = ""
            mc = item.find("media:content", ns)
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
                         "source": source_name, "date": date})
            if len([n for n in news if n["image"]]) >= max_results:
                break
        result = [n for n in news if n["image"]][:max_results] or news[:max_results]
        print(f"      {source_name} RSS: {len(result)} items")
        return result
    except Exception as e:
        print(f"      {source_name} RSS error: {e}")
    return []


def fetch_iucn_news() -> list[dict]:
    """IUCN Red List — endangered & threatened species news"""
    return _parse_rss("https://www.iucn.org/feed", "IUCN", max_results=3)


def fetch_wwf_news() -> list[dict]:
    """WWF — World Wildlife Fund conservation news"""
    return _parse_rss("https://www.worldwildlife.org/stories.rss", "WWF", max_results=3)


def fetch_natgeo_animals() -> list[dict]:
    """National Geographic Animals RSS"""
    return _parse_rss("https://www.nationalgeographic.com/animals/topic/wildlife/rss", "National Geographic", max_results=3)


def fetch_bbc_wildlife() -> list[dict]:
    """BBC Wildlife & Nature news RSS"""
    return _parse_rss("https://feeds.bbci.co.uk/news/science_and_environment/rss.xml", "BBC Wildlife", max_results=3)


def fetch_smithsonian_nature() -> list[dict]:
    """Smithsonian Magazine — nature & animal discoveries"""
    return _parse_rss("https://www.smithsonianmag.com/rss/articles/", "Smithsonian", max_results=2)


def fetch_audubon_birds() -> list[dict]:
    """Audubon Society — birds & birding news"""
    return _parse_rss("https://www.audubon.org/rss.xml", "Audubon Birds", max_results=2)


def fetch_mongabay_news() -> list[dict]:
    """Mongabay — tropical wildlife & rainforest conservation news"""
    return _parse_rss("https://news.mongabay.com/feed/", "Mongabay", max_results=3)


def fetch_earthsky_nature() -> list[dict]:
    """EarthSky — nature & animal news"""
    return _parse_rss("https://earthsky.org/feed/", "EarthSky Nature", max_results=2)


def fetch_inaturalist_obs() -> dict | None:
    """iNaturalist API — recent interesting wildlife observation"""
    try:
        resp = requests.get(
            "https://api.inaturalist.org/v1/observations",
            params={
                "quality_grade": "research",
                "photos":        "true",
                "order":         "desc",
                "order_by":      "votes",
                "per_page":      10,
                "iconic_taxa":   "Mammalia,Aves,Reptilia,Amphibia,Actinopterygii"
            },
            timeout=12,
            headers={"User-Agent": "AtlantisWildlifeBot/1.0"}
        )
        results = resp.json().get("results", [])
        for obs in results:
            taxon   = obs.get("taxon", {}) or {}
            name    = taxon.get("preferred_common_name", "") or taxon.get("name", "")
            sci     = taxon.get("name", "")
            place   = obs.get("place_guess", "Unknown location")
            photos  = obs.get("photos", [])
            img     = photos[0].get("url", "").replace("square", "large") if photos else ""
            desc    = taxon.get("wikipedia_summary", "")[:400]
            if name and img:
                print(f"      iNaturalist: {name} @ {place}")
                return {
                    "title": f"{name} Spotted — {place}",
                    "body":  desc or f"{name} ({sci}) ki ek amazing wildlife observation. Location: {place}.",
                    "image": img,
                    "source": "iNaturalist",
                    "date": datetime.now().strftime("%Y-%m-%d")
                }
    except Exception as e:
        print(f"      iNaturalist error: {e}")
    return None


def fetch_gbif_species() -> dict | None:
    """GBIF — Global Biodiversity species observation"""
    try:
        resp = requests.get(
            "https://api.gbif.org/v1/occurrence/search",
            params={
                "mediaType":  "StillImage",
                "basisOfRecord": "HUMAN_OBSERVATION",
                "hasCoordinate": "true",
                "limit": 10,
            },
            timeout=12,
            headers={"User-Agent": "AtlantisWildlifeBot/1.0"}
        )
        results = resp.json().get("results", [])
        import random
        random.shuffle(results)
        for obs in results:
            name    = obs.get("vernacularName", "") or obs.get("species", "")
            sci     = obs.get("species", "")
            country = obs.get("country", "")
            media   = obs.get("media", [])
            img     = media[0].get("identifier", "") if media else ""
            if name and img and img.startswith("http"):
                print(f"      GBIF: {name}, {country}")
                return {
                    "title": f"{name} — Wildlife Observation",
                    "body":  f"{name} ({sci}) {country} mein observe kiya gaya. GBIF global biodiversity database pe record hai.",
                    "image": img,
                    "source": "GBIF Wildlife",
                    "date":  datetime.now().strftime("%Y-%m-%d")
                }
    except Exception as e:
        print(f"      GBIF error: {e}")
    return None


def fetch_wikimedia_wildlife_image(keyword: str) -> str | None:
    """Wikimedia Commons se free wildlife image"""
    try:
        resp = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query", "list": "search", "format": "json",
                "srsearch": f"{keyword} wildlife animal high quality",
                "srnamespace": "6", "srlimit": 5
            }, timeout=10
        )
        results = resp.json().get("query", {}).get("search", [])
        img_titles = [r["title"] for r in results
                      if any(r["title"].lower().endswith(e) for e in [".jpg", ".jpeg", ".png"])]
        if not img_titles:
            return None
        info = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={"action": "query", "titles": img_titles[0],
                    "prop": "imageinfo", "iiprop": "url", "format": "json"},
            timeout=10
        )
        pages = info.json().get("query", {}).get("pages", {})
        for page in pages.values():
            url = page.get("imageinfo", [{}])[0].get("url", "")
            if url:
                return url
    except Exception as e:
        print(f"      Wikimedia error: {e}")
    return None


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
            ["git", "commit", "-m", "chore: update wildlife posted history [skip ci]"],
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
    print(f"\n[AI] {len(all_news)} wildlife items analyze kar raha hoon...")
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
Ye wildlife/nature content hai. Visual aur wow-factor score do (1-10):
- 9-10: Stunning animal photo/video (rare species, predator-prey, close-up)
- 7-8: Interesting discovery (new species, conservation win, migration)
- 5-6: Informative (research findings, wildlife news)
- 1-4: Boring, no visual

Priority: iNaturalist > GBIF > National Geographic > BBC Wildlife > WWF > Mongabay

{news_list_str}

TOP {count} choose karo. JSON:
{{
  "plan": [
    {{"index": 0, "wow_score": 9, "reason": "why this is visually stunning"}}
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
Tu {CHANNEL_HANDLE} ka Instagram content creator hai — ye ek WILDLIFE EXPLORATION channel hai.

Content:
Title: {news_item.get('title', '')}
Description: {news_item.get('body', '')[:500]}
Source: {news_item.get('source', '')}

TONE — WILDLIFE EXPLORER, NOT NEWS REPORTER:
- Wonder, curiosity, amazement — jaisa koi wildlife photographer baat kar raha ho
- "Dekho ye sher ki aankhein!", "Socho zaraa — ye jungle mein raat ko kya hota hai!", "Ye moment camera ne capture kiya!"
- Readers ko nature se CONNECT karwao — unhe feel ho ki ye unki bhi duniya hai
- Hindi+English mix (Hinglish), young Indian audience ke liye
- 6-8 lines, educational but exciting — David Attenborough wali curiosity
- End mein ek mind-blowing animal fact ya question
- CAPTION MEIN HASHTAG NAHI — sirf "hashtags" field mein

JSON:
{{
  "caption": "wildlife explorer style caption, no hashtags",
  "hashtags": "#Wildlife #Nature #Animals #WildIndia #NaturePhotography #WildlifePhotography #IndianWildlife #NatureLovers #WildAnimal #Jungle #Conservation #BBCWildlife #NatGeo #WildlifeConservation #AnimalLovers #Predator #WildBeauty #NatureIsAmazing #AtlantisWildlife #SaveWildlife (20 tags)",
  "image_keyword": "2-3 word English description for video search",
  "emoji_title": "emoji + short title",
  "headline": "5-8 word Hinglish headline — SIRF confirmed facts, spelling 100% correct",
  "image_summary": "2-3 Hinglish sentences (max 35 words) — confirmed facts only"
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
            "caption": news_item.get('title', 'Amazing Wildlife!'),
            "hashtags": "#Wildlife #Nature #Animals #WildIndia",
            "image_keyword": "wildlife animal nature",
            "emoji_title": "🦁 Wildlife",
            "headline": news_item.get('title', 'Wildlife')[:50],
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
        resp = requests.get(image_url, timeout=15, headers={"User-Agent": "AtlantisWildlifeBot/1.0"})
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
        path = os.path.join(tempfile.gettempdir(), f"wildlife_{int(time.time())}.jpg")
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

def fetch_pexels_video(keyword: str) -> str | None:
    """Pexels — free wildlife stock video"""
    if not PEXELS_API_KEY:
        return None
    try:
        headers = {"Authorization": PEXELS_API_KEY}
        resp = requests.get(
            f"https://api.pexels.com/videos/search?query={keyword}&per_page=8&orientation=portrait",
            headers=headers, timeout=10
        )
        videos = resp.json().get("videos", [])
        for video in videos:
            for vf in video.get("video_files", []):
                if vf.get("file_type") == "video/mp4" and vf.get("height", 0) >= 720:
                    url = vf["link"]
                    print(f"      Pexels video: {url[:60]}")
                    r = requests.get(url, timeout=90, stream=True)
                    path = os.path.join(tempfile.gettempdir(), f"wildlife_vid_{int(time.time())}.mp4")
                    with open(path, "wb") as f:
                        for chunk in r.iter_content(8192):
                            f.write(chunk)
                    size_mb = os.path.getsize(path) // 1024 // 1024
                    print(f"      Downloaded: {size_mb}MB")
                    return path
    except Exception as e:
        print(f"      Pexels video error: {e}")
    return None


def fetch_wildlife_video(keyword: str, source: str = "") -> str | None:
    """Wildlife video — Pexels primary source"""
    source_lower = source.lower()
    video_keyword = keyword
    for key, val in WILDLIFE_VIDEO_KEYWORDS.items():
        if key in source_lower:
            video_keyword = val
            break
    print(f"      Searching wildlife footage: '{video_keyword}'")
    path = fetch_pexels_video(video_keyword)
    if path:
        return path
    if video_keyword != keyword:
        path = fetch_pexels_video(keyword)
        if path:
            return path
    # Generic wildlife fallback
    return fetch_pexels_video("wildlife animal nature")


REALTIME_SOURCES = {"iNaturalist", "GBIF Wildlife"}


def generate_narration(news_item: dict, headline: str, summary: str) -> str:
    """Groq se 30-second wildlife Reel narration"""
    source = news_item.get("source", "")
    title  = news_item.get("title", "")
    body   = news_item.get("body", "")[:500]
    is_rt  = any(s in source for s in REALTIME_SOURCES)

    if is_rt:
        style = (
            "YE REAL WILDLIFE OBSERVATION HAI — abhi spotted kiya gaya.\n"
            "Tone: 'Abhi is waqt...', 'Aaj...', 'Is exact moment mein...'\n"
            "Excitement + urgency — viewers ko feel ho ki ye ABHI ho raha hai.\n"
            "Animal ka naam, location, behavior detail mein batao."
        )
    else:
        style = (
            "YE WILDLIFE EDUCATIONAL content hai.\n"
            "Tone: 'Dekho zaraa...', 'Socho agar tum jungle mein hote...', 'Ye jo tum dekh rahe ho...'\n"
            "Wonder aur curiosity — viewer ko nature se deeply connect karo.\n"
            "Hidden animal facts, survival instincts, food chain batao jo mind blow kar de."
        )

    try:
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=400,
            messages=[{"role": "user", "content": f"""
Tu @atlantis_wildlife Instagram Reel ka narrator hai.
Ek 30-second Hindi narration script likho jo poori Reel mein chale.

Topic: {title}
Details: {body}
Summary: {summary}

{style}

STRICT RULES:
- HEADLINE BILKUL MAT PADHO — woh screen pe already dikh raha hai
- ~90-100 words likhoo — 30 second ke liye enough hoga
- Actual animal facts, behavior, habitat context do
- Hinglish (Hindi + English mix), conversational
- FORBIDDEN: "yaar", "sun", "yaar sun", "bhai", "dosto"
- Vary opener: "Socho zaraa...", "Ye dekhna zaroori hai...", "Is jungle mein...", "Nature ki ye sachai..."
- "..." use karo dramatic pause ke liye
- Chhoti punchy sentences = strong impact
- Sirf bolne wala text — koi heading, bullet, asterisk nahi

Script:"""}]
        )
        narration = resp.choices[0].message.content.strip()
        import re
        narration = re.sub(r'\*+', '', narration).strip()
        wc = len(narration.split())
        print(f"      Narration ({wc} words, {'realtime' if is_rt else 'educational'})")
        return narration
    except Exception as e:
        print(f"      Narration error: {e}")
        return summary


def generate_tts(text: str, out_path: str) -> bool:
    """Edge TTS — SwaraNeural Hindi voice"""
    import re as _re
    import subprocess as _sp

    clean = _re.sub(r'\.{2,}', '... ', text)
    clean = _re.sub(r'\s+', ' ', clean).strip()

    try:
        import asyncio
        import edge_tts

        async def _speak():
            comm = edge_tts.Communicate(clean, voice="hi-IN-SwaraNeural",
                                        rate="+5%", pitch="+0Hz", volume="+15%")
            await comm.save(out_path)

        asyncio.run(_speak())
        if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
            norm_path = out_path.replace(".mp3", "_norm.mp3")
            norm = _sp.run([
                "ffmpeg", "-y", "-i", out_path,
                "-af", "loudnorm=I=-14:TP=-1.5:LRA=7,highpass=f=80",
                norm_path
            ], capture_output=True, timeout=30)
            if norm.returncode == 0 and os.path.exists(norm_path):
                os.replace(norm_path, out_path)
            print(f"      Edge TTS (SwaraNeural) ready")
            return True
    except Exception as e:
        print(f"      Edge TTS error: {e}")

    try:
        from gtts import gTTS
        gTTS(text=clean, lang="hi", slow=False).save(out_path)
        print(f"      gTTS fallback ready")
        return os.path.exists(out_path) and os.path.getsize(out_path) > 0
    except Exception as e2:
        print(f"      gTTS error: {e2}")
        return False


def process_reel(video_path: str, headline: str, summary: str, narration: str = "", source: str = "") -> str | None:
    """Wildlife video ko Reel format mein convert karo"""
    import subprocess
    try:
        ts          = int(time.time())
        tmp         = tempfile.gettempdir()
        base_path   = os.path.join(tmp, f"wbase_{ts}.mp4")
        overlay_png = os.path.join(tmp, f"wovl_{ts}.png")
        audio_path  = os.path.join(tmp, f"wtts_{ts}.mp3")
        out_path    = os.path.join(tmp, f"wreel_{ts}.mp4")

        # Step 1: Full-screen 1080x1920 fill
        crop = subprocess.run([
            "ffmpeg", "-y", "-i", video_path,
            "-t", "30",
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
            "-r", "30",
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
                "[bg_blur][fg_pad]overlay=(W-w)/2:(H-h)/2"
            )
            crop = subprocess.run([
                "ffmpeg", "-y", "-i", video_path,
                "-t", "30", "-vf", vf_blur, "-r", "30",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-an", "-preset", "fast", "-crf", "22",
                base_path
            ], capture_output=True, timeout=180)

        if crop.returncode != 0 or not os.path.exists(base_path):
            print(f"      Crop fail: {crop.stderr[-200:].decode(errors='ignore')}")
            return None

        # Step 2: Full-frame overlay PNG (1080x1920)
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

        # Bottom text bar
        bar_y = FRAME_H - BAR_H
        for i in range(BAR_H):
            alpha = int(170 * (i / BAR_H) + 60)
            ov_draw.line([(0, bar_y + i), (FRAME_W, bar_y + i)],
                         fill=(0, 20, 0, min(alpha, 245)))   # dark green tint for wildlife
        ov_draw.rectangle([0, bar_y, FRAME_W, bar_y + 6], fill=(50, 200, 80, 255))  # green accent

        y = bar_y + 24
        for line in wrap_px(headline, font_head, MAX_W, ov_draw)[:2]:
            ov_draw.text((PAD_LEFT, y), line, font=font_head, fill=(255, 255, 255, 255))
            y += 66

        y += 10
        for line in wrap_px(summary, font_body, MAX_W, ov_draw)[:3]:
            ov_draw.text((PAD_LEFT, y), line, font=font_body, fill=(200, 240, 200, 240))
            y += 44

        date_str = datetime.now().strftime("%d %b %Y")
        ov_draw.text((PAD_LEFT, FRAME_H - 44),
                     f"{CHANNEL_HANDLE}  •  {date_str}",
                     font=font_foot, fill=(130, 210, 140, 210))
        if source:
            font_src = get_font(22)
            src_text = f"© {source}"
            src_w    = ov_draw.textlength(src_text, font=font_src)
            ov_draw.text((FRAME_W - src_w - PAD_RIGHT - 10, FRAME_H - 40),
                         src_text, font=font_src, fill=(160, 220, 160, 180))

        overlay.save(overlay_png, "PNG")

        # Step 3: TTS
        tts_text = narration if narration else summary
        has_audio = generate_tts(tts_text, audio_path)

        # Step 4: FFmpeg combine
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
                "[0:v][1:v]overlay=0:0[vout];[2:a]volume=1.5,atrim=0:30[aout]",
                "-map", "[vout]", "-map", "[aout]",
                "-c:a", "aac", "-b:a", "128k",
                "-shortest", *common, out_path
            ], capture_output=True, timeout=180)
        else:
            result = subprocess.run([
                "ffmpeg", "-y",
                "-i", base_path, "-i", overlay_png,
                "-filter_complex", "[0:v][1:v]overlay=0:0[out]",
                "-map", "[out]", *common, out_path
            ], capture_output=True, timeout=180)

        for p in [base_path, overlay_png, audio_path]:
            try:
                os.remove(p)
            except:
                pass

        if result.returncode == 0 and os.path.exists(out_path):
            size_mb = os.path.getsize(out_path) // 1024 // 1024
            print(f"      Reel ready: {size_mb}MB {'(with audio)' if has_audio else ''}")
            return out_path
        print(f"      FFmpeg error: {result.stderr[-150:].decode(errors='ignore')}")
    except Exception as e:
        print(f"      Reel process error: {e}")
    return None


def upload_video_github(video_path: str) -> str | None:
    """Reel video GitHub Release pe upload karo"""
    gh_token = (os.getenv("GH_PAT") or os.getenv("GITHUB_TOKEN") or "").strip()
    repo = os.getenv("GITHUB_REPOSITORY")
    if not gh_token or not repo:
        return None
    headers = {
        "Authorization": f"token {gh_token}",
        "Accept": "application/vnd.github.v3+json"
    }
    filename = f"wildlife_reel_{int(time.time())}.mp4"
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
                      "draft": False, "body": "Auto-generated wildlife reels"},
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


def post_reel(video_url: str, caption: str) -> str | None:
    """Instagram Reels API"""
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
        for _ in range(12):
            time.sleep(8)
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
        if media_id:
            print(f"      Reel posted! ID: {media_id}")
            return media_id
        print(f"      Reel publish error: {pub.json()}")
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
    """YouTube Shorts upload"""
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
                    f"🦁 Atlantis Wildlife — Wildlife & Nature in Hindi\n"
                    f"Subscribe for daily wildlife Shorts!\n\n"
                    f"📅 {date_str}\n"
                    f"© Sources: WWF, National Geographic, BBC Wildlife, iNaturalist\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"#Wildlife #Nature #Animals #Shorts #AtlantisWildlife "
                    f"#WildlifeShorts #NatureShorts #AnimalLovers #WildIndia"
                ),
                "tags": [
                    "Wildlife", "Nature", "Animals", "Shorts", "AtlantisWildlife",
                    "WildlifeShorts", "NatureShorts", "AnimalLovers", "WildIndia",
                    "Conservation", "NatGeo", "BBCWildlife", "HindiWildlife"
                ],
                "categoryId":           "15",   # Pets & Animals
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
        except Exception as e:
            if attempt < 2:
                time.sleep(6)


# --- Main Agent ---------------------------------------------------------------
def run_agent():
    print("=" * 55)
    print(f"  🦁 Atlantis Wildlife Agent Starting...")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 55)

    all_news = []

    # Source 1: iNaturalist — real wildlife observations
    inat = fetch_inaturalist_obs()
    if inat:
        all_news.insert(0, inat)

    # Source 2: GBIF — global biodiversity observations
    gbif = fetch_gbif_species()
    if gbif:
        all_news.append(gbif)

    # Source 3: National Geographic Animals
    natgeo = fetch_natgeo_animals()
    all_news.extend(natgeo)

    # Source 4: BBC Wildlife / Science & Environment
    bbc = fetch_bbc_wildlife()
    all_news.extend(bbc)

    # Source 5: WWF conservation news
    wwf = fetch_wwf_news()
    all_news.extend(wwf)

    # Source 6: Mongabay — tropical wildlife
    mongabay = fetch_mongabay_news()
    all_news.extend(mongabay)

    # Source 7: IUCN Red List news
    iucn = fetch_iucn_news()
    all_news.extend(iucn)

    # Source 8: Smithsonian nature articles
    smithsonian = fetch_smithsonian_nature()
    all_news.extend(smithsonian)

    # Source 9: Audubon birds
    audubon = fetch_audubon_birds()
    all_news.extend(audubon)

    # Source 10: EarthSky nature
    earthsky = fetch_earthsky_nature()
    all_news.extend(earthsky)

    # Source 11: DuckDuckGo fallback
    if len(all_news) < 4:
        for topic in WILDLIFE_TOPICS[:2]:
            results = fetch_news(topic, max_results=3)
            all_news.extend(results)

    all_news = [n for n in all_news if n.get("image")]
    print(f"      Image wali news: {len(all_news)}")

    if not all_news:
        print("Koi wildlife news nahi mili.")
        return

    all_news_raw = all_news.copy()
    recent_titles = get_recently_posted_titles()
    recent_images = load_posted_images()
    all_news = [
        n for n in all_news
        if not is_duplicate(n.get("title", ""), recent_titles)
        and not is_image_duplicate(n.get("image", ""), recent_images)
    ]
    print(f"      Duplicate hataane ke baad: {len(all_news)}")

    if not all_news:
        print("      Sab duplicate — force post...")
        all_news = [n for n in all_news_raw
                    if n.get("source", "") in {"iNaturalist", "GBIF Wildlife", "National Geographic"}]
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
        hashtags = content.get("hashtags", "#Wildlife #Nature #Animals")
        caption  = content.get("caption", "")

        media_id  = None
        narration = generate_narration(news, headline, summary)
        keyword   = content.get("image_keyword", "wildlife animal nature")
        video_path = fetch_wildlife_video(keyword, source=news.get("source", ""))

        if video_path:
            reel_path = process_reel(video_path, headline, summary, narration,
                                     source=news.get("source", ""))
            try:
                os.remove(video_path)
            except:
                pass

            if reel_path:
                # YouTube Shorts
                yt_id = None
                if YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET and YOUTUBE_REFRESH_TOKEN:
                    yt_id = upload_youtube_short(reel_path, headline, caption)

                # Instagram Reel
                if not YOUTUBE_ONLY:
                    video_url = upload_video_github(reel_path)
                try:
                    os.remove(reel_path)
                except:
                    pass

                if not YOUTUBE_ONLY and video_url:
                    media_id = post_reel(video_url, caption)
                elif yt_id:
                    media_id = f"yt_{yt_id}"

        if not media_id:
            if YOUTUBE_ONLY:
                print("      Reel fail — skipping (YouTube-only mode)")
            else:
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
    print(f"  Agent complete! {posted}/{CAROUSEL_SLIDES} posts. (10 sources, 5 runs/day)")
    print("=" * 55)


if __name__ == "__main__":
    run_agent()
