"""
Atlantis Space — Instagram Agent
=================================
Space, astronomy, ISRO, NASA news automatically fetch karke Instagram pe post karta hai.
Same infrastructure as atlantis_news_ai, space branding ke saath.

Run:
    python atlantis_space/agent.py
"""

import os
import sys
import json
import time
import tempfile
import colorsys
import requests

# Parent folder se common utilities import karo
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from dotenv import load_dotenv
from ddgs import DDGS
from PIL import Image, ImageDraw
from groq import Groq

# Space agent ka apna .env load karo
_space_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(_space_env)

# --- Config -------------------------------------------------------------------
GROQ_API_KEY         = os.getenv("GROQ_API_KEY")
PEXELS_API_KEY       = os.getenv("PEXELS_API_KEY")
INSTAGRAM_TOKEN      = os.getenv("SPACE_INSTAGRAM_ACCESS_TOKEN")
INSTAGRAM_ACCOUNT_ID = os.getenv("SPACE_INSTAGRAM_ACCOUNT_ID")
IMGBB_API_KEY        = os.getenv("IMGBB_API_KEY")
APP_ID               = os.getenv("SPACE_APP_ID")
APP_SECRET           = os.getenv("SPACE_APP_SECRET")

NASA_API_KEY         = os.getenv("NASA_API_KEY")

CHANNEL_HANDLE  = "@atlantis_space"
POST_DELAY      = 45   # seconds between posts in same run
CAROUSEL_SLIDES = 2    # 2 posts per run × 5 runs = 10 posts/day

LOGO_PATH        = os.path.join(os.path.dirname(os.path.abspath(__file__)), "atlantis_space.png")
HISTORY_FILE     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "posted_history.json")
YT_HISTORY_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "youtube_history.json")

# YouTube config (only active when YOUTUBE_ONLY=true or YT creds present)
YOUTUBE_CLIENT_ID     = os.getenv("YOUTUBE_CLIENT_ID", "")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET", "")
YOUTUBE_REFRESH_TOKEN = os.getenv("YOUTUBE_REFRESH_TOKEN", "")
YOUTUBE_ONLY          = os.getenv("YOUTUBE_ONLY", "false").lower() == "true"

# YouTube-only mode overrides
if YOUTUBE_ONLY:
    HISTORY_FILE    = YT_HISTORY_FILE   # separate duplicate tracking
    CAROUSEL_SLIDES = 1                 # 1 Short/run × 5 runs = 5/day = 8,000 API units (hard limit: 10,000)

SPACE_DISCOVERY_TOPICS = [
    "ISRO India space mission launch 2025",
    "Mars Moon Jupiter solar system discovery",
    "black hole nebula galaxy telescope image",
    "astronaut ISS space station update",
    "asteroid comet solar flare space event",
]


# --- Shared utilities (copy from parent to keep standalone) -------------------
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
    print(f"\n[1/4] Space news fetch: '{topic}'")
    cutoff = datetime.now().timestamp() - 86400
    strategies = [{"timelimit": "d"}, {"timelimit": "w"}, {}]

    for attempt, params in enumerate(strategies):
        try:
            time.sleep(attempt * 4)
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
                    if pub_ts >= cutoff - 86400 * attempt:
                        fresh.append(n)
                except Exception:
                    fresh.append(n)
            fresh = fresh[:max_results]
            if fresh:
                print(f"      {len(fresh)} news mili")
                return fresh
            raise Exception("No fresh results after filtering.")
        except Exception as e:
            print(f"      Attempt {attempt+1} failed: {e}")
    return []


# --- NASA Sources -------------------------------------------------------------
def fetch_nasa_apod() -> dict | None:
    """NASA Astronomy Picture of the Day — stunning daily image with description"""
    try:
        resp = requests.get(
            "https://api.nasa.gov/planetary/apod",
            params={"api_key": NASA_API_KEY or "DEMO_KEY", "thumbs": "true"},
            timeout=10
        )
        data = resp.json()
        if data.get("url") and data.get("title"):
            img_url = data.get("hdurl") or data.get("url")
            # Only use if it's an image (not YouTube video)
            if not img_url.endswith((".jpg", ".jpeg", ".png", ".gif")):
                img_url = data.get("thumbnail_url") or data.get("url")
            print(f"      NASA APOD: {data['title'][:60]}")
            return {
                "title": data["title"],
                "body": data.get("explanation", "")[:500],
                "image": img_url,
                "source": "NASA APOD",
                "date": data.get("date", ""),
                "_nasa": True
            }
    except Exception as e:
        print(f"      APOD error: {e}")
    return None


def fetch_spaceflight_news(max_results: int = 5) -> list[dict]:
    """Spaceflight News API — real-time space news, no key needed"""
    try:
        resp = requests.get(
            "https://api.spaceflightnewsapi.net/v4/articles/",
            params={"limit": max_results, "ordering": "-published_at"},
            timeout=10
        )
        articles = resp.json().get("results", [])
        news = []
        for a in articles:
            if a.get("image_url") and a.get("title"):
                news.append({
                    "title": clean_title(a["title"]),
                    "body": a.get("summary", "")[:500],
                    "image": a["image_url"],
                    "source": a.get("news_site", "Space News"),
                    "date": a.get("published_at", ""),
                    "url": a.get("url", "")
                })
        print(f"      Spaceflight News: {len(news)} articles")
        return news
    except Exception as e:
        print(f"      Spaceflight News error: {e}")
    return []


def fetch_nasa_image(keyword: str) -> str | None:
    """NASA Image & Video Library se high-quality space image URL lo"""
    try:
        resp = requests.get(
            "https://images-api.nasa.gov/search",
            params={"q": keyword, "media_type": "image", "page_size": 5},
            timeout=10
        )
        items = resp.json().get("collection", {}).get("items", [])
        for item in items:
            links = item.get("links", [])
            for link in links:
                if link.get("render") == "image":
                    url = link["href"]
                    print(f"      NASA image: {url[:60]}")
                    return url
    except Exception as e:
        print(f"      NASA image error: {e}")
    return None


def fetch_nasa_eonet() -> list[dict]:
    """NASA EONET — Earth Observatory Natural Events (space weather, solar flares)"""
    try:
        resp = requests.get(
            "https://eonet.gsfc.nasa.gov/api/v3/events",
            params={
                "api_key": NASA_API_KEY or "DEMO_KEY",
                "limit": 5,
                "status": "open",
                "category": "severeStorms,wildfires,volcanoes,seaLakeIce"
            },
            timeout=10
        )
        events = resp.json().get("events", [])
        news = []
        for e in events[:3]:
            title = e.get("title", "")
            category = e.get("categories", [{}])[0].get("title", "Earth Event")
            geometry = e.get("geometry", [{}])
            date = geometry[-1].get("date", "") if geometry else ""
            if title:
                today = datetime.now().strftime("%d %b")
                news.append({
                    "title": f"{title} — NASA EONET {today}",
                    "body": f"NASA Earth Observatory ne detect kiya: {title}. Category: {category}. Real-time satellite data se track ho raha hai.",
                    "image": "https://epic.gsfc.nasa.gov/epic-galleries/2022/high_cadence/thumbs/epic_1b_20220613185138.jpg",
                    "source": "NASA EONET",
                    "date": date
                })
        print(f"      NASA EONET: {len(news)} events")
        return news
    except Exception as e:
        print(f"      EONET error: {e}")
    return []


