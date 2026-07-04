"""
Atlantis Finance — Instagram Reels Agent
=========================================
Stock market, crypto, mutual funds, economy news
automatically fetch karke Instagram Reels pe post karta hai.

Sources (sabhi free):
  - Economic Times Markets RSS
  - MoneyControl RSS
  - Yahoo Finance RSS
  - LiveMint RSS
  - CoinGecko API (crypto news)
  - Reuters Business RSS
  - CNBC International RSS
  - DuckDuckGo (fallback)

Run:
    python agent.py
"""

import os
import sys
import json
import time
import base64
import tempfile
import colorsys
import requests

from datetime import datetime
from dotenv import load_dotenv
from ddgs import DDGS
from PIL import Image, ImageDraw
from groq import Groq

_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(_env)

# ── Config ─────────────────────────────────────────────────────────────────────
GROQ_API_KEY         = os.getenv("GROQ_API_KEY", "")
PEXELS_API_KEY       = os.getenv("PEXELS_API_KEY", "")
INSTAGRAM_TOKEN      = os.getenv("FINANCE_INSTAGRAM_ACCESS_TOKEN", "")
INSTAGRAM_ACCOUNT_ID = os.getenv("FINANCE_INSTAGRAM_ACCOUNT_ID", "")
IMGBB_API_KEY        = os.getenv("IMGBB_API_KEY", "")
PIXABAY_API_KEY      = os.getenv("PIXABAY_API_KEY", "")

CHANNEL_HANDLE  = "@atlantis_finance"
POST_DELAY      = 20
CAROUSEL_SLIDES = 1

LOGO_PATH     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "atlantis_finance.png")
HISTORY_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "posted_history.json")

FINANCE_TOPICS = [
    "Nifty Sensex India stock market today 2026",
    "Indian economy GDP budget news today",
    "crypto bitcoin ethereum market news today",
    "mutual funds SIP investment India news",
    "RBI interest rate inflation India today",
    "BSE NSE top gainers losers today",
    "IPO India 2026 new listing news",
]

FINANCE_VIDEO_KEYWORDS = {
    "stock":        "stock market trading screen charts investment",
    "nifty":        "stock market india trading bulls bears",
    "sensex":       "mumbai stock exchange trading floor",
    "bitcoin":      "bitcoin cryptocurrency digital currency",
    "crypto":       "cryptocurrency blockchain digital finance",
    "rbi":          "bank india reserve currency money",
    "mutual fund":  "investment portfolio growth finance money",
    "ipo":          "stock exchange listing new company investment",
    "gold":         "gold bars coins precious metals investment",
    "rupee":        "indian rupee currency exchange money",
    "inflation":    "economy finance money growth charts",
    "gdp":          "economic growth india finance charts",
    "budget":       "government finance budget money economy",
    "interest rate":"banking finance interest money",
    "default":      "stock market trading charts finance money",
}


# ── Utilities ──────────────────────────────────────────────────────────────────
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
                from PIL import ImageFont as IF
                return IF.truetype(path, size)
            except Exception:
                continue
    try:
        from PIL import ImageFont as IF
        return IF.load_default(size=size)
    except Exception:
        from PIL import ImageFont as IF
        return IF.load_default()


def clean_title(title: str) -> str:
    import re
    return re.sub(r'\s*[-–|]\s*[A-Z][A-Za-z0-9 &.]{2,40}$', '', title).strip()


def get_video_keyword(title: str, body: str = "") -> str:
    text = (title + " " + body).lower()
    for key, kw in FINANCE_VIDEO_KEYWORDS.items():
        if key in text:
            return kw
    return FINANCE_VIDEO_KEYWORDS["default"]


# ── Duplicate Prevention ───────────────────────────────────────────────────────
def load_history() -> dict:
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"titles": [], "images": []}


def save_history(data: dict):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"      History save error: {e}")


def get_recently_posted_titles() -> list[str]:
    return load_history().get("titles", [])[-200:]


def load_posted_images() -> list[str]:
    return load_history().get("images", [])[-200:]


