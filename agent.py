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
import requests

from datetime import datetime
from dotenv import load_dotenv
from ddgs import DDGS
from PIL import Image, ImageDraw
from groq import Groq

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

MAX_NEWS     = 1                               # har run mein 1 post (din mein 4 baar chalega)
POST_DELAY   = 60                              # seconds between posts

# High-impact queries — 5 topics, fast fetch
NEWS_TOPICS = [
    "India breaking news today",
    "India government Parliament Supreme Court today",
    "India economy RBI cricket sports today",
    "India scam arrest CBI ED crime today",
    "India disaster accident viral news today",
]


# --- Step 1: News Fetch -------------------------------------------------------
def fetch_news(topic: str, max_results: int = 5) -> list[dict]:
    """DuckDuckGo se sirf last 24 hours ki news fetch karo"""
    print(f"\n[1/4] News fetch kar raha hoon: '{topic}'")
    cutoff = datetime.now().timestamp() - 86400  # 24 hours ago

    for attempt in range(2):
        try:
            with DDGS() as ddgs:
                results = list(ddgs.news(topic, max_results=max_results * 2, timelimit="d"))

            fresh = []
            for n in results:
                pub = n.get("date", "")
                try:
                    from datetime import datetime as dt
                    pub_ts = dt.fromisoformat(pub.replace("Z", "+00:00")).timestamp()
                    if pub_ts >= cutoff:
                        fresh.append(n)
                except Exception:
                    fresh.append(n)

            fresh = fresh[:max_results]
            print(f"      {len(fresh)} fresh news mili (last 24h)")
            return fresh
        except Exception as e:
            print(f"      Attempt {attempt+1} failed: {e}")
            if attempt < 1:
                time.sleep(2)
    return []


# --- Token Auto-Refresh -------------------------------------------------------
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
def smart_plan(all_news: list[dict]) -> list[dict]:
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

Sirf TOP {MAX_NEWS} news choose karo jinka importance score 7+ ho.
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
            model="llama-3.3-70b-versatile",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        result = json.loads(resp.choices[0].message.content)
        print(f"      Strategy: {result.get('strategy', '')}")
        planned = []
        for item in result.get("plan", []):
            idx = item.get("index", 0)
            if 0 <= idx < len(all_news) and item.get("worth_posting", True):
                news = all_news[idx].copy()
                news["_format"] = item.get("format", "image")
                news["_image_source"] = item.get("image_source", "news")
                news["_reason"] = item.get("reason", "")
                news["_importance"] = item.get("importance", 7)
                planned.append(news)
        return planned[:MAX_NEWS]
    except Exception as e:
        print(f"      Planning error: {e} — default order use kar raha hoon")
        return all_news[:MAX_NEWS]


# --- Content Quality Check ----------------------------------------------------
def is_worth_posting(caption: str, news_title: str) -> bool:
    """Strict quality gate — low-value ya clickbait news reject karo"""
    try:
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
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
Image URL: {image_url}
News Title: {news_item.get('title', '')}
News Body: {news_item.get('body', '')[:500]}
Source: {news_item.get('source', '')}
Published: {news_item.get('date', 'aaj')[:10]}

Caption likhte waqt STRICT RULES:
- YE EK PHOTO POST HAI — "video", "clip", "watch", "dekho video", "reel" jaisi koi bhi word BILKUL MAT LIKHO
- "tasveer", "photo", "image", "ye shot", "is frame mein" — yahi words use karo
- Caption IMAGE ke saath cohesive lagne chahiye — pehli ya doosri line mein photo ko acknowledge karo
  (e.g., "Ye tasveer kaafi kuch kehti hai...", "Is photo mein dekho...", "Ye moment capture hua jab...")
- Hinglish mein likho (Hindi + English mix)
- 6-8 lines total
- Hook line se shuru karo jo scroll rokde
- News ka context 2-3 lines mein explain karo with key facts/numbers
- Emotional aur conversational tone
- End mein strong question ya call-to-action
- 15-20 relevant hashtags (Hindi + English mix)