def fetch_spacedevs_launches() -> list[dict]:
    """SpaceDevs Launch Library — upcoming rocket launches worldwide"""
    try:
        resp = requests.get(
            "https://ll.thespacedevs.com/2.2.0/launch/upcoming/",
            params={"limit": 5, "format": "json"},
            timeout=10,
            headers={"User-Agent": "AtlantisSpaceBot/1.0"}
        )
        results = resp.json().get("results", [])
        news = []
        for launch in results[:3]:
            name = launch.get("name", "")
            net  = launch.get("net", "")       # Net Expected Time
            pad  = launch.get("pad", {}).get("location", {}).get("name", "")
            rocket = launch.get("rocket", {}).get("configuration", {}).get("full_name", "")
            img  = launch.get("image", "") or launch.get("rocket", {}).get("configuration", {}).get("image_url", "")
            agency = launch.get("launch_service_provider", {}).get("name", "")
            if name and img:
                try:
                    from datetime import datetime as dt
                    launch_dt = dt.fromisoformat(net.replace("Z", "+00:00"))
                    date_str = launch_dt.strftime("%d %b %Y %H:%M UTC")
                except Exception:
                    date_str = net[:10] if net else "Soon"
                news.append({
                    "title": f"Upcoming Launch: {name}",
                    "body": f"{agency} ka {rocket} rocket {date_str} ko {pad} se launch hoga. {name} mission.",
                    "image": img,
                    "source": "SpaceDevs",
                    "date": net[:10] if net else ""
                })
        print(f"      SpaceDevs launches: {len(news)} upcoming")
        return news
    except Exception as e:
        print(f"      SpaceDevs error: {e}")
    return []


# --- Launch Library 2 Extended Endpoints -------------------------------------

def fetch_spacedevs_events() -> list[dict]:
    """SpaceDevs Events — spacewalks, landings, engine tests, crewed milestones"""
    try:
        resp = requests.get(
            "https://ll.thespacedevs.com/2.2.0/event/upcoming/",
            params={"limit": 5, "format": "json"},
            timeout=10,
            headers={"User-Agent": "AtlantisSpaceBot/1.0"}
        )
        results = resp.json().get("results", [])
        news = []
        for event in results[:3]:
            name   = event.get("name", "")
            etype  = event.get("type", {}).get("name", "Space Event")
            date   = event.get("date", "")
            desc   = event.get("description", "")[:400]
            img    = event.get("feature_image", "") or event.get("thumbnail", "")
            if name and img:
                try:
                    from datetime import datetime as dt
                    ev_dt    = dt.fromisoformat(date.replace("Z", "+00:00"))
                    date_str = ev_dt.strftime("%d %b %Y")
                except Exception:
                    date_str = date[:10] if date else "Soon"
                news.append({
                    "title": f"{etype}: {name}",
                    "body": desc or f"{name} — {date_str} ko hoga.",
                    "image": img,
                    "source": "SpaceDevs Events",
                    "date": date[:10] if date else ""
                })
        print(f"      SpaceDevs events: {len(news)}")
        return news
    except Exception as e:
        print(f"      SpaceDevs events error: {e}")
    return []


def fetch_spacedevs_astronauts() -> dict | None:
    """SpaceDevs Astronauts — featured active astronaut (Indian/Asian priority)"""
    try:
        # First try Indian astronaut
        for nationality in ["Indian", "Chinese", "Japanese", "South Korean"]:
            resp = requests.get(
                "https://ll.thespacedevs.com/2.2.0/astronaut/",
                params={"status": "Active", "nationality": nationality,
                        "limit": 3, "format": "json"},
                timeout=10,
                headers={"User-Agent": "AtlantisSpaceBot/1.0"}
            )
            results = resp.json().get("results", [])
            for astro in results:
                img = astro.get("profile_image", "") or astro.get("profile_image_thumbnail", "")
                if img:
                    name       = astro.get("name", "")
                    bio        = astro.get("bio", "")[:400]
                    flights    = astro.get("flights_count", 0)
                    spacewalks = astro.get("spacewalks_count", 0)
                    agency     = astro.get("agency", {}).get("name", "") if astro.get("agency") else ""
                    print(f"      Astronaut: {name} ({nationality})")
                    return {
                        "title": f"Astronaut Spotlight: {name} — {nationality} Space Hero",
                        "body": (f"{name} ek {nationality} astronaut hain. {bio[:200]}. "
                                 f"Abh tak {flights} space flights aur {spacewalks} spacewalks kar chuke hain."),
                        "image": img,
                        "source": agency or "SpaceDevs",
                        "date": datetime.now().strftime("%Y-%m-%d")
                    }
        # Fallback: any active astronaut with image
        resp = requests.get(
            "https://ll.thespacedevs.com/2.2.0/astronaut/",
            params={"status": "Active", "limit": 10, "format": "json"},
            timeout=10,
            headers={"User-Agent": "AtlantisSpaceBot/1.0"}
        )
        for astro in resp.json().get("results", []):
            img = astro.get("profile_image", "")
            if img:
                name    = astro.get("name", "")
                bio     = astro.get("bio", "")[:400]
                flights = astro.get("flights_count", 0)
                agency  = astro.get("agency", {}).get("name", "") if astro.get("agency") else ""
                return {
                    "title": f"Astronaut Spotlight: {name}",
                    "body": f"{name}. {bio[:250]}. {flights} space flights complete.",
                    "image": img,
                    "source": agency or "SpaceDevs",
                    "date": datetime.now().strftime("%Y-%m-%d")
                }
    except Exception as e:
        print(f"      Astronaut error: {e}")
    return None


def fetch_spacedevs_expeditions() -> dict | None:
    """SpaceDevs Expeditions — current ISS/space station expedition"""
    try:
        resp = requests.get(
            "https://ll.thespacedevs.com/2.2.0/expedition/",
            params={"limit": 3, "format": "json", "ordering": "-start"},
            timeout=10,
            headers={"User-Agent": "AtlantisSpaceBot/1.0"}
        )
        for exp in resp.json().get("results", []):
            name   = exp.get("name", "")
            start  = exp.get("start", "")[:10]
            end    = exp.get("end", "")
            crew   = exp.get("crew", [])
            img    = exp.get("feature_image", "")
            station = exp.get("spacestation", {}).get("name", "ISS") if exp.get("spacestation") else "ISS"
            if name and img:
                crew_names = [c.get("astronaut", {}).get("name", "") for c in crew[:4] if c.get("astronaut")]
                crew_str   = ", ".join(crew_names) if crew_names else "International crew"
                end_str    = end[:10] if end else "Ongoing"
                return {
                    "title": f"{name} — {station} Pe Abhi Chal Raha Hai",
                    "body": f"{name} {start} ko shuru hua. Crew: {crew_str}. {station} pe {len(crew)} members hain. End: {end_str}.",
                    "image": img,
                    "source": "SpaceDevs / NASA",
                    "date": start
                }
    except Exception as e:
        print(f"      Expedition error: {e}")
    return None


def fetch_spacedevs_dockings() -> dict | None:
    """SpaceDevs Dockings — recent spacecraft docking with space station"""
    try:
        resp = requests.get(
            "https://ll.thespacedevs.com/2.2.0/docking_event/",
            params={"limit": 5, "format": "json", "ordering": "-docking"},
            timeout=10,
            headers={"User-Agent": "AtlantisSpaceBot/1.0"}
        )
        for dock in resp.json().get("results", []):
            flight_vehicle = dock.get("flight_vehicle", {}) or {}
            spacecraft     = flight_vehicle.get("spacecraft", {}) or {}
            sc_name        = spacecraft.get("name", "")
            sc_img         = spacecraft.get("spacecraft_config", {}).get("image_url", "") if spacecraft.get("spacecraft_config") else ""
            station        = dock.get("space_station", {}).get("name", "ISS") if dock.get("space_station") else "ISS"
            docking_time   = dock.get("docking", "")[:10]
            port           = dock.get("docking_location", {}).get("name", "") if dock.get("docking_location") else ""
            if sc_name and sc_img:
                return {
                    "title": f"{sc_name} Ne {station} Ke Saath Docking Ki!",
                    "body": f"{sc_name} spacecraft ne {docking_time} ko {station} ke {port} port se dock kiya. Ye ek critical maneuver hai jisme do spacecraft space mein mile.",
                    "image": sc_img,
                    "source": "SpaceDevs",
                    "date": docking_time
                }
    except Exception as e:
        print(f"      Docking error: {e}")
    return None


# --- More Free Space APIs -----------------------------------------------------