def save_posted_title(title: str, image_url: str = ""):
    h = load_history()
    h.setdefault("titles", [])
    h.setdefault("images", [])
    if title and title not in h["titles"]:
        h["titles"].append(title)
        h["titles"] = h["titles"][-300:]
    if image_url and image_url not in h["images"]:
        h["images"].append(image_url)
        h["images"] = h["images"][-300:]
    save_history(h)


def is_duplicate(title: str, recent_titles: list[str]) -> bool:
    import difflib
    t = title.lower().strip()
    for r in recent_titles:
        if difflib.SequenceMatcher(None, t, r.lower().strip()).ratio() > 0.82:
            return True
    return False


def is_image_duplicate(url: str, recent_images: list[str]) -> bool:
    if not url:
        return False
    return url in recent_images


# ── News Fetch ─────────────────────────────────────────────────────────────────
def fetch_news(topic: str, max_results: int = 5) -> list[dict]:
    print(f"\n[Fetch] Finance news: '{topic}'")
    strategies = [{"timelimit": "d"}, {"timelimit": "w"}, {}]
    for attempt, params in enumerate(strategies):
        try:
            time.sleep(attempt * 4)
            with DDGS() as ddgs:
                results = list(ddgs.news(topic, max_results=max_results * 3, **params))
            if not results:
                raise Exception("No results")
            news = [dict(n, title=clean_title(n.get("title", ""))) for n in results]
            news = news[:max_results]
            if news:
                print(f"      {len(news)} news mili")
                return news
        except Exception as e:
            print(f"      Attempt {attempt+1} failed: {e}")
    return []


# ── Finance RSS Sources ────────────────────────────────────────────────────────
def _parse_rss(url: str, source_name: str, max_results: int = 4) -> list[dict]:
    import xml.etree.ElementTree as ET
    import re as _re
    try:
        resp = requests.get(url, timeout=12,
                            headers={"User-Agent": "AtlantisFinanceBot/1.0"})
        if resp.status_code != 200:
            print(f"      {source_name} RSS: HTTP {resp.status_code}")
            return []
        root = ET.fromstring(resp.content)
        ns   = {"media": "http://search.yahoo.com/mrss/"}
        items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        news  = []
        for item in items:
            t_el  = item.find("title") or item.find("{http://www.w3.org/2005/Atom}title")
            title = (t_el.text or "").strip() if t_el is not None else ""
            if not title:
                continue
            title = clean_title(title)
            d_el  = (item.find("description") or
                     item.find("{http://www.w3.org/2005/Atom}summary") or
                     item.find("{http://www.w3.org/2005/Atom}content"))
            raw   = (d_el.text or "") if d_el is not None else ""
            desc  = _re.sub(r'<[^>]+>', '', raw).strip()[:500]
            img   = ""
            mc    = item.find("media:content", ns)
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
            url_el = item.find("link") or item.find("{http://www.w3.org/2005/Atom}link")
            link   = (url_el.text or url_el.get("href", "") if url_el is not None else "")
            date_el = item.find("pubDate") or item.find("{http://www.w3.org/2005/Atom}published")
            date    = (date_el.text or "")[:10] if date_el is not None else ""
            news.append({"title": title, "body": desc, "image": img,
                         "source": source_name, "date": date, "url": link})
            if len([n for n in news if n["image"]]) >= max_results:
                break
        result = [n for n in news if n["image"]][:max_results] or news[:max_results]
        print(f"      {source_name} RSS: {len(result)} items")
        return result
    except Exception as e:
        print(f"      {source_name} RSS error: {e}")
    return []


def fetch_economic_times_rss() -> list[dict]:
    return _parse_rss(
        "https://economictimes.indiatimes.com/markets/rss.cms",
        "Economic Times", 5
    )


def fetch_moneycontrol_rss() -> list[dict]:
    return _parse_rss(
        "https://www.moneycontrol.com/rss/latestnews.xml",
        "MoneyControl", 4
    )


def fetch_livemint_rss() -> list[dict]:
    return _parse_rss(
        "https://www.livemint.com/rss/markets",
        "LiveMint Markets", 4
    )


def fetch_yahoo_finance_rss() -> list[dict]:
    return _parse_rss(
        "https://finance.yahoo.com/rss/topstories",
        "Yahoo Finance", 4
    )


def fetch_reuters_business_rss() -> list[dict]:
    return _parse_rss(
        "https://feeds.reuters.com/reuters/businessNews",
        "Reuters Business", 4
    )


