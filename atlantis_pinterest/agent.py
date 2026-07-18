"""
Atlantis Pinterest Agent
========================
100% copyright-free images automatically Pinterest pe post karta hai.

Safe Sources ONLY:
  NASA APOD / JWST / Mars / EPIC  — US Government Public Domain
  PIB India                        — Government of India Public Domain
  iNaturalist                      — CC-BY (research grade)
  GBIF                             — CC0 / CC-BY open data
  Pexels                           — CC0 commercial free
  Pixabay                          — CC0 commercial free
  Wikimedia Commons                — CC-BY / CC0 filtered

Boards:
  SPACE_BOARD_ID    → NASA/JWST/Cosmos pins
  WILDLIFE_BOARD_ID → iNaturalist/Nature pins
  INDIA_BOARD_ID    → PIB India / government pins
  SCIENCE_BOARD_ID  → General science / discovery pins
"""

import os, sys, json, time, tempfile, requests, colorsys
from datetime import datetime
from dotenv import load_dotenv
from PIL import Image, ImageDraw
from groq import Groq

_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(_env)

# --- Config -------------------------------------------------------------------
GROQ_API_KEY      = os.getenv("GROQ_API_KEY", "")
PEXELS_API_KEY    = os.getenv("PEXELS_API_KEY", "")
PIXABAY_API_KEY   = os.getenv("PIXABAY_API_KEY", "")
IMGBB_API_KEY     = os.getenv("IMGBB_API_KEY", "")
NASA_API_KEY      = os.getenv("NASA_API_KEY", "DEMO_KEY")
PINTEREST_TOKEN   = os.getenv("PINTEREST_ACCESS_TOKEN", "")

SPACE_BOARD_ID    = os.getenv("PINTEREST_SPACE_BOARD_ID", "")
WILDLIFE_BOARD_ID = os.getenv("PINTEREST_WILDLIFE_BOARD_ID", "")
INDIA_BOARD_ID    = os.getenv("PINTEREST_INDIA_BOARD_ID", "")
SCIENCE_BOARD_ID  = os.getenv("PINTEREST_SCIENCE_BOARD_ID", "")

# --- Groq model auto-select: best available model khud pick karo (future-proof) ---
GROQ_MODEL_PREFERENCES = [
    "openai/gpt-oss-120b",      # 2026: sabse smart Groq model
    "llama-3.3-70b-versatile",  # proven fallback
    "llama-3.1-8b-instant",     # last resort
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

CHANNEL_HANDLE  = "@atlantis_pinterest"
HISTORY_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "posted_history.json")
LOGO_PATH       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "atlantis_pinterest.png")

# Pinterest optimal: 2:3 ratio
PIN_W, PIN_H = 1000, 1500

# Posts per run (GitHub Actions cron pe 3x/day = ~9 pins/day)
PINS_PER_RUN = 3


# --- Fonts --------------------------------------------------------------------
def get_font(size: int):
    from PIL import ImageFont
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansBold.ttf",
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


# --- History ------------------------------------------------------------------
def load_history() -> dict:
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"titles": [], "images": []}


def is_duplicate(title: str, image_url: str = "") -> bool:
    data = load_history()
    titles = set(data.get("titles", []))
    images = set(data.get("images", []))
    STOP = {"the","a","an","is","in","of","on","at","to","for","and","or","with","from","by"}
    words = set(title.lower().split()) - STOP
    for stored in titles:
        stored_words = set(stored.split()) - STOP
        if stored_words and words:
            overlap = len(words & stored_words) / max(len(words), len(stored_words))
            if overlap >= 0.55:
                return True
    if image_url and image_url.strip()[:100] in images:
        return True
    return False


def save_history(title: str, image_url: str = "") -> None:
    try:
        import subprocess
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        subprocess.run(["git", "stash"], cwd=repo_dir, capture_output=True)
        subprocess.run(["git", "pull", "origin", "main", "--no-rebase"], cwd=repo_dir, capture_output=True)
        subprocess.run(["git", "stash", "pop"], cwd=repo_dir, capture_output=True)

        data = load_history()
        titles = data.get("titles", [])
        images = data.get("images", [])
        norm = title.lower().strip()[:120]
        if norm not in titles:
            titles.append(norm)
        titles = titles[-500:]
        if image_url:
            key = image_url.strip()[:100]
            if key not in images:
                images.append(key)
            images = images[-500:]

        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump({"titles": titles, "images": images,
                       "updated": datetime.now().isoformat()}, f, ensure_ascii=False, indent=2)

        subprocess.run(["git", "add", "posted_history.json"], cwd=repo_dir)
        r = subprocess.run(
            ["git", "commit", "-m", "chore: update pinterest history [skip ci]"],
            cwd=repo_dir, capture_output=True
        )
        if r.returncode == 0:
            subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=repo_dir)
        print(f"      History saved ({len(titles)} pins)")
    except Exception as e:
        print(f"      History error: {e}")