def fetch_spacex_launches() -> list[dict]:
    """SpaceX Community API — upcoming + recent launches (no key needed)"""
    news = []
    try:
        # Upcoming launches
        resp = requests.get("https://api.spacexdata.com/v4/launches/upcoming",
                            timeout=10)
        launches = resp.json()[:3]
        for l in launches:
            name    = l.get("name", "")
            date_unix = l.get("date_unix", 0)
            details = l.get("details") or f"SpaceX ka {name} mission launch hone wala hai."
            patch   = l.get("links", {}).get("patch", {}).get("large") or \
                      l.get("links", {}).get("patch", {}).get("small")
            if not patch:
                continue
            from datetime import datetime as dt
            date_str = dt.utcfromtimestamp(date_unix).strftime("%d %b %Y") if date_unix else "Soon"
            news.append({
                "title": f"SpaceX Launch: {name} — {date_str}",
                "body": details[:400],
                "image": patch,
                "source": "SpaceX",
                "date": dt.utcfromtimestamp(date_unix).isoformat() if date_unix else ""
            })
        print(f"      SpaceX upcoming: {len(news)}")
    except Exception as e:
        print(f"      SpaceX error: {e}")
    return news


def fetch_iss_update() -> dict | None:
    """Open-Notify — ISS realtime location + astronauts in space"""
    try:
        astros = requests.get("http://api.open-notify.org/astros.json", timeout=12).json()
        people = astros.get("people", [])
        iss_crew = [p["name"] for p in people if p.get("craft") == "ISS"]
        total = astros.get("number", len(people))

        # ISS current location
        loc = requests.get("http://api.open-notify.org/iss-now.json", timeout=12).json()
        lat = loc.get("iss_position", {}).get("latitude", "?")
        lon = loc.get("iss_position", {}).get("longitude", "?")

        crew_str = ", ".join(iss_crew[:4]) if iss_crew else "International crew"
        body = (f"Abhi {total} astronauts space mein hain. ISS pe {len(iss_crew)} log hain: {crew_str}. "
                f"ISS ka current location: {float(lat):.1f}°N, {float(lon):.1f}°E.")

        today = datetime.now().strftime("%d %b %Y")
        return {
            "title": f"{total} Astronauts Space Mein Hain — ISS Update {today}",
            "body": body,
            "image": "https://www.nasa.gov/wp-content/uploads/2023/03/iss068e027100.jpg",
            "source": "Open-Notify / NASA",
            "date": datetime.now().isoformat()
        }
    except Exception as e:
        print(f"      ISS update error: {e}")
    return None


def fetch_mars_photos() -> dict | None:
    """NASA Mars Rover (Curiosity/Perseverance) — latest photos from Mars"""
    try:
        for rover in ["perseverance", "curiosity"]:
            resp = requests.get(
                f"https://api.nasa.gov/mars-photos/api/v1/rovers/{rover}/latest_photos",
                params={"api_key": NASA_API_KEY or "DEMO_KEY", "page": 1},
                timeout=10
            )
            photos = resp.json().get("latest_photos", [])
            if photos:
                photo = photos[0]
                sol   = photo.get("sol", "?")
                cam   = photo.get("camera", {}).get("full_name", "Camera")
                img   = photo.get("img_src", "")
                earth_date = photo.get("earth_date", "")
                rover_name = photo.get("rover", {}).get("name", rover.title())
                return {
                    "title": f"{rover_name} Ne Mars Pe Nayi Tasveer Li — Sol {sol}",
                    "body": f"NASA ka {rover_name} rover ne {earth_date} ko {cam} se Mars ki surface ki nayi photo capture ki. Sol {sol} — ye Mars ka {sol}va din hai mission shuru hone ke baad.",
                    "image": img,
                    "source": f"NASA {rover_name}",
                    "date": earth_date
                }
    except Exception as e:
        print(f"      Mars photos error: {e}")
    return None


def fetch_nasa_asteroids() -> dict | None:
    """NASA NeoWs — Near Earth Objects / asteroids passing by Earth"""
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        resp = requests.get(
            "https://api.nasa.gov/neo/rest/v1/feed",
            params={"start_date": today, "end_date": today,
                    "api_key": NASA_API_KEY or "DEMO_KEY"},
            timeout=10
        )
        data = resp.json()
        neos = data.get("near_earth_objects", {}).get(today, [])
        if not neos:
            return None
        # Largest asteroid today
        neo = max(neos, key=lambda x: x.get("estimated_diameter", {})
                  .get("kilometers", {}).get("estimated_diameter_max", 0))
        name = neo.get("name", "Unknown")
        diam = neo.get("estimated_diameter", {}).get("meters", {})
        size = f"{diam.get('estimated_diameter_min', 0):.0f}-{diam.get('estimated_diameter_max', 0):.0f}m"
        hazardous = neo.get("is_potentially_hazardous_asteroid", False)
        approach = neo.get("close_approach_data", [{}])[0]
        distance_km = float(approach.get("miss_distance", {}).get("kilometers", 0))
        velocity = float(approach.get("relative_velocity", {}).get("kilometers_per_hour", 0))
        hazard_text = "⚠️ Potentially hazardous!" if hazardous else "Safe passage."
        return {
            "title": f"Asteroid {name} Aaj Earth Ke Paas Se Guzrega — {size}",
            "body": f"NASA ne track kiya: Asteroid {name} ({size}) aaj Earth se {distance_km:,.0f} km door se guzrega. Speed: {velocity:,.0f} km/h. {hazard_text}",
            "image": "https://www.nasa.gov/wp-content/uploads/2023/03/bennu-osiris-rex-1041.jpg",
            "source": "NASA NeoWs",
            "date": today
        }
    except Exception as e:
        print(f"      Asteroids error: {e}")
    return None


def fetch_nasa_epic() -> dict | None:
    """NASA EPIC — Earth Polychromatic Imaging Camera (beautiful Earth photos from space)"""
    try:
        resp = requests.get(
            "https://api.nasa.gov/EPIC/api/natural",
            params={"api_key": NASA_API_KEY or "DEMO_KEY"},
            timeout=10
        )
        images = resp.json()
        if not images:
            return None
        img_data = images[0]
        img_name = img_data.get("image", "")
        date_str = img_data.get("date", "")[:10].replace("-", "/")
        caption  = img_data.get("caption", "NASA EPIC camera se Earth ki photo")
        img_url  = (f"https://epic.gsfc.nasa.gov/archive/natural/"
                    f"{date_str}/png/{img_name}.png")
        return {
            "title": "NASA EPIC Camera Ne Space Se Earth Ki Tasveer Li",
            "body": f"{caption}. NASA ka EPIC camera DSCOVR satellite par hai jo Earth se 15 lakh km door L1 orbit mein hai. Ye Earth ki poori disc ki real photo hai.",
            "image": img_url,
            "source": "NASA EPIC",
            "date": img_data.get("date", "")[:10]
        }
    except Exception as e:
        print(f"      NASA EPIC error: {e}")
    return None


def fetch_wikimedia_space_image(keyword: str) -> str | None:
    """Wikimedia Commons se free high-quality space image"""
    try:
        resp = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query", "list": "search", "format": "json",
                "srsearch": f"{keyword} space high quality",
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


# --- YouTube Shorts Upload ---------------------------------------------------

def get_youtube_token() -> str | None:
    """OAuth refresh token se YouTube access token lo"""
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
        else:
            print(f"      YouTube token error: {resp.json().get('error_description', resp.text[:100])}")
        return token
    except Exception as e:
        print(f"      YouTube token error: {e}")
    return None