def fetch_cnbc_rss() -> list[dict]:
    return _parse_rss(
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "CNBC Business", 3
    )


def fetch_coingecko_news() -> list[dict]:
    """CoinGecko free API — crypto market news"""
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/news",
            headers={"accept": "application/json",
                     "x-cg-demo-api-key": os.getenv("COINGECKO_API_KEY", "")},
            timeout=12
        )
        if resp.status_code != 200:
            print(f"      CoinGecko: HTTP {resp.status_code}")
            return []
        items = resp.json().get("data", [])[:4]
        news  = []
        for item in items:
            title = clean_title(item.get("title", "") or item.get("news_title", ""))
            desc  = item.get("description", "") or item.get("text", "")
            img   = item.get("thumb_2x", "") or item.get("author", {}).get("avatar_url", "")
            if title:
                news.append({
                    "title":  title,
                    "body":   desc[:500],
                    "image":  img,
                    "source": "CoinGecko",
                    "date":   "",
                    "url":    item.get("url", ""),
                })
        print(f"      CoinGecko: {len(news)} items")
        return news
    except Exception as e:
        print(f"      CoinGecko error: {e}")
    return []


# ── AI Caption & Planning ──────────────────────────────────────────────────────
def generate_caption(news: dict) -> dict:
    title  = news.get("title", "")
    body   = news.get("body", "")[:400]
    source = news.get("source", "")
    print(f"\n[AI] Caption: '{title[:60]}'")
    try:
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=500,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": f"""
Tu ek Hindi finance content creator hai — Zerodha, Groww, Upstox ki tarah simple aur educational tone.

News: {title}
Details: {body}
Source: {source}

JSON format mein do:
{{
  "headline": "Short punchy Hindi headline, max 8 words — numbers/percentages include karo agar hain",
  "image_summary": "2-line simple Hindi summary — jaise ghar ka koi samjha raha ho",
  "caption": "Instagram caption — emoji ke saath, casual Hindi, pehle interesting fact/number, phir context, call to action. 150-200 words.",
  "hashtags": "#Finance #StockMarket #Nifty #Sensex #Investment #Crypto #MoneyMindset #AtlantisFinance",
  "video_search_query": "English keyword for stock/finance video search (e.g. 'stock market trading charts')",
  "image_keyword": "finance keyword for video"
}}
"""}]
        )
        content = json.loads(resp.choices[0].message.content)
        print(f"      Caption ready: '{content.get('headline','')[:50]}'")
        return content
    except Exception as e:
        print(f"      Caption error: {e}")
    return {
        "headline":          title[:60],
        "image_summary":     body[:120],
        "caption":           f"📈 {title}\n\n{body[:200]}\n\n#Finance #StockMarket",
        "hashtags":          "#Finance #StockMarket #Investment #AtlantisFinance",
        "video_search_query": "stock market trading",
        "image_keyword":     "finance",
    }


def smart_plan(all_news: list[dict], count: int = CAROUSEL_SLIDES) -> list[dict]:
    print(f"\n[AI] {len(all_news)} finance items analyze kar raha hoon...")
    news_list_str = "\n".join([
        f"{i+1}. [{n.get('source','')}] {n.get('title','')}" for i, n in enumerate(all_news[:25])
    ])
    try:
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=300,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": f"""
Tu ek finance content strategist hai.
In items mein se sabse engaging {count} choose karo — jo common investor ke liye most useful/interesting ho.

Priority: breaking market moves > crypto > IPO > RBI/policy > economy analysis

{news_list_str}

JSON: {{"plan": [{{"item_number": 1, "reason": "...", "engagement_score": 8}}]}}
"""}]
        )
        plan  = json.loads(resp.choices[0].message.content).get("plan", [])
        planned = []
        for item in plan[:count]:
            idx = item.get("item_number", 1) - 1
            if 0 <= idx < len(all_news):
                planned.append(all_news[idx])
        return planned[:count] if planned else all_news[:count]
    except Exception as e:
        print(f"      Planning error: {e}")
    return all_news[:count]


# ── Narration ──────────────────────────────────────────────────────────────────
def generate_narration(news: dict, headline: str, summary: str) -> str:
    title = news.get("title", "")
    body  = news.get("body", "")[:500]
    import random
    styles = [
        "MARKET ALERT: Seedha number/fact se shuru karo — 'Aaj Nifty ne...', 'Bitcoin ne...'",
        "INVESTOR ADVICE: Simple language mein samjhao — ye khabar tumhare paisa pe kya asar dalegi",
        "TREND ANALYSIS: Ye kyu hua, age kya ho sakta hai — analyst ki tarah 30 seconds mein breakdown",
    ]
    try:
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=420,
            messages=[{"role": "user", "content": f"""