Sirf JSON format mein respond karo:
{{
  "caption": "...",
  "hashtags": "#tag1 #tag2 ...",
  "image_keyword": "2-3 word English description of what image likely shows",
  "emoji_title": "emoji + short title"
}}
"""

    try:
        message = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=900,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        raw = message.choices[0].message.content.strip()
        result = json.loads(raw)

        # Hard filter — replace any video-related words that slipped through
        import re
        caption = result.get("caption", "")
        caption = re.sub(r'\b(video|reel|clip)\b', 'photo', caption, flags=re.IGNORECASE)
        caption = re.sub(r'dekho video', 'dekho ye tasveer', caption, flags=re.IGNORECASE)
        caption = re.sub(r'ye video', 'ye tasveer', caption, flags=re.IGNORECASE)
        result["caption"] = caption

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


def post_video_to_instagram(video_url: str, caption: str, hashtags: str) -> bool:
    """Instagram pe video (Reel) post karo"""
    print(f"\n[4/4] Instagram pe video post kar raha hoon...")
    if not INSTAGRAM_TOKEN or not INSTAGRAM_ACCOUNT_ID:
        print("      Dry run — credentials nahi hain")
        return False

    full_caption = f"{caption}\n\n{hashtags}"
    try:
        upload_url = f"https://graph.facebook.com/v25.0/{INSTAGRAM_ACCOUNT_ID}/media"
        resp = requests.post(upload_url, data={
            "video_url": video_url,
            "caption": full_caption,
            "media_type": "REELS",
            "access_token": INSTAGRAM_TOKEN
        })
        container_id = resp.json().get("id")
        if not container_id:
            print(f"      Video upload error: {resp.json()}")
            return False

        # Processing wait karo
        for _ in range(10):
            time.sleep(8)
            status = requests.get(
                f"https://graph.facebook.com/v25.0/{container_id}",
                params={"fields": "status_code", "access_token": INSTAGRAM_TOKEN}
            ).json()
            if status.get("status_code") == "FINISHED":
                break
            print(f"      Processing... {status.get('status_code')}")

        pub_resp = requests.post(
            f"https://graph.facebook.com/v25.0/{INSTAGRAM_ACCOUNT_ID}/media_publish",
            data={"creation_id": container_id, "access_token": INSTAGRAM_TOKEN}
        )
        if pub_resp.json().get("id"):
            print(f"      Video post successful! ID: {pub_resp.json()['id']}")
            return True
        else:
            print(f"      Publish error: {pub_resp.json()}")
            return False
    except Exception as e:
        print(f"      Video post error: {e}")
        return False


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

    # Source + date
    date_str = datetime.now().strftime("%d %b %Y")
    draw.text((54, 40), f"{source.upper()}  •  {date_str}",
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


# --- Logo Watermark -----------------------------------------------------------
LOGO_PATH = os.path.join(os.path.dirname(__file__), "atlantis_news_ai.png")

def add_logo_watermark(image_url: str) -> str | None:
    """News image download karo, corner mein logo lagao, ImgBB pe upload karo"""
    try:
        # News image download karo
        resp = requests.get(image_url, timeout=15)
        if resp.status_code != 200:
            return image_url  # fallback: original URL

        news_img = Image.open(__import__("io").BytesIO(resp.content)).convert("RGBA")

        # 1080x1080 square crop (Instagram feed)
        w, h = news_img.size
        side = min(w, h)
        left = (w - side) // 2
        top  = (h - side) // 2
        news_img = news_img.crop((left, top, left + side, top + side))
        news_img = news_img.resize((1080, 1080), Image.LANCZOS)

        # Logo overlay
        if os.path.exists(LOGO_PATH):
            logo = Image.open(LOGO_PATH).convert("RGBA")

            # Logo size: image width ka 18%
            logo_w = int(1080 * 0.18)
            ratio  = logo_w / logo.width
            logo_h = int(logo.height * ratio)
            logo   = logo.resize((logo_w, logo_h), Image.LANCZOS)

            # Semi-transparent logo (70% opacity)
            r, g, b, a = logo.split()
            a = a.point(lambda x: int(x * 0.70))
            logo.putalpha(a)

            # Bottom-right corner, 20px padding
            pad = 20
            x = 1080 - logo_w - pad
            y = 1080 - logo_h - pad
            news_img.paste(logo, (x, y), logo)

        # Save aur upload
        final = news_img.convert("RGB")
        path  = os.path.join(tempfile.gettempdir(), f"watermarked_{int(time.time())}.jpg")
        final.save(path, "JPEG", quality=92)
        new_url = upload_image(path)
        return new_url if new_url else image_url

    except Exception as e:
        print(f"      Watermark error: {e} — original image use kar raha hoon")
        return image_url


# --- Story Card + Story Post --------------------------------------------------
def create_story_card(title: str, source: str, emoji_title: str = "Breaking News") -> str | None:
    """1080x1920 vertical story card banao"""
    print("      Story card bana raha hoon...")
    try:
        from PIL import ImageFont
        width, height = 1080, 1920
        img = Image.new("RGB", (width, height), color=(8, 8, 20))
        draw = ImageDraw.Draw(img)

        # Gradient background
        for i in range(height):
            shade = int(8 + (i / height) * 30)
            draw.line([(0, i), (width, i)], fill=(shade, shade, shade + 20))

        # Top red accent
        draw.rectangle([0, 0, width, 12], fill=(220, 40, 40))

        try:
            font_big   = ImageFont.load_default(size=72)
            font_mid   = ImageFont.load_default(size=44)
            font_small = ImageFont.load_default(size=34)
        except Exception:
            font_big = font_mid = font_small = ImageFont.load_default()

        # BREAKING label
        draw.rectangle([60, 180, 520, 260], fill=(220, 40, 40))
        draw.text((80, 188), "  BREAKING NEWS  ", font=font_small, fill=(255, 255, 255))

        # Emoji title
        draw.text((60, 300), emoji_title, font=font_mid, fill=(255, 80, 80))

        # Source + date
        draw.text((60, 380), f"{source.upper()}  |  {datetime.now().strftime('%d %b %Y  •  %I:%M %p')}",
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


def auto_first_comment(media_id: str, hashtags: str) -> None:
    """Post ke baad hashtags first comment mein daalo — caption clean dikhti hai"""
    if not INSTAGRAM_TOKEN or media_id == "dry_run":
        return
    try:
        resp = requests.post(
            f"https://graph.facebook.com/v25.0/{media_id}/comments",
            data={"message": hashtags, "access_token": INSTAGRAM_TOKEN},
            timeout=10
        )
        if resp.json().get("id"):
            print(f"      First comment (hashtags) posted!")
        else:
            print(f"      First comment error: {resp.json()}")
    except Exception as e:
        print(f"      First comment error: {e}")


def generate_reply(comment_text: str, post_caption: str) -> str:
    """Groq se comment ka friendly Hinglish reply banao"""
    try:
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
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
        return
    print(f"\n[Comments] Naye comments check kar raha hoon...")

    try:
        media_resp = requests.get(
            f"https://graph.facebook.com/v25.0/{INSTAGRAM_ACCOUNT_ID}/media",
            params={"fields": "id,caption,timestamp", "limit": 5,
                    "access_token": INSTAGRAM_TOKEN},
            timeout=10
        )
        posts = media_resp.json().get("data", [])
        replied = 0

        for post in posts:
            comments_resp = requests.get(
                f"https://graph.facebook.com/v25.0/{post['id']}/comments",
                params={"fields": "id,text,username,timestamp,replies{id}",
                        "access_token": INSTAGRAM_TOKEN},
                timeout=10
            )
            for comment in comments_resp.json().get("data", []):
                # Skip if already replied to
                if comment.get("replies", {}).get("data"):
                    continue
                # Skip comments older than 48 hours
                try:
                    from datetime import datetime as dt
                    age = (dt.now().timestamp() -
                           dt.fromisoformat(comment["timestamp"].replace("Z", "+00:00")).timestamp())
                    if age > 172800:
                        continue
                except Exception:
                    pass

                text = comment.get("text", "")
                username = comment.get("username", "")
                print(f"      @{username}: {text[:60]}")

                reply = generate_reply(text, post.get("caption", ""))
                reply_resp = requests.post(
                    f"https://graph.facebook.com/v25.0/{comment['id']}/replies",
                    data={"message": reply, "access_token": INSTAGRAM_TOKEN},
                    timeout=10
                )
                if reply_resp.json().get("id"):
                    print(f"      Replied: {reply[:60]}")
                    replied += 1
                    time.sleep(3)

        print(f"      {replied} comments pe reply kiya")
    except Exception as e:
        print(f"      Comments error: {e}")


# --- Main Agent Loop ----------------------------------------------------------
def run_agent():
    print("=" * 55)
    print("  Instagram News Agent Starting...")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 55)

    # Token refresh karo (60-day long-lived token)
    refresh_token()

    # 1. Multiple topics se news fetch karo
    all_news = []
    for topic in NEWS_TOPICS:
        results = fetch_news(topic, max_results=3)
        all_news.extend(results)

    # Sirf wahi news jisme image ho — no image = skip
    all_news = [n for n in all_news if n.get("image")]
    print(f"      Image wali news: {len(all_news)}")

    if not all_news:
        print("Koi image wali news nahi mili. Agent band ho raha hai.")
        return

    # 2. AI se smart plan banwao
    news_list = smart_plan(all_news)

    posted = 0
    for news in news_list:
        if posted >= MAX_NEWS:
            break

        print(f"\n{'-'*50}")
        print(f"News: {news.get('title', '')[:70]}...")
        fmt = news.get("_format", "image")
        print(f"Format: {fmt} | Reason: {news.get('_reason', '')[:50]}")

        print(f"Importance: {news.get('_importance', '?')}/10")

        # 3. Caption generate karo
        content = generate_caption(news)

        # 5. AI ke source + format decision ke hisaab se content lo
        src = news.get("_image_source", "pexels")
        print(f"Image source: {src}")

        image_path = news.get("image")
        if not image_path:
            print("      Image nahi mili — skip")
            continue

        # Logo watermark lagao
        print(f"      Logo watermark laga raha hoon...")
        image_path = add_logo_watermark(image_path)

        # Hashtags caption mein nahi — first comment mein jaayenge
        media_id = post_to_instagram(image_path, content["caption"])

        if media_id:
            time.sleep(2)
            auto_first_comment(media_id, content["hashtags"])
            posted += 1
            print(f"      [{posted}/{MAX_NEWS}] Post ho gaya!")

            # Story sirf 8am aur 6pm run pe (2 stories daily)
            hour = datetime.now().hour
            if hour in (8, 18):
                story_url = create_story_card(
                    news.get("title", ""),
                    news.get("source", ""),
                    content.get("emoji_title", "Breaking News")
                )
                if story_url:
                    post_story(story_url)

            if posted < MAX_NEWS:
                print(f"      {POST_DELAY}s wait kar raha hoon...")
                time.sleep(POST_DELAY)

    # Recent comments pe auto-reply karo
    reply_to_recent_comments()

    print(f"\n{'='*55}")
    print(f"  Agent complete! {posted} posts kiye gaye.")
    print("=" * 55)


if __name__ == "__main__":
    run_agent()