def upload_youtube_short(video_path: str, title: str, description: str) -> str | None:
    """YouTube Shorts pe upload karo via YouTube Data API v3 (resumable upload)"""
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
                    f"🚀 Atlantis Space — Space exploration in Hindi\n"
                    f"Subscribe for daily space Shorts!\n\n"
                    f"📅 {date_str}\n"
                    f"© Sources: NASA, JWST, ESA, ISRO (Public Domain / Free Use)\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"#Space #NASA #Astronomy #Shorts #AtlantisSpace #ISRO "
                    f"#SpaceShorts #Universe #ScienceShorts #SpaceExploration"
                ),
                "tags": [
                    "Space", "NASA", "Astronomy", "Shorts", "AtlantisSpace",
                    "ISRO", "ScienceShorts", "SpaceShorts", "SpaceExploration",
                    "Universe", "JWST", "Mars", "Rocket", "ISS", "HindiScience"
                ],
                "categoryId":         "28",   # Science & Technology
                "defaultLanguage":    "hi",
                "defaultAudioLanguage": "hi"
            },
            "status": {
                "privacyStatus":            "public",
                "selfDeclaredMadeForKids":  False,
                "madeForKids":              False,
                "containsSyntheticMedia":   True   # YouTube 2024 AI disclosure policy
            }
        }
        # Step 1: Init resumable upload session
        init_resp = requests.post(
            "https://www.googleapis.com/upload/youtube/v3/videos"
            "?uploadType=resumable&part=snippet,status",
            headers={
                "Authorization":          f"Bearer {token}",
                "Content-Type":           "application/json",
                "X-Upload-Content-Type":  "video/mp4",
                "X-Upload-Content-Length": str(video_size)
            },
            json=body,
            timeout=30
        )
        upload_url = init_resp.headers.get("Location")
        if not upload_url:
            print(f"      YouTube init error: {init_resp.text[:200]}")
            return None

        # Step 2: Upload video bytes
        size_mb = video_size // 1024 // 1024
        print(f"      YouTube upload ({size_mb}MB)...")
        with open(video_path, "rb") as f:
            up_resp = requests.put(
                upload_url,
                headers={"Content-Type": "video/mp4", "Content-Length": str(video_size)},
                data=f,
                timeout=300
            )
        video_id = up_resp.json().get("id")
        if video_id:
            print(f"      YouTube Short live: https://youtube.com/shorts/{video_id}")
            return video_id
        print(f"      YouTube error: {up_resp.text[:200]}")
    except Exception as e:
        print(f"      YouTube upload error: {e}")
    return None


# --- Additional Free Space Sources (JWST, Hubble, ESA, ISRO, RSS feeds) ------

def _parse_rss(url: str, source_name: str, max_results: int = 3) -> list[dict]:
    """Generic RSS/Atom feed parser — returns news items with image"""
    import xml.etree.ElementTree as ET
    import re as _re
    try:
        resp = requests.get(url, timeout=12, headers={"User-Agent": "AtlantisSpaceBot/1.0"})
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


def fetch_jwst_news() -> list[dict]:
    """James Webb Space Telescope — WebbTelescope.org JSON API (no key needed)"""
    import re as _re
    try:
        resp = requests.get(
            "https://webbtelescope.org/api/v1/news",
            params={"itemsPerPage": 5, "page": 1},
            timeout=12, headers={"User-Agent": "AtlantisSpaceBot/1.0"}
        )
        items = resp.json().get("news", [])
        news = []
        for a in items[:4]:
            title = a.get("name", "") or a.get("title", "")
            if not title:
                continue
            desc = _re.sub(r'<[^>]+>', '', a.get("abstract", "") or a.get("description", "") or "")[:500]
            img = ""
            ks = a.get("keystone") or {}
            for asset in ks.get("assets", []):
                if "image" in asset.get("fileType", "").lower():
                    img = asset.get("publicUrl", "")
                    break
            if not img:
                thumb = a.get("thumbnail") or {}
                img = thumb.get("publicUrl", "") if isinstance(thumb, dict) else ""
            news.append({
                "title": f"JWST: {clean_title(title)}",
                "body": desc,
                "image": img,
                "source": "James Webb Telescope",
                "date": str(a.get("publicationDate", ""))[:10]
            })
        result = [n for n in news if n["image"]][:3] or news[:2]
        print(f"      JWST news: {len(result)}")
        return result
    except Exception as e:
        print(f"      JWST error: {e}")
    return []


def fetch_hubble_news() -> list[dict]:
    """Hubble Space Telescope news — HubbleSite API (no key needed)"""
    import re as _re
    try:
        resp = requests.get(
            "https://hubblesite.org/api/v3/news",
            params={"page": 1, "per_page": 6},
            timeout=12, headers={"User-Agent": "AtlantisSpaceBot/1.0"}
        )
        data = resp.json()
        items = data.get("news", data) if isinstance(data, dict) else data
        news = []
        for a in (items[:4] if isinstance(items, list) else []):
            title = a.get("name", "") or a.get("title", "")
            if not title:
                continue
            desc = _re.sub(r'<[^>]+>', '', str(a.get("abstract", "") or ""))[:500]
            thumb = a.get("thumbnail_url", "") or a.get("thumbnail", "")
            img = str(thumb) if str(thumb).startswith("http") else (
                f"https://hubblesite.org{thumb}" if thumb else "")
            news.append({
                "title": f"Hubble: {clean_title(title)}",
                "body": desc,
                "image": img,
                "source": "Hubble Space Telescope",
                "date": str(a.get("publication_date", ""))[:10]
            })
        result = [n for n in news if n["image"]][:2] or news[:2]
        print(f"      Hubble news: {len(result)}")
        return result
    except Exception as e:
        print(f"      Hubble error: {e}")
    return []


def fetch_esa_news() -> list[dict]:
    """European Space Agency — Space Science RSS (free, no key)"""
    return _parse_rss(
        "https://www.esa.int/rssfeed/Our_Activities/Space_Science",
        "ESA", max_results=2
    )


def fetch_isro_news() -> list[dict]:
    """ISRO — Indian Space Research Organisation RSS (best for Indian audience)"""
    news = _parse_rss("https://www.isro.gov.in/rss.xml", "ISRO", max_results=3)
    for n in news:
        if not n.get("image"):
            n["image"] = fetch_nasa_image("isro india space rocket launch") or ""
    return [n for n in news if n.get("image")]


def fetch_nasa_jpl_news() -> list[dict]:
    """NASA JPL — Jet Propulsion Laboratory planetary science news"""
    return _parse_rss("https://www.jpl.nasa.gov/feeds/news", "NASA JPL", max_results=2)


def fetch_sky_telescope() -> list[dict]:
    """Sky & Telescope magazine — astronomy news RSS"""
    return _parse_rss("https://skyandtelescope.org/feed/", "Sky & Telescope", max_results=2)


def fetch_earthsky() -> list[dict]:
    """EarthSky — skywatching events, astronomy news RSS"""
    return _parse_rss("https://earthsky.org/feed/", "EarthSky", max_results=2)


def fetch_nasa_blogs() -> list[dict]:
    """NASA official blogs — missions, science, discoveries RSS"""
    import random
    blogs = [
        ("https://blogs.nasa.gov/webb/feed/",          "NASA Webb Blog"),
        ("https://blogs.nasa.gov/artemis/feed/",        "NASA Artemis Blog"),
        ("https://blogs.nasa.gov/spacestation/feed/",   "NASA Space Station Blog"),
        ("https://science.nasa.gov/feed/",              "NASA Science"),
    ]
    random.shuffle(blogs)
    for url, name in blogs[:2]:
        items = _parse_rss(url, name, max_results=2)
        if items:
            return items
    return []


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
        # Load existing history (titles + images both)
        existing = {}
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)

        titles = existing.get("titles", [])
        images = existing.get("images", [])

        # Save title
        normalized = title.lower().strip()[:120]
        if normalized not in titles:
            titles.append(normalized)
        titles = titles[-150:]

        # Save image URL fingerprint (first 120 chars = unique enough)
        if image_url:
            img_key = image_url.strip()[:120]
            if img_key not in images:
                images.append(img_key)
            images = images[-150:]

        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "titles": titles,
                "images": images,
                "updated": datetime.now().isoformat()
            }, f, ensure_ascii=False, indent=2)

        import subprocess
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        subprocess.run(["git", "add", "posted_history.json"], cwd=repo_dir)
        result = subprocess.run(
            ["git", "commit", "-m", "chore: update space posted history [skip ci]"],
            cwd=repo_dir, capture_output=True
        )
        if result.returncode == 0:
            subprocess.run(["git", "pull", "--rebase", "origin", "main"],
                           cwd=repo_dir, capture_output=True)
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
        if overlap >= 0.35:   # 35% — tighter than before (was 40%)
            return True
    return False