# --- Image Overlay (Pinterest 2:3 format) -------------------------------------
def create_pin_image(image_url: str, title: str, fact: str = "",
                     source_label: str = "", category: str = "") -> str | None:
    """Download image, create Pinterest 2:3 overlay, return local path"""
    try:
        import io
        resp = requests.get(image_url, timeout=15,
                            headers={"User-Agent": "AtlantisPinterestBot/1.0"})
        if resp.status_code != 200:
            return None

        img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
        w, h = img.size

        # Crop to 2:3 — center crop
        target_ratio = PIN_W / PIN_H
        current_ratio = w / h
        if current_ratio > target_ratio:
            new_w = int(h * target_ratio)
            img = img.crop(((w - new_w) // 2, 0, (w + new_w) // 2, h))
        else:
            new_h = int(w / target_ratio)
            img = img.crop((0, (h - new_h) // 2, w, (h + new_h) // 2))

        img = img.resize((PIN_W, PIN_H), Image.LANCZOS)

        # Color palette from image
        sample = img.resize((80, 80)).convert("RGB")
        raw = sample.tobytes()
        n = 80 * 80
        avg_r = sum(raw[0::3]) // n
        avg_g = sum(raw[1::3]) // n
        avg_b = sum(raw[2::3]) // n
        h_hue, s, v = colorsys.rgb_to_hsv(avg_r/255, avg_g/255, avg_b/255)
        accent = tuple(int(c*255) for c in colorsys.hsv_to_rgb(h_hue, min(s+0.35, 1), 0.9))
        dark   = tuple(int(c*255) for c in colorsys.hsv_to_rgb(h_hue, min(s+0.2, 0.85), 0.12))

        # Gradient overlay — bottom 45% of image
        overlay = Image.new("RGBA", (PIN_W, PIN_H), (0, 0, 0, 0))
        ov_draw = ImageDraw.Draw(overlay)
        grad_start = int(PIN_H * 0.55)
        for i in range(PIN_H - grad_start):
            alpha = int(235 * (i / (PIN_H - grad_start)) ** 0.7)
            ov_draw.line([(0, grad_start + i), (PIN_W, grad_start + i)],
                         fill=(*dark, alpha))
        img = Image.alpha_composite(img, overlay)
        draw = ImageDraw.Draw(img)

        # Top accent bar
        draw.rectangle([0, 0, PIN_W, 8], fill=(*accent, 255))

        # Category tag (top-left)
        if category:
            tag_font = get_font(28)
            tag_text = f"  {category.upper()}  "
            draw.rectangle([20, 20, 20 + len(tag_text) * 16, 56],
                           fill=(*accent, 220))
            draw.text((28, 22), tag_text, font=tag_font, fill=(255, 255, 255, 255))

        # Title — bottom section
        title_font = get_font(54)
        fact_font  = get_font(34)
        src_font   = get_font(28)

        y = int(PIN_H * 0.58)

        # Wrap title
        words = title.split()
        lines, line = [], ""
        for word in words:
            test = f"{line} {word}".strip()
            if len(test) > 24:
                if line:
                    lines.append(line)
                line = word
            else:
                line = test
        if line:
            lines.append(line)

        for l in lines[:3]:
            draw.text((30, y), l, font=title_font, fill=(255, 255, 255, 255))
            y += 66

        # Fact / description
        if fact:
            y += 10
            fact_words = fact.split()
            fact_lines, fact_line = [], ""
            for word in fact_words:
                test = f"{fact_line} {word}".strip()
                if len(test) > 36:
                    if fact_line:
                        fact_lines.append(fact_line)
                    fact_line = word
                else:
                    fact_line = test
            if fact_line:
                fact_lines.append(fact_line)
            for fl in fact_lines[:3]:
                draw.text((30, y), fl, font=fact_font, fill=(210, 210, 210, 240))
                y += 44

        # Source + handle — bottom
        date_str = datetime.now().strftime("%d %b %Y")
        src_color = tuple(min(255, int(c * 1.3 + 50)) for c in accent)
        src_text  = f"{source_label}  •  {date_str}  •  {CHANNEL_HANDLE}"
        draw.text((30, PIN_H - 50), src_text, font=src_font, fill=(*src_color, 220))

        # Logo (if exists)
        if os.path.exists(LOGO_PATH):
            try:
                logo = Image.open(LOGO_PATH).convert("RGBA")
                logo_w = int(PIN_W * 0.12)
                logo_h = int(logo.height * (logo_w / logo.width))
                logo = logo.resize((logo_w, logo_h), Image.LANCZOS)
                img.paste(logo, (PIN_W - logo_w - 20, PIN_H - logo_h - 20), logo)
            except Exception:
                pass

        # Save
        ts = int(time.time())
        out = os.path.join(tempfile.gettempdir(), f"pin_{ts}.jpg")
        img.convert("RGB").save(out, "JPEG", quality=92)
        return out
    except Exception as e:
        print(f"      Overlay error: {e}")
        return None


# --- Upload to ImgBB ----------------------------------------------------------
def upload_imgbb(file_path: str) -> str | None:
    if not IMGBB_API_KEY:
        return None
    try:
        import base64
        with open(file_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        resp = requests.post("https://api.imgbb.com/1/upload",
                             data={"key": IMGBB_API_KEY, "image": b64}, timeout=30)
        url = resp.json().get("data", {}).get("url")
        if url:
            print(f"      ImgBB: {url[:60]}...")
        return url
    except Exception as e:
        print(f"      ImgBB error: {e}")
        return None


# --- Pinterest Post -----------------------------------------------------------
def post_to_pinterest(image_url: str, board_id: str, title: str,
                      description: str, link: str = "") -> str | None:
    if not PINTEREST_TOKEN or not board_id:
        print("      Pinterest token ya board_id missing")
        return None
    try:
        payload = {
            "board_id": board_id,
            "title":    title[:100],
            "description": description[:500],
            "media_source": {
                "source_type": "image_url",
                "url":          image_url
            },
        }
        if link:
            payload["link"] = link

        resp = requests.post(
            "https://api.pinterest.com/v5/pins",
            headers={"Authorization": f"Bearer {PINTEREST_TOKEN}",
                     "Content-Type":  "application/json"},
            json=payload, timeout=30
        )
        data = resp.json()
        pin_id = data.get("id")
        if pin_id:
            print(f"      Pinterest pin: {pin_id}")
            return pin_id
        print(f"      Pinterest error: {data}")
        return None
    except Exception as e:
        print(f"      Pinterest post error: {e}")
        return None


# --- Groq SEO Description -----------------------------------------------------
def generate_pin_description(title: str, fact: str, category: str) -> str:
    if not GROQ_API_KEY:
        return f"{title}. {fact}"
    try:
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": f"""
Pinterest pin ke liye SEO description likho — English mein.
Topic: {title}
Fact: {fact}
Category: {category}

Rules:
- 3-4 sentences max
- Keywords rich — Pinterest search mein aaye
- Engaging aur informative
- No hashtags (Pinterest description mein hashtags work nahi karte)
- End with a subtle CTA like "Follow for more" or "Save this pin"

Description:"""}]
        )
        desc = resp.choices[0].message.content.strip()
        import re
        desc = re.sub(r'#\w+', '', desc).strip()
        return desc[:500]
    except Exception:
        return f"{title}. {fact}. Follow for more amazing content!"


# =============================================================================
# COPYRIGHT-FREE IMAGE SOURCES
# =============================================================================

# --- NASA Sources (Public Domain) --------------------------------------------
def fetch_nasa_apod() -> list[dict]:
    """NASA Astronomy Picture of the Day — guaranteed public domain"""
    try:
        resp = requests.get(
            "https://api.nasa.gov/planetary/apod",
            params={"api_key": NASA_API_KEY, "count": 5},
            timeout=12
        )
        items = []
        for apod in resp.json():
            if apod.get("media_type") != "image":
                continue
            url = apod.get("hdurl") or apod.get("url", "")
            if not url:
                continue
            items.append({
                "title":    apod.get("title", "NASA Astronomy Picture"),
                "fact":     apod.get("explanation", "")[:200],
                "image":    url,
                "source":   "NASA APOD",
                "category": "space",
                "link":     "https://apod.nasa.gov",
            })
        print(f"      NASA APOD: {len(items)} images")
        return items
    except Exception as e:
        print(f"      NASA APOD error: {e}")
        return []


def fetch_nasa_mars() -> list[dict]:
    """NASA Mars Rover photos — public domain"""
    try:
        resp = requests.get(
            "https://api.nasa.gov/mars-photos/api/v1/rovers/curiosity/latest_photos",
            params={"api_key": NASA_API_KEY},
            timeout=12
        )
        photos = resp.json().get("latest_photos", [])
        import random
        random.shuffle(photos)
        items = []
        for p in photos[:4]:
            items.append({
                "title":    f"Mars Surface — {p.get('camera', {}).get('full_name', 'Curiosity Rover')}",
                "fact":     f"Sol {p.get('sol', '?')} — Mars pe Curiosity Rover ne capture kiya. NASA ki public domain photo.",
                "image":    p.get("img_src", ""),
                "source":   "NASA Mars",
                "category": "space",
                "link":     "https://mars.nasa.gov",
            })
        print(f"      NASA Mars: {len(items)} photos")
        return items
    except Exception as e:
        print(f"      NASA Mars error: {e}")
        return []


def fetch_nasa_epic() -> list[dict]:
    """NASA EPIC — Earth images from space, public domain"""
    try:
        resp = requests.get(
            "https://api.nasa.gov/EPIC/api/natural",
            params={"api_key": NASA_API_KEY},
            timeout=12
        )
        data = resp.json()
        import random
        random.shuffle(data)
        items = []
        for img in data[:3]:
            date_str  = img.get("date", "")[:10].replace("-", "/")
            img_name  = img.get("image", "")
            url = f"https://epic.gsfc.nasa.gov/archive/natural/{date_str.replace('/', '/')}/png/{img_name}.png"
            items.append({
                "title":    "Earth from Space — NASA EPIC",
                "fact":     img.get("caption", "NASA DSCOVR satellite se li gayi Earth ki photo."),
                "image":    url,
                "source":   "NASA EPIC",
                "category": "space",
                "link":     "https://epic.gsfc.nasa.gov",
            })
        print(f"      NASA EPIC: {len(items)} images")
        return items
    except Exception as e:
        print(f"      NASA EPIC error: {e}")
        return []


# --- iNaturalist (CC-BY) -----------------------------------------------------
def fetch_inaturalist_pins() -> list[dict]:
    """iNaturalist research-grade observations — CC-BY licensed"""
    import random
    items = []
    taxa_list = [
        ("Mammalia", "wildlife"),
        ("Aves", "wildlife"),
        ("Reptilia", "science"),
        ("Insecta", "science"),
        ("Actinopterygii", "wildlife"),
    ]
    try:
        for taxa, category in taxa_list:
            resp = requests.get(
                "https://api.inaturalist.org/v1/observations",
                params={
                    "quality_grade": "research",
                    "photos":        "true",
                    "order":         "desc",
                    "order_by":      "votes",
                    "per_page":      15,
                    "iconic_taxa":   taxa,
                },
                timeout=12,
                headers={"User-Agent": "AtlantisPinterestBot/1.0"}
            )
            obs_list = resp.json().get("results", [])
            random.shuffle(obs_list)
            for obs in obs_list[:5]:
                taxon  = obs.get("taxon", {}) or {}
                name   = taxon.get("preferred_common_name", "") or taxon.get("name", "")
                sci    = taxon.get("name", "")
                place  = obs.get("place_guess", "")
                photos = obs.get("photos", [])
                img    = photos[0].get("url", "").replace("square", "large") if photos else ""
                desc   = taxon.get("wikipedia_summary", "")[:200]
                if name and img:
                    items.append({
                        "title":    f"{name}",
                        "fact":     desc or f"{name} ({sci}) — iNaturalist research grade observation. CC-BY licensed.",
                        "image":    img,
                        "source":   "iNaturalist",
                        "category": category,
                        "link":     obs.get("uri", "https://www.inaturalist.org"),
                    })
                    break
    except Exception as e:
        print(f"      iNaturalist error: {e}")
    print(f"      iNaturalist: {len(items)} observations")
    return items


# --- GBIF (CC0 / CC-BY) -------------------------------------------------------
def fetch_gbif_pins() -> list[dict]:
    """GBIF biodiversity observations — CC0/CC-BY open data"""
    import random
    items = []
    try:
        resp = requests.get(
            "https://api.gbif.org/v1/occurrence/search",
            params={
                "mediaType":      "StillImage",
                "basisOfRecord":  "HUMAN_OBSERVATION",
                "hasCoordinate":  "true",
                "taxonKey":       "1",
                "limit":          30,
            },
            timeout=12,
            headers={"User-Agent": "AtlantisPinterestBot/1.0"}
        )
        results = resp.json().get("results", [])
        random.shuffle(results)
        for obs in results:
            name  = obs.get("vernacularName", "") or obs.get("species", "")
            sci   = obs.get("species", "")
            media = obs.get("media", [])
            img   = media[0].get("identifier", "") if media else ""
            country = obs.get("country", "")
            if name and img and img.startswith("http"):
                items.append({
                    "title":    f"{name}",
                    "fact":     f"{name} ({sci}) — {country} mein observe kiya gaya. GBIF global biodiversity open data.",
                    "image":    img,
                    "source":   "GBIF",
                    "category": "wildlife",
                    "link":     "https://www.gbif.org",
                })
            if len(items) >= 3:
                break
    except Exception as e:
        print(f"      GBIF error: {e}")
    print(f"      GBIF: {len(items)} observations")
    return items


# --- Pexels (CC0) -------------------------------------------------------------
def fetch_pexels_pins(keywords: list[str]) -> list[dict]:
    """Pexels CC0 images — completely copyright-free"""
    if not PEXELS_API_KEY:
        return []
    import random
    items = []
    random.shuffle(keywords)
    try:
        for keyword in keywords[:4]:
            resp = requests.get(
                "https://api.pexels.com/v1/search",
                params={"query": keyword, "per_page": 10, "orientation": "portrait"},
                headers={"Authorization": PEXELS_API_KEY},
                timeout=10
            )
            photos = resp.json().get("photos", [])
            if photos:
                p = random.choice(photos[:5])
                url = p.get("src", {}).get("large2x") or p.get("src", {}).get("large", "")
                if url:
                    items.append({
                        "title":    keyword.title(),
                        "fact":     f"Stunning {keyword} photography. Pexels CC0 — free to use.",
                        "image":    url,
                        "source":   "Pexels CC0",
                        "category": _guess_category(keyword),
                        "link":     p.get("url", "https://pexels.com"),
                    })
    except Exception as e:
        print(f"      Pexels error: {e}")
    print(f"      Pexels: {len(items)} images")
    return items


def _guess_category(keyword: str) -> str:
    kw = keyword.lower()
    if any(w in kw for w in ["space", "galaxy", "star", "planet", "cosmos", "nebula", "moon"]):
        return "space"
    if any(w in kw for w in ["animal", "wildlife", "bird", "tiger", "elephant", "nature", "forest"]):
        return "wildlife"
    if any(w in kw for w in ["india", "temple", "festival", "diwali", "holi"]):
        return "india"
    return "science"


# --- PIB India (Public Domain) -----------------------------------------------
def fetch_pib_pins() -> list[dict]:
    """PIB India — Government of India public domain images"""
    import xml.etree.ElementTree as ET
    import re as _re
    items = []
    try:
        resp = requests.get(
            "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3",
            timeout=12,
            headers={"User-Agent": "AtlantisPinterestBot/1.0"}
        )
        root = ET.fromstring(resp.content)
        for item in root.findall(".//item")[:8]:
            t_el = item.find("title")
            title = (t_el.text or "").strip() if t_el is not None else ""
            d_el = item.find("description")
            raw = (d_el.text or "") if d_el is not None else ""
            m = _re.search(r'<img[^>]+src=["\']([^"\']+)["\']', raw)
            img = m.group(1) if m else ""
            if title and img and img.startswith("http"):
                items.append({
                    "title":    title[:80],
                    "fact":     _re.sub(r'<[^>]+>', '', raw).strip()[:200],
                    "image":    img,
                    "source":   "PIB India",
                    "category": "india",
                    "link":     "https://pib.gov.in",
                })
    except Exception as e:
        print(f"      PIB error: {e}")
    print(f"      PIB India: {len(items)} items")
    return items


# --- Board Router -------------------------------------------------------------
def get_board_id(category: str) -> str:
    mapping = {
        "space":    SPACE_BOARD_ID,
        "wildlife": WILDLIFE_BOARD_ID,
        "india":    INDIA_BOARD_ID,
        "science":  SCIENCE_BOARD_ID,
    }
    board = mapping.get(category, SCIENCE_BOARD_ID)
    if not board:
        # fallback to any available board
        for b in [SPACE_BOARD_ID, WILDLIFE_BOARD_ID, INDIA_BOARD_ID, SCIENCE_BOARD_ID]:
            if b:
                return b
    return board


# =============================================================================
# MAIN
# =============================================================================
def run_agent():
    print("=" * 55)
    print("  Atlantis Pinterest Agent Starting...")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 55)

    if not PINTEREST_TOKEN:
        print("  PINTEREST_ACCESS_TOKEN missing — exit")
        return

    # --- Fetch all copyright-free sources in parallel ---
    from concurrent.futures import ThreadPoolExecutor, as_completed

    pexels_keywords = [
        "space galaxy cosmos nebula",
        "tiger wildlife India forest",
        "eagle bird wildlife nature",
        "coral reef ocean marine",
        "milky way stars night sky",
        "elephant herd Africa savanna",
        "lotus flower India nature",
        "snow leopard mountain wildlife",
        "aurora borealis northern lights",
        "butterfly macro nature insect",
    ]

    sources = [
        fetch_nasa_apod,
        fetch_nasa_mars,
        fetch_nasa_epic,
        fetch_inaturalist_pins,
        fetch_gbif_pins,
        fetch_pib_pins,
        lambda: fetch_pexels_pins(pexels_keywords),
    ]

    all_items = []
    print("\n[Fetch] Copyright-free sources se images fetch kar raha hoon...")
    with ThreadPoolExecutor(max_workers=7) as ex:
        futs = {ex.submit(fn): fn.__name__ if hasattr(fn, '__name__') else 'pexels' for fn in sources}
        for fut in as_completed(futs):
            try:
                result = fut.result()
                if isinstance(result, list):
                    all_items.extend(result)
            except Exception as e:
                print(f"      Source error: {e}")

    print(f"      Total images: {len(all_items)}")

    # --- Duplicate filter ---
    import random
    random.shuffle(all_items)
    fresh = [item for item in all_items
             if not is_duplicate(item.get("title", ""), item.get("image", ""))]
    print(f"      Fresh (non-duplicate): {len(fresh)}")

    if not fresh:
        print("  Sab duplicate — skip")
        return

    # --- Post top N pins ---
    posted = 0
    for item in fresh:
        if posted >= PINS_PER_RUN:
            break

        title    = item.get("title", "Amazing Discovery")
        fact     = item.get("fact", "")
        image    = item.get("image", "")
        source   = item.get("source", "")
        category = item.get("category", "science")
        link     = item.get("link", "")

        if not image:
            continue

        print(f"\n[Pin {posted+1}] {title[:60]}...")
        print(f"      Source: {source} | Category: {category}")

        # Create Pinterest-optimized overlay image
        pin_path = create_pin_image(image, title, fact, source, category)
        if not pin_path:
            print("      Overlay fail — skipping")
            continue

        # Upload to ImgBB for public URL
        pin_url = upload_imgbb(pin_path)
        try:
            os.remove(pin_path)
        except Exception:
            pass

        if not pin_url:
            print("      ImgBB upload fail — skipping")
            continue

        # Generate SEO description
        description = generate_pin_description(title, fact, category)

        # Get board
        board_id = get_board_id(category)
        if not board_id:
            print(f"      Board ID missing for '{category}' — skipping")
            continue

        # Post to Pinterest
        pin_id = post_to_pinterest(pin_url, board_id, title, description, link)
        if pin_id:
            save_history(title, image)
            posted += 1
            print(f"      Posted! ({posted}/{PINS_PER_RUN})")
            time.sleep(10)

    print(f"\n{'='*55}")
    print(f"  Done! {posted}/{PINS_PER_RUN} pins posted.")
    print("=" * 55)


if __name__ == "__main__":
    run_agent()