Tu ek Hindi finance expert hai — Zerodha Varsity ka teacher + CNBC Awaaz ka anchor combined.
Ek 30-second Reel narration likho — educational, confident, numbers pe focused.

News: {title}
Details: {body}
Summary: {summary}

STYLE: {random.choice(styles)}

RULES:
- Numbers aur percentages zaroori — "2.3%" better than "thoda badha"
- Common investor ki language — technical jargon avoid karo
- ~90-100 words — 30 seconds ke liye
- HEADLINE MAT PADHO — screen pe dikh raha hai
- Hindi dominant, English sirf proper nouns (Nifty, RBI, Bitcoin)
- End mein ek actionable tip ya key takeaway
- FORBIDDEN: "yaar", "sun", "bhai", "dosto"
- Sirf bolne wala text — koi bullet, asterisk, heading nahi

Narration:"""}]
        )
        narration = resp.choices[0].message.content.strip()
        import re
        narration = re.sub(r'\*+', '', narration).strip()
        print(f"      Narration ready ({len(narration.split())} words)")
        return narration
    except Exception as e:
        print(f"      Narration error: {e}")
    return summary


# ── TTS ────────────────────────────────────────────────────────────────────────
def _normalize_audio(path: str) -> None:
    import subprocess as _sp
    norm = path.replace(".mp3", "_norm.mp3")
    filters = (
        "highpass=f=85,"
        "lowpass=f=13000,"
        "acompressor=threshold=-18dB:ratio=4:attack=5:release=50:makeup=2dB,"
        "equalizer=f=3500:t=q:w=1.5:g=3,"
        "loudnorm=I=-14:TP=-1.5:LRA=7"
    )
    r = _sp.run(["ffmpeg", "-y", "-i", path, "-af", filters, norm],
                capture_output=True, timeout=30)
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
            comm = edge_tts.Communicate(clean, voice=voice, rate=rate, pitch=pitch, volume=vol)
            asyncio.run(comm.save(out_path))
            if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
                _normalize_audio(out_path)
                print(f"      TTS: {voice}")
                return True
        except Exception:
            continue
    return False


def generate_tts(text: str, out_path: str) -> bool:
    import re as _re
    clean = _re.sub(r'[*_`#~\[\]{}|<>\\]', '', text).strip()
    if not clean:
        return False
    if _tts_edge(clean, out_path):
        return True
    try:
        from gtts import gTTS
        gTTS(text=clean, lang="hi", slow=False).save(out_path)
        if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
            print(f"      TTS: gTTS fallback")
            return True
    except Exception as e:
        print(f"      gTTS error: {e}")
    return False


# ── Video Fetch ────────────────────────────────────────────────────────────────
def _download_video(url: str, prefix: str = "fin", min_size: int = 500_000) -> str | None:
    try:
        path = os.path.join(tempfile.gettempdir(), f"{prefix}_{int(time.time())}.mp4")
        r = requests.get(url, stream=True, timeout=90,
                         headers={"User-Agent": "AtlantisFinanceBot/1.0"})
        with open(path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        if os.path.getsize(path) >= min_size:
            return path
        os.remove(path)
    except Exception as e:
        print(f"      Download error: {e}")
    return None


def fetch_pexels_video(keyword: str) -> str | None:
    if not PEXELS_API_KEY:
        return None
    try:
        headers = {"Authorization": PEXELS_API_KEY}
        for orientation in ("portrait", "landscape"):
            resp = requests.get(
                "https://api.pexels.com/videos/search",
                params={"query": keyword, "per_page": 10, "orientation": orientation},
                headers=headers, timeout=10
            )
            for video in resp.json().get("videos", []):
                for vf in sorted(video.get("video_files", []),
                                 key=lambda x: x.get("height", 0), reverse=True):
                    if vf.get("file_type") == "video/mp4" and vf.get("height", 0) >= 720:
                        path = _download_video(vf["link"], "pexels")
                        if path:
                            print(f"      Pexels: {keyword[:40]}")
                            return path
    except Exception as e:
        print(f"      Pexels error: {e}")
    return None


def fetch_pixabay_video(keyword: str) -> str | None:
    if not PIXABAY_API_KEY:
        return None
    try:
        import random
        r = requests.get("https://pixabay.com/api/videos/", params={
            "key": PIXABAY_API_KEY, "q": keyword,
            "video_type": "film", "per_page": 10, "safesearch": "true",
        }, timeout=10)
        hits = r.json().get("hits", [])
        random.shuffle(hits)
        for hit in hits[:5]:
            for quality in ("large", "medium", "small"):
                url = hit.get("videos", {}).get(quality, {}).get("url", "")
                if url:
                    path = _download_video(url, "pixabay")
                    if path:
                        return path
    except Exception as e:
        print(f"      Pixabay error: {e}")
    return None


def fetch_finance_video(keyword: str) -> str | None:
    print(f"\n      [Video] '{keyword}'")
    # Pexels first — best finance footage
    if PEXELS_API_KEY:
        words = keyword.split()
        for kw in [keyword, " ".join(words[:3]), words[0]]:
            path = fetch_pexels_video(kw)
            if path:
                return path
    # Pixabay fallback
    if PIXABAY_API_KEY:
        path = fetch_pixabay_video(keyword)
        if path:
            return path
    # Generic fallback
    if PEXELS_API_KEY:
        for fallback in ["stock market charts trading", "money finance investment", "trading screen"]:
            path = fetch_pexels_video(fallback)
            if path:
                return path
    print(f"      No video found for '{keyword}'")
    return None


# ── Reel Processing ────────────────────────────────────────────────────────────
def process_reel(video_path: str, headline: str, summary: str,
                 narration: str = "", source: str = "") -> str | None:
    import subprocess
    try:
        ts          = int(time.time())
        tmp         = tempfile.gettempdir()
        base_path   = os.path.join(tmp, f"fbase_{ts}.mp4")
        overlay_png = os.path.join(tmp, f"fovl_{ts}.png")
        audio_path  = os.path.join(tmp, f"ftts_{ts}.mp3")
        out_path    = os.path.join(tmp, f"freel_{ts}.mp4")

        # Step 1: TTS
        tts_text  = narration if narration else summary
        has_audio = generate_tts(tts_text, audio_path)

        reel_dur = 30.0
        if has_audio and os.path.exists(audio_path):
            try:
                probe = subprocess.run([
                    "ffprobe", "-v", "quiet", "-print_format", "json",
                    "-show_streams", audio_path
                ], capture_output=True, timeout=10)
                streams = json.loads(probe.stdout).get("streams", [{}])
                reel_dur = float(streams[0].get("duration", 30.0))
                reel_dur = min(reel_dur + 0.3, 88.0)
                print(f"      Audio duration: {reel_dur:.1f}s")
            except Exception:
                reel_dur = 30.0

        # Step 1b: Video 1080×1920, looped
        vf_main = (
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920"
        )
        crop = subprocess.run([
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", video_path,
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
                "[bg_blur][fg_pad]overlay=(W-w)/2:(H-h)/2"
            )
            crop = subprocess.run([
                "ffmpeg", "-y", "-stream_loop", "-1", "-i", video_path,
                "-t", str(reel_dur), "-vf", vf_blur, "-r", "30",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-an", "-preset", "fast", "-crf", "22",
                base_path
            ], capture_output=True, timeout=180)

        if crop.returncode != 0 or not os.path.exists(base_path):
            print(f"      Crop fail: {crop.stderr[-200:].decode(errors='ignore')}")
            return None

        # Step 2: Overlay — finance green/gold theme
        FRAME_W   = 1080
        FRAME_H   = 1920
        BAR_H     = 460
        PAD_LEFT  = 40
        PAD_RIGHT = 150
        MAX_W     = FRAME_W - PAD_LEFT - PAD_RIGHT
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
        ov      = ImageDraw.Draw(overlay)

        # Logo top-left
        if os.path.exists(LOGO_PATH):
            try:
                logo_img = Image.open(LOGO_PATH).convert("RGB")
                logo_w   = 160
                logo_h   = int(logo_img.height * (logo_w / logo_img.width))
                logo_img = logo_img.resize((logo_w, logo_h), Image.LANCZOS)
                lx, ly   = 40, 60
                pad      = 10
                ov.rounded_rectangle(
                    [lx-pad, ly-pad, lx+logo_w+pad, ly+logo_h+pad],
                    radius=12, fill=(255, 255, 255, 255)
                )
                overlay.paste(logo_img, (lx, ly))
            except Exception as le:
                print(f"      Logo error: {le}")

        # Bottom bar — dark green tint
        bar_y = FRAME_H - BAR_H
        for i in range(BAR_H):
            alpha = int(170 * (i / BAR_H) + 60)
            ov.line([(0, bar_y + i), (FRAME_W, bar_y + i)],
                    fill=(0, 30, 10, min(alpha, 245)))     # dark green tint
        # Gold accent line
        ov.rectangle([0, bar_y, FRAME_W, bar_y + 6],
                     fill=(255, 200, 0, 255))               # gold accent

        y = bar_y + 24
        for line in wrap_px(headline, font_head, MAX_W, ov)[:2]:
            ov.text((PAD_LEFT, y), line, font=font_head, fill=(255, 255, 255, 255))
            y += 66

        y += 10
        for line in wrap_px(summary, font_body, MAX_W, ov)[:3]:
            ov.text((PAD_LEFT, y), line, font=font_body, fill=(200, 255, 210, 240))  # light green text
            y += 44

        date_str = datetime.now().strftime("%d %b %Y")
        ov.text((PAD_LEFT, FRAME_H - 44),
                f"{CHANNEL_HANDLE}  •  {date_str}",
                font=font_foot, fill=(180, 255, 180, 210))
        if source:
            font_src = get_font(22)
            src_text = f"© {source}"
            src_w    = ov.textlength(src_text, font=font_src)
            ov.text((FRAME_W - src_w - PAD_RIGHT - 10, FRAME_H - 40),
                    src_text, font=font_src, fill=(200, 230, 200, 180))

        overlay.save(overlay_png, "PNG")

        # Step 3: Combine
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
            size_kb = os.path.getsize(out_path) // 1024
            print(f"      Reel ready: {size_kb}KB {'(with audio)' if has_audio else ''}")
            if size_kb < 10:
                print(f"      WARNING: reel too small — skip")
                return None
            return out_path
        print(f"      FFmpeg error: {result.stderr[-200:].decode(errors='ignore')}")
    except Exception as e:
        print(f"      Reel process error: {e}")
    return None


# ── GitHub Upload (Contents API) ───────────────────────────────────────────────
def upload_video_github(video_path: str) -> str | None:
    gh_token = (os.getenv("GH_PAT") or os.getenv("GITHUB_TOKEN") or "").strip()
    repo     = os.getenv("GITHUB_REPOSITORY", "")
    if not gh_token or not repo:
        print("      GitHub token ya repo missing")
        return None
    try:
        with open(video_path, "rb") as f:
            content = base64.b64encode(f.read()).decode()
        filename = f"finance_reel_{int(time.time())}.mp4"
        api_url  = f"https://api.github.com/repos/{repo}/contents/reels/{filename}"
        size_kb  = os.path.getsize(video_path) // 1024
        print(f"      GitHub upload ({size_kb}KB)...")
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


# ── Instagram Post ─────────────────────────────────────────────────────────────
def post_reel_instagram(video_url: str, caption: str) -> str | None:
    print(f"\n[Reel] Instagram pe post kar raha hoon...")
    if not INSTAGRAM_TOKEN or not INSTAGRAM_ACCOUNT_ID:
        print("      Token/Account ID missing — dry run")
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
            print(f"      Container error: {resp.json()}")
            return None
        for i in range(18):
            time.sleep(5 if i < 4 else 8)
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
            timeout=60
        )
        media_id = pub.json().get("id")
        if media_id:
            print(f"      Reel published! media_id={media_id}")
            return media_id
        print(f"      Publish error: {pub.json()}")
    except Exception as e:
        print(f"      Reel error: {e}")
    return None


def auto_first_comment(media_id: str, hashtags: str):
    if media_id == "dry_run" or not INSTAGRAM_TOKEN:
        return
    try:
        requests.post(
            f"https://graph.facebook.com/v25.0/{media_id}/comments",
            data={"message": hashtags, "access_token": INSTAGRAM_TOKEN},
            timeout=10
        )
        print(f"      Hashtags comment added")
    except Exception:
        pass


# ── Main Agent ─────────────────────────────────────────────────────────────────
def run_agent():
    print("=" * 60)
    print("  Atlantis Finance — Market News Reel Agent")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # 1. Fetch from all free sources in parallel
    from concurrent.futures import ThreadPoolExecutor, as_completed
    all_news: list[dict] = []

    official_sources = [
        fetch_economic_times_rss,
        fetch_moneycontrol_rss,
        fetch_livemint_rss,
        fetch_yahoo_finance_rss,
        fetch_reuters_business_rss,
        fetch_cnbc_rss,
        fetch_coingecko_news,
    ]

    print("\n[Sources] RSS feeds fetch kar raha hoon...")
    with ThreadPoolExecutor(max_workers=7) as ex:
        futures = {ex.submit(fn): fn.__name__ for fn in official_sources}
        for future in as_completed(futures):
            try:
                items = future.result()
                all_news.extend(items)
            except Exception as e:
                print(f"      {futures[future]} failed: {e}")

    print(f"\n[Sources] Total: {len(all_news)} items from official sources")

    # 2. DuckDuckGo fallback if < 6 items
    if len(all_news) < 6:
        print("[Fallback] DuckDuckGo...")
        for topic in FINANCE_TOPICS[:3]:
            results = fetch_news(topic, max_results=3)
            all_news.extend(results)
            if len(all_news) >= 8:
                break

    if not all_news:
        print("Koi finance news nahi mili.")
        return

    # 3. Deduplicate
    recent_titles = get_recently_posted_titles()
    recent_images = load_posted_images()
    all_news_raw  = all_news.copy()
    all_news = [
        n for n in all_news
        if not is_duplicate(n.get("title", ""), recent_titles)
        and not is_image_duplicate(n.get("image", ""), recent_images)
    ]
    print(f"      Duplicate hataane ke baad: {len(all_news)}")

    if not all_news:
        print("      Sab duplicate — force post...")
        all_news = all_news_raw[:CAROUSEL_SLIDES]

    # 4. AI planning
    news_list = smart_plan(all_news, count=CAROUSEL_SLIDES)
    posted    = 0

    for news in news_list:
        print(f"\n{'-'*55}")
        print(f"News: {news.get('title','')[:70]}...")

        content  = generate_caption(news)
        headline = content.get("headline") or news.get("title", "")
        summary  = content.get("image_summary", "")
        hashtags = content.get("hashtags", "#Finance #StockMarket #AtlantisFinance")
        caption  = content.get("caption", f"📈 {headline}\n\n{summary}")

        video_kw = content.get("video_search_query",
                   get_video_keyword(news.get("title", ""), news.get("body", "")))
        narration = generate_narration(news, headline, summary)

        video_path = fetch_finance_video(video_kw)

        if video_path:
            reel_path = process_reel(video_path, headline, summary, narration,
                                     source=news.get("source", ""))
            try:
                os.remove(video_path)
            except Exception:
                pass

            if reel_path:
                video_url = upload_video_github(reel_path)
                try:
                    os.remove(reel_path)
                except Exception:
                    pass

                if video_url:
                    media_id = post_reel_instagram(video_url, caption)
                    if media_id:
                        save_posted_title(news.get("title", ""), news.get("image", ""))
                        time.sleep(8)
                        auto_first_comment(media_id, hashtags)
                        print(f"      Post ho gaya!")
                        posted += 1
                        time.sleep(POST_DELAY)
                        continue

        print(f"      Reel fail hua — skipping")

    print(f"\n{'='*60}")
    print(f"  Agent complete! {posted}/{CAROUSEL_SLIDES} posts.")
    print("=" * 60)


if __name__ == "__main__":
    run_agent()