def is_image_duplicate(image_url: str, recent_images: set) -> bool:
    if not image_url:
        return False
    img_key = image_url.strip()[:120]
    return img_key in recent_images


# --- AI Planning --------------------------------------------------------------
def smart_plan(all_news: list[dict], count: int = CAROUSEL_SLIDES) -> list[dict]:
    print(f"\n[AI] {len(all_news)} space items analyze kar raha hoon...")
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
Ye space/astronomy content hai. Visual aur wow-factor score do (1-10):
- 9-10: Stunning visual (Mars photo, APOD, Earth from space, asteroid close pass)
- 7-8: Exciting event (rocket launch, ISS update, new discovery)
- 5-6: Informative but less visual (news article, conference)
- 1-4: Boring, no visual, unrelated

Priority: NASA APOD > Mars photos > ISS live > Asteroid > EPIC Earth > Launches > Articles

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
    print(f"\n[2/4] Caption generate kar raha hoon...")
    client = Groq(api_key=GROQ_API_KEY)

    prompt = f"""
Tu {CHANNEL_HANDLE} ka Instagram content creator hai — ye ek SPACE EXPLORATION channel hai, news channel nahi.

Content:
Title: {news_item.get('title', '')}
Description: {news_item.get('body', '')[:500]}
Source: {news_item.get('source', '')}

FACT ACCURACY: Sirf provided facts use karo — kuch invent mat karo.

TONE — SPACE EXPLORER, NOT NEWS REPORTER:
- "Breaking news" jaisi language BILKUL NAHI
- Wonder, curiosity, awe feel karwao — jaise koi space explorer baat kar raha ho
- "Dekho ye Mars ki photo!", "Socho zaraa — ye asteroid Earth ke itna paas se guzra!", "Ye wala moment history mein darj ho gaya!"
- Readers ko space se CONNECT karwao — unhe feel ho ki ye unki bhi duniya hai
- Hindi+English mix (Hinglish), young Indian audience ke liye
- 6-8 lines, educational but exciting — Carl Sagan wali curiosity
- End mein ek mind-blowing question ya fact
- YE PHOTO POST HAI — "video/reel/clip" mat likho
- CAPTION MEIN HASHTAG NAHI — sirf "hashtags" field mein

JSON:
{{
  "caption": "space explorer style caption, no hashtags",
  "hashtags": "#Space #ISRO #NASA #Astronomy #SpaceScience #Cosmos #Universe #IndiaInSpace #SpaceExploration #MarsExploration #BlackHole #SpaceTech #ScienceIsAwesome #AtlantisSpace #Stargazing #RocketLaunch #AstroPhotography #SpaceLovers #NASAIndia #FutureOfSpace (20 tags)",
  "image_keyword": "2-3 word English description for image search",
  "emoji_title": "emoji + short title",
  "headline": "5-8 word Hinglish headline — SIRF confirmed facts, spelling 100% correct",
  "image_summary": "2-3 Hinglish sentences (max 35 words) — confirmed facts only, spelling correct"
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
        caption = re.sub(r'\b(video|reel|clip)\b', 'photo', caption, flags=re.IGNORECASE)
        result["caption"] = caption

        # Spell-check headline + summary
        headline = result.get("headline", "")
        summary  = result.get("image_summary", "")
        if headline or summary:
            try:
                fix = client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    max_tokens=200,
                    messages=[{"role": "user", "content": f"""Fix spelling mistakes only. Do NOT change meaning or words.

Headline: {headline}
Summary: {summary}

JSON: {{"headline": "corrected", "summary": "corrected"}}"""}],
                    response_format={"type": "json_object"}
                )
                fixed = json.loads(fix.choices[0].message.content)
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
        print(f"      Caption error: {e}")
        return {
            "caption": news_item.get('title', 'Space Breaking News!'),
            "hashtags": "#Space #ISRO #NASA #Astronomy #SpaceScience",
            "image_keyword": "space astronomy",
            "emoji_title": "🚀 Space News",
            "headline": news_item.get('title', 'Space News')[:50],
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
        resp = requests.get(image_url, timeout=15,
                            headers={"User-Agent": "AtlantisSpaceBot/1.0"})
        if resp.status_code != 200:
            print(f"      Image download failed: {resp.status_code}")
            return None

        news_img = Image.open(io.BytesIO(resp.content)).convert("RGBA")

        # 1080x1080 square crop
        w, h = news_img.size
        side = min(w, h)
        news_img = news_img.crop(((w-side)//2, (h-side)//2, (w+side)//2, (h+side)//2))
        news_img = news_img.resize((1080, 1080), Image.LANCZOS)

        draw = ImageDraw.Draw(news_img)
        accent_color, bar_base = image_palette(news_img)

        # Gradient bar — bottom 38%
        bar_top = int(1080 * 0.62)
        overlay = Image.new("RGBA", (1080, 1080), (0, 0, 0, 0))
        ov_draw = ImageDraw.Draw(overlay)
        for i in range(1080 - bar_top):
            alpha = int(220 * (i / (1080 - bar_top)))
            ov_draw.line([(0, bar_top + i), (1080, bar_top + i)], fill=(*bar_base, alpha))
        news_img = Image.alpha_composite(news_img, overlay)
        draw = ImageDraw.Draw(news_img)

        # Top accent bar
        draw.rectangle([0, 0, 1080, 10], fill=(*accent_color, 255))

        font_title   = get_font(52)
        font_summary = get_font(32)
        font_source  = get_font(32)

        # Source + date line
        date_str  = datetime.now().strftime("%d %b %Y")
        src_color = tuple(min(255, int(c * 1.4 + 60)) for c in accent_color)
        src_label = f"{source}  •  " if source else ""
        draw.text((30, bar_top + 18), f"{src_label}{date_str}  •  {CHANNEL_HANDLE}",
                  font=font_source, fill=(*src_color, 255))

        # Headline
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

        # Summary
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

        # Logo
        if os.path.exists(LOGO_PATH):
            logo = Image.open(LOGO_PATH).convert("RGB")   # RGB — white background preserved
            logo_w = int(1080 * 0.10)
            logo_h = int(logo.height * (logo_w / logo.width))
            logo = logo.resize((logo_w, logo_h), Image.LANCZOS)
            pad = 4
            lx, ly = 1080 - logo_w - 20, 1080 - logo_h - 20
            draw.rectangle([lx-pad, ly-pad, lx+logo_w+pad, ly+logo_h+pad], fill=(255, 255, 255, 255))
            news_img.paste(logo, (lx, ly))

        final = news_img.convert("RGB")
        path = os.path.join(tempfile.gettempdir(), f"space_{int(time.time())}.jpg")
        final.save(path, "JPEG", quality=92)
        url = upload_image(path)
        try: os.remove(path)
        except: pass
        if not url:
            print(f"      ImgBB upload failed — skipping post")
        return url  # None if ImgBB failed — never use original blocked URLs

    except Exception as e:
        print(f"      Overlay error: {e}")
        return None


# --- Instagram Post -----------------------------------------------------------
def post_to_instagram(image_url: str, caption: str) -> str | None:
    print(f"\n[4/4] Instagram pe post kar raha hoon...")
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


# --- Reel Pipeline -----------------------------------------------------------

# NASA content topic → best search keyword mapping
NASA_VIDEO_KEYWORDS = {
    "nasa apod":              "nebula galaxy stars timelapse",
    "nasa perseverance":      "mars rover perseverance",
    "nasa curiosity":         "mars rover curiosity",
    "nasa epic":              "earth from space blue marble",
    "open-notify":            "international space station orbit",
    "nasa neows":             "asteroid solar system",
    "spacedevs events":       "spacewalk astronaut",
    "spacedevs":              "rocket launch",
    "spacex":                 "falcon 9 rocket launch",
    "nasa eonet":             "earth satellite view",
    "james webb telescope":   "james webb space telescope infrared deep field",
    "hubble space telescope": "hubble telescope galaxy nebula colorful",
    "esa":                    "european space agency spacecraft mission",
    "isro":                   "isro india rocket satellite launch chandrayaan",
    "nasa jpl":               "jet propulsion laboratory spacecraft deep space",
    "sky & telescope":        "galaxy nebula deep space timelapse",
    "planetary society":      "solar system planets exploration",
    "spaceweather":           "sun solar flare corona eruption",
    "earthsky":               "stars milky way night sky timelapse",
    "nasa webb blog":         "james webb telescope infrared universe",
    "nasa science":           "nasa science discovery universe",
    "nasa artemis blog":      "artemis moon rocket launch",
    "nasa space station blog":"international space station orbit earth",
    "arXiv astrophysics":     "astrophysics deep space universe discovery",
}


def fetch_nasa_video(keyword: str) -> str | None:
    """NASA Image & Video Library — actual space footage (public domain, free)"""
    try:
        resp = requests.get(
            "https://images-api.nasa.gov/search",
            params={"q": keyword, "media_type": "video", "page_size": 8},
            timeout=12
        )
        items = resp.json().get("collection", {}).get("items", [])
        for item in items:
            nasa_id = item.get("data", [{}])[0].get("nasa_id", "")
            if not nasa_id:
                continue
            try:
                asset_resp = requests.get(
                    f"https://images-api.nasa.gov/asset/{nasa_id}",
                    timeout=10
                )
                assets = asset_resp.json().get("collection", {}).get("items", [])
                # Prefer mobile/small MP4 — fast download, enough quality
                priority = ["~mobile.mp4", "~small.mp4", "~medium.mp4", ".mp4"]
                mp4_urls = [a["href"] for a in assets if a.get("href", "").endswith(".mp4")]
                chosen = None
                for suffix in priority:
                    for url in mp4_urls:
                        if suffix in url:
                            chosen = url
                            break
                    if chosen:
                        break
                if not chosen and mp4_urls:
                    chosen = mp4_urls[0]
                if not chosen:
                    continue

                print(f"      NASA video found: {chosen[-50:]}")
                r = requests.get(chosen, timeout=120, stream=True)
                if r.status_code != 200:
                    continue
                path = os.path.join(tempfile.gettempdir(), f"nasa_vid_{int(time.time())}.mp4")
                size = 0
                with open(path, "wb") as f:
                    for chunk in r.iter_content(65536):
                        f.write(chunk)
                        size += len(chunk)
                        if size > 150 * 1024 * 1024:  # 150MB cap
                            break
                size_mb = os.path.getsize(path) // 1024 // 1024
                if size_mb > 0:
                    print(f"      NASA video downloaded: {size_mb}MB")
                    return path
            except Exception:
                continue
    except Exception as e:
        print(f"      NASA video error: {e}")
    return None


def fetch_pexels_video(keyword: str) -> str | None:
    """Pexels se space stock video — fallback when NASA video unavailable"""
    if not PEXELS_API_KEY:
        return None
    try:
        headers = {"Authorization": PEXELS_API_KEY}
        resp = requests.get(
            f"https://api.pexels.com/videos/search?query={keyword} space&per_page=8&orientation=portrait",
            headers=headers, timeout=10
        )
        videos = resp.json().get("videos", [])
        for video in videos:
            for vf in video.get("video_files", []):
                if vf.get("file_type") == "video/mp4" and vf.get("height", 0) >= 720:
                    url = vf["link"]
                    print(f"      Pexels video (fallback): {url[:60]}")
                    r = requests.get(url, timeout=90, stream=True)
                    path = os.path.join(tempfile.gettempdir(), f"space_vid_{int(time.time())}.mp4")
                    with open(path, "wb") as f:
                        for chunk in r.iter_content(8192):
                            f.write(chunk)
                    size_mb = os.path.getsize(path) // 1024 // 1024
                    print(f"      Downloaded: {size_mb}MB")
                    return path
    except Exception as e:
        print(f"      Pexels video error: {e}")
    return None


def fetch_jwst_video() -> str | None:
    """James Webb Space Telescope videos — WebbTelescope.org media library (free)"""
    try:
        resp = requests.get(
            "https://webbtelescope.org/api/v1/media",
            params={"itemsPerPage": 8, "filterTypes": "Video"},
            timeout=12, headers={"User-Agent": "AtlantisSpaceBot/1.0"}
        )
        items = resp.json().get("media", [])
        for item in items:
            for asset in item.get("assets", []):
                ft = asset.get("fileType", "").lower()
                if "mp4" in ft or ft == "video/mp4":
                    url = asset.get("publicUrl", "")
                    if not url:
                        continue
                    print(f"      JWST video: {url[-50:]}")
                    r = requests.get(url, timeout=120, stream=True)
                    if r.status_code != 200:
                        continue
                    path = os.path.join(tempfile.gettempdir(), f"jwst_{int(time.time())}.mp4")
                    size = 0
                    with open(path, "wb") as f:
                        for chunk in r.iter_content(65536):
                            f.write(chunk)
                            size += len(chunk)
                            if size > 150 * 1024 * 1024:
                                break
                    if os.path.getsize(path) > 0:
                        size_mb = os.path.getsize(path) // 1024 // 1024
                        print(f"      JWST video downloaded: {size_mb}MB")
                        return path
    except Exception as e:
        print(f"      JWST video error: {e}")
    return None


def fetch_space_video(keyword: str, source: str = "") -> str | None:
    """NASA/JWST footage first (public domain), Pexels as fallback"""
    # Map source to best NASA search keyword
    source_lower = source.lower()
    nasa_keyword = keyword
    for key, val in NASA_VIDEO_KEYWORDS.items():
        if key in source_lower:
            nasa_keyword = val
            break

    # JWST content: try WebbTelescope video library first (stunning footage)
    if any(x in source_lower for x in ["webb", "jwst", "james webb"]):
        path = fetch_jwst_video()
        if path:
            return path

    print(f"      Searching NASA footage: '{nasa_keyword}'")
    path = fetch_nasa_video(nasa_keyword)
    if path:
        return path

    # Fallback: try original keyword on NASA
    if nasa_keyword != keyword:
        path = fetch_nasa_video(keyword)
        if path:
            return path

    # Final fallback: Pexels
    print(f"      NASA footage not found — trying Pexels")
    return fetch_pexels_video(keyword)


REALTIME_SOURCES = {
    "NASA NeoWs", "Open-Notify / NASA", "SpaceDevs Events",
    "SpaceDevs", "SpaceX", "NASA EONET", "SpaceDevs / NASA",
    "ISRO", "SpaceWeather", "NASA Space Station Blog"
}


def generate_narration(news_item: dict, headline: str, summary: str) -> str:
    """Groq se 30-second Reel narration — real-time vs educational, headline nahi padega"""
    source  = news_item.get("source", "")
    title   = news_item.get("title", "")
    body    = news_item.get("body", "")[:500]
    is_rt   = any(s in source for s in REALTIME_SOURCES)

    if is_rt:
        style = (
            "YE REAL-TIME EVENT HAI — abhi ho raha hai ya aaj hoga.\n"
            "Tone: 'Abhi is waqt...', 'Aaj...', 'Is exact moment mein...'\n"
            "Urgency + excitement — viewers ko feel ho ki ye ABHI ho raha hai.\n"
            "Exact numbers, distances, speeds, dates zaroor batao."
        )
    else:
        style = (
            "YE EDUCATIONAL/DISCOVERY content hai — photo ya historical finding.\n"
            "Tone: 'Dekho zaraa...', 'Socho agar tum wahan hote...', 'Ye jo tum dekh rahe ho...'\n"
            "Wonder aur curiosity — viewer ko space se deeply connect karo.\n"
            "Hidden facts, scale, comparisons batao jo mind blow kar de."
        )

    try:
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=400,
            messages=[{"role": "user", "content": f"""
Tu @atlantis_space Instagram Reel ka narrator hai.
Ek 30-second Hindi narration script likho jo poori Reel mein chale.

Topic: {title}
Details: {body}
Summary: {summary}

{style}

STRICT RULES:
- HEADLINE BILKUL MAT PADHO — woh screen pe already dikh raha hai
- ~90-100 words likhoo — 30 second ke liye enough hoga
- Actual facts, numbers, context do jo screen pe nahi dikh raha
- Hinglish (Hindi + English mix), conversational documentary style
- FORBIDDEN words/phrases: "yaar", "sun", "yaar sun", "bhai", "dosto"
- Vary the opening — NEVER repeat same opener. Options: "Socho zaraa...", "Ye dekhna zaroori hai...", "Is pal ko samjho...", "Ek baat batao...", "Space ki ye sachai..."
- "..." use karo dramatic pause ke liye — TTS naturally pause karta hai
- Chhoti punchy sentences = strong impact. Example: "Distance? 40 lakh kilometer. Speed? 28,000 km per ghante."
- Sirf bolne wala text — koi heading, bullet, asterisk nahi

Script:"""}]
        )
        narration = resp.choices[0].message.content.strip()
        # Clean up any formatting
        import re
        narration = re.sub(r'\*+', '', narration).strip()
        wc = len(narration.split())
        print(f"      Narration ({wc} words, {'realtime' if is_rt else 'educational'})")
        return narration
    except Exception as e:
        print(f"      Narration error: {e} — using summary")
        return f"{summary}"


def generate_tts(text: str, out_path: str) -> bool:
    """Edge TTS — improved Hindi neural voice with audio normalization."""
    import re as _re
    import subprocess as _sp

    # Clean text — normalize ellipsis for natural TTS pauses
    clean = _re.sub(r'\.{2,}', '... ', text)
    clean = _re.sub(r'\s+', ' ', clean).strip()

    # Voice: hi-IN-SwaraNeural female — same as before, slightly improved
    voices = [
        # (voice_name, rate, pitch, volume)
        ("hi-IN-SwaraNeural", "+5%", "+0Hz", "+15%"),   # original speed, clean pitch, louder
    ]

    try:
        import asyncio
        import edge_tts

        for voice, rate, pitch, volume in voices:
            try:
                async def _speak(v=voice, r=rate, p=pitch, vol=volume):
                    comm = edge_tts.Communicate(clean, voice=v,
                                                rate=r, pitch=p, volume=vol)
                    await comm.save(out_path)

                asyncio.run(_speak())
                if not (os.path.exists(out_path) and os.path.getsize(out_path) > 1000):
                    continue

                # Audio normalization — consistent loudness, cleaner sound
                norm_path = out_path.replace(".mp3", "_norm.mp3")
                norm = _sp.run([
                    "ffmpeg", "-y", "-i", out_path,
                    "-af", "loudnorm=I=-14:TP=-1.5:LRA=7,highpass=f=80",
                    norm_path
                ], capture_output=True, timeout=30)
                if norm.returncode == 0 and os.path.exists(norm_path):
                    os.replace(norm_path, out_path)

                print(f"      Edge TTS ({voice}) ready")
                return True
            except Exception as ve:
                print(f"      {voice} error: {ve}")
                continue

    except Exception as e:
        print(f"      Edge TTS error: {e}")

    # Final fallback: gTTS
    try:
        from gtts import gTTS
        gTTS(text=clean, lang="hi", slow=False).save(out_path)
        print(f"      gTTS fallback ready")
        return os.path.exists(out_path) and os.path.getsize(out_path) > 0
    except Exception as e2:
        print(f"      gTTS error: {e2}")
        return False


def process_reel(video_path: str, headline: str, summary: str, narration: str = "", source: str = "") -> str | None:
    """Space video ko Reel format mein convert karo — text overlay + TTS audio"""
    import subprocess
    try:
        ts          = int(time.time())
        tmp         = tempfile.gettempdir()
        base_path   = os.path.join(tmp, f"rbase_{ts}.mp4")
        overlay_png = os.path.join(tmp, f"rovl_{ts}.png")
        audio_path  = os.path.join(tmp, f"rtts_{ts}.mp3")
        out_path    = os.path.join(tmp, f"reel_{ts}.mp4")

        # Step 1: Convert to 1080x1920 (9:16) — full-screen fill
        # Scale to COVER entire frame then center-crop — fills screen completely
        # Best for YouTube Shorts & Instagram Reels (no blurred bars)
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
            # Fallback: blur background for unusual aspect ratios
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

        # Step 2: Full-frame overlay PNG (1080x1920) — logo top + text bar bottom
        # This gives complete control over positioning
        FRAME_W  = 1080
        FRAME_H  = 1920
        BAR_H    = 460    # bottom text bar height
        PAD_LEFT = 40
        PAD_RIGHT= 150    # right margin for Instagram action buttons
        MAX_W    = FRAME_W - PAD_LEFT - PAD_RIGHT   # 890px safe text width
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

        # ── LOGO — top-left, big, prominent ──────────────────────────────
        if os.path.exists(LOGO_PATH):
            try:
                logo_img = Image.open(LOGO_PATH).convert("RGB")   # RGB — white background preserved
                logo_w = 160
                logo_h = int(logo_img.height * (logo_w / logo_img.width))
                logo_img = logo_img.resize((logo_w, logo_h), Image.LANCZOS)
                lx, ly = 40, 60
                pad = 10
                # Fully opaque white rectangle — no semi-transparency mixing with dark video
                ov_draw.rounded_rectangle(
                    [lx - pad, ly - pad, lx + logo_w + pad, ly + logo_h + pad],
                    radius=12, fill=(255, 255, 255, 255)
                )
                # Paste logo directly — its own white background shows as solid white
                overlay.paste(logo_img, (lx, ly))
            except Exception as le:
                print(f"      Logo error: {le}")

        # ── BOTTOM TEXT BAR ───────────────────────────────────────────────
        bar_y = FRAME_H - BAR_H
        # Gradient: transparent → dark
        for i in range(BAR_H):
            alpha = int(170 * (i / BAR_H) + 60)
            ov_draw.line([(0, bar_y + i), (FRAME_W, bar_y + i)],
                         fill=(0, 0, 15, min(alpha, 245)))
        # Accent line at top of bar
        ov_draw.rectangle([0, bar_y, FRAME_W, bar_y + 6], fill=(80, 180, 255, 255))

        # Headline
        y = bar_y + 24
        for line in wrap_px(headline, font_head, MAX_W, ov_draw)[:2]:
            ov_draw.text((PAD_LEFT, y), line, font=font_head, fill=(255, 255, 255, 255))
            y += 66

        # Summary
        y += 10
        for line in wrap_px(summary, font_body, MAX_W, ov_draw)[:3]:
            ov_draw.text((PAD_LEFT, y), line, font=font_body, fill=(200, 225, 255, 240))
            y += 44

        # Footer — left: channel + date | right: source credit (small)
        date_str = datetime.now().strftime("%d %b %Y")
        ov_draw.text((PAD_LEFT, FRAME_H - 44),
                     f"{CHANNEL_HANDLE}  •  {date_str}",
                     font=font_foot, fill=(130, 170, 220, 210))
        if source:
            font_src  = get_font(22)
            src_text  = f"© {source}"
            src_w     = ov_draw.textlength(src_text, font=font_src)
            ov_draw.text((FRAME_W - src_w - PAD_RIGHT - 10, FRAME_H - 40),
                         src_text, font=font_src, fill=(160, 190, 220, 180))

        overlay.save(overlay_png, "PNG")

        # Step 3: TTS — use full narration (covers entire 30s), not just headline
        tts_text = narration if narration else summary
        has_audio = generate_tts(tts_text, audio_path)

        # Step 4: FFmpeg combine — 1080x1920, Instagram-compatible encoding
        common = [
            "-c:v", "libx264", "-profile:v", "high", "-level:v", "4.0",
            "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "23",
            "-movflags", "+faststart"   # streaming-friendly
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


def upload_video_github(video_path: str) -> str | None:
    """Reel video GitHub Release pe upload karo — public URL milegi"""
    gh_token = (os.getenv("GH_PAT") or os.getenv("GITHUB_TOKEN") or "").strip()
    repo = os.getenv("GITHUB_REPOSITORY")
    if not gh_token or not repo:
        return None
    headers = {
        "Authorization": f"token {gh_token}",
        "Accept": "application/vnd.github.v3+json"
    }
    filename = f"space_reel_{int(time.time())}.mp4"
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
                      "draft": False, "body": "Auto-generated space reels"},
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
    """Instagram Reels API se post karo"""
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
        # Wait for processing (max 90s)
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


def auto_first_comment(media_id: str, hashtags: str) -> None:
    if not INSTAGRAM_TOKEN or media_id in ("dry_run",) or not hashtags \
            or (media_id or "").startswith("yt_"):
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
                print(f"      Hashtag comment posted!")
                return
            print(f"      Comment attempt {attempt+1} error: {data}")
            if attempt < 2:
                time.sleep(6)
        except Exception as e:
            print(f"      Comment attempt {attempt+1} exception: {e}")
            if attempt < 2:
                time.sleep(6)


# --- Main Agent ---------------------------------------------------------------
def run_agent():
    print("=" * 55)
    print(f"  🚀 Atlantis Space Agent Starting...")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 55)

    all_news = []

    # Source 1: NASA APOD — stunning daily astronomy photo (highest priority)
    apod = fetch_nasa_apod()
    if apod:
        all_news.insert(0, apod)

    # Source 2: Mars Rover latest photos — actual Mars surface
    mars = fetch_mars_photos()
    if mars:
        all_news.append(mars)

    # Source 3: NASA EPIC — Earth from space (beautiful full-disc Earth)
    epic = fetch_nasa_epic()
    if epic:
        all_news.append(epic)

    # Source 4: ISS live update — astronauts in space right now
    iss = fetch_iss_update()
    if iss:
        all_news.append(iss)

    # Source 5: Asteroids passing Earth today
    asteroids = fetch_nasa_asteroids()
    if asteroids:
        all_news.append(asteroids)

    # Source 6: SpaceDevs Events — spacewalks, landings, crewed milestones
    events = fetch_spacedevs_events()
    all_news.extend(events)

    # Source 7: Current ISS expedition
    expedition = fetch_spacedevs_expeditions()
    if expedition:
        all_news.append(expedition)

    # Source 8: Recent spacecraft docking
    docking = fetch_spacedevs_dockings()
    if docking:
        all_news.append(docking)

    # Source 9: Featured astronaut (Indian/Asian priority)
    astronaut = fetch_spacedevs_astronauts()
    if astronaut:
        all_news.append(astronaut)

    # Source 10: SpaceX upcoming launches
    spacex = fetch_spacex_launches()
    all_news.extend(spacex)

    # Source 11: SpaceDevs — other agency launches
    launches = fetch_spacedevs_launches()
    all_news.extend(launches)

    # Source 12: Spaceflight News API — space articles with images
    sf_news = fetch_spaceflight_news(max_results=5)
    all_news.extend(sf_news)

    # Source 13: NASA EONET — Earth/space weather events
    eonet = fetch_nasa_eonet()
    all_news.extend(eonet)

    # Source 15: James Webb Space Telescope — cutting-edge deep universe imagery
    jwst = fetch_jwst_news()
    all_news.extend(jwst)

    # Source 16: Hubble Space Telescope — classic iconic space photos
    hubble = fetch_hubble_news()
    all_news.extend(hubble)

    # Source 17: ESA — European Space Agency (Rosetta, Gaia, Solar Orbiter etc.)
    esa = fetch_esa_news()
    all_news.extend(esa)

    # Source 18: ISRO — Indian missions (Chandrayaan, Gaganyaan, Aditya-L1)
    isro = fetch_isro_news()
    all_news.extend(isro)

    # Source 19: NASA JPL — planetary science, rover missions, deep space probes
    jpl = fetch_nasa_jpl_news()
    all_news.extend(jpl)

    # Source 20: Sky & Telescope — telescope-worthy astronomy events
    sky_tel = fetch_sky_telescope()
    all_news.extend(sky_tel)

    # Source 21: EarthSky — skywatching events, eclipses, meteor showers
    earthsky = fetch_earthsky()
    all_news.extend(earthsky)

    # Source 22: NASA Blogs (rotating: Webb, Artemis, Space Station, Science)
    blogs = fetch_nasa_blogs()
    all_news.extend(blogs)

    # Source 23: DuckDuckGo fallback (ISRO + regional space news) — last resort
    if len(all_news) < 4:
        for topic in SPACE_DISCOVERY_TOPICS[:2]:
            results = fetch_news(topic, max_results=2)
            all_news.extend(results)

    all_news = [n for n in all_news if n.get("image")]
    print(f"      Image wali news: {len(all_news)}")

    if not all_news:
        print("Koi image wali space news nahi mili.")
        return

    all_news_raw = all_news.copy()  # backup before duplicate filter

    recent_titles = get_recently_posted_titles()
    recent_images = load_posted_images()
    all_news = [
        n for n in all_news
        if not is_duplicate(n.get("title", ""), recent_titles)
        and not is_image_duplicate(n.get("image", ""), recent_images)
    ]
    print(f"      Duplicate hataane ke baad: {len(all_news)}")

    if not all_news:
        # Fallback: force post from priority NASA sources
        print("      Sab duplicate — NASA priority sources se force post...")
        priority = {"NASA APOD", "NASA Perseverance", "NASA Curiosity",
                    "NASA EPIC", "SpaceDevs Events", "NASA NeoWs"}
        all_news = [n for n in all_news_raw if n.get("source", "") in priority]
        if not all_news:
            all_news = all_news_raw[:CAROUSEL_SLIDES]
        print(f"      Force mode: {len(all_news)} items")

    news_list = smart_plan(all_news, count=CAROUSEL_SLIDES)
    posted = 0

    for i, news in enumerate(news_list):
        print(f"\n{'-'*50}")
        print(f"News: {news.get('title', '')[:70]}...")

        content = generate_caption(news)
        headline = content.get("headline") or news.get("title", "")
        summary  = content.get("image_summary", "")
        hashtags = content.get("hashtags", "#Space #ISRO #NASA #Astronomy")
        caption  = content.get("caption", "")

        media_id = None

        # Reel sources: APOD, Mars, EPIC, ISS, Asteroid, events get Reel attempt
        visual_sources = {"NASA APOD", "NASA Perseverance", "NASA Curiosity",
                          "NASA EPIC", "Open-Notify / NASA", "NASA NeoWs",
                          "SpaceDevs Events", "James Webb Telescope",
                          "Hubble Space Telescope", "ESA", "ISRO",
                          "NASA JPL", "NASA Webb Blog", "NASA Artemis Blog"}
        is_visual = (news.get("source", "") in visual_sources or
                     news.get("_nasa") or i == 0)

        if is_visual:
            print(f"      [Reel mode] Space video dhund raha hoon...")
            keyword    = content.get("image_keyword", "space astronomy")
            narration  = generate_narration(news, headline, summary)
            video_path = fetch_space_video(keyword, source=news.get("source", ""))
            if video_path:
                reel_path = process_reel(video_path, headline, summary, narration,
                                         source=news.get("source", ""))
                try: os.remove(video_path)
                except: pass
                if reel_path:
                    # YouTube Shorts — upload from local file BEFORE deleting
                    yt_id = None
                    if YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET and YOUTUBE_REFRESH_TOKEN:
                        yt_id = upload_youtube_short(reel_path, headline, caption)

                    # Instagram Reel — upload to GitHub Release for public URL
                    if not YOUTUBE_ONLY:
                        video_url = upload_video_github(reel_path)
                    try: os.remove(reel_path)
                    except: pass

                    if not YOUTUBE_ONLY and video_url:
                        media_id = post_reel(video_url, caption)
                    elif yt_id:
                        media_id = f"yt_{yt_id}"   # mark success for history tracking

            if not media_id:
                if YOUTUBE_ONLY:
                    print("      Reel fail — skipping (YouTube-only mode, no photo fallback)")
                else:
                    print("      Reel fail — photo post pe fallback")

        # Photo post (Reel fail ya remaining news) — skip in YouTube-only mode
        if not media_id and not YOUTUBE_ONLY:
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
    print(f"  Agent complete! {posted}/{CAROUSEL_SLIDES} posts kiye. (23 sources, 5 runs/day = ~10 posts/day)")
    print("=" * 55)


if __name__ == "__main__":
    run_agent()
