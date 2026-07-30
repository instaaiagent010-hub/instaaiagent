"""
Atlantis — Agentic Learning Layer

Ye agent ko "agentic" banata hai: PERCEIVE → LEARN → DECIDE.
  1. PERCEIVE : har post ki "recipe" (topic, hook, voice, hour) yaad rakho,
                phir Instagram Insights se uska performance (reach/likes/saves) padho.
  2. LEARN    : data se pattern nikalo — kaunsa hook/topic/hour sabse zyada chala.
  3. DECIDE   : agla content banate waqt wahi seekh use karo (epsilon-greedy —
                zyadatar best, kabhi-kabhi naya try, taaki explore bhi hota rahe).

Data GitHub Contents API se `agentic/` folder mein persist hota hai (cloud runs
ephemeral hote hain, isliye repo mein store). Token/data na ho to sab gracefully
degrade — agent purane tarike se chalta rahega.
"""

import os, json, base64, time, random
import requests

_API = "https://api.github.com"
REC_PATH  = "agentic/recipes.json"
LEARN_PATH = "agentic/learnings.json"
EXPLORE_RATE = 0.25   # 25% time naya try karo (exploration)


def _gh():
    tok  = (os.getenv("GH_PAT") or os.getenv("GITHUB_TOKEN") or "").strip()
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    if not tok or not repo:
        return None, None, None
    return tok, repo, {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}


def _read_json(path, default):
    tok, repo, hdr = _gh()
    if not tok:
        return default, None
    try:
        r = requests.get(f"{_API}/repos/{repo}/contents/{path}", headers=hdr, timeout=30)
        if r.status_code == 200:
            meta = r.json()
            data = json.loads(base64.b64decode(meta["content"]).decode())
            return data, meta["sha"]
    except Exception as e:
        print(f"      [agentic] read {path}: {e}")
    return default, None


def _write_json(path, data, sha, msg):
    tok, repo, hdr = _gh()
    if not tok:
        return False
    body = {"message": msg,
            "content": base64.b64encode(json.dumps(data, ensure_ascii=False, indent=2).encode()).decode()}
    if sha:
        body["sha"] = sha
    try:
        r = requests.put(f"{_API}/repos/{repo}/contents/{path}", headers=hdr, json=body, timeout=60)
        return r.status_code in (200, 201)
    except Exception as e:
        print(f"      [agentic] write {path}: {e}")
        return False


# ─── 1. PERCEIVE — recipe record karo ────────────────────────────────────────
def record_recipe(media_id: str, topic: str = "", hook_idx: int = -1,
                  voice: str = "", media_type: str = "reel") -> None:
    """Post ke baad: kya banaya (topic, hook, voice, kis ghante) — yaad rakho."""
    if not media_id or media_id == "dry_run":
        return
    recipes, sha = _read_json(REC_PATH, [])
    recipes.append({
        "media_id":   media_id,
        "topic":      (topic or "")[:120],
        "hook_idx":   hook_idx,
        "voice":      voice,
        "type":       media_type,
        "hour":       time.localtime().tm_hour,
        "posted_at":  int(time.time()),
        "scored":     False,
    })
    recipes = recipes[-300:]
    _write_json(REC_PATH, recipes, sha, "agentic: recipe record")


# ─── 2. LEARN — performance padho, pattern nikalo ────────────────────────────
def _engagement(likes, comments, saved, shares, reach):
    # saves + shares sabse strong signal (log actually value karta hai)
    score = likes + 2 * comments + 4 * saved + 3 * shares
    return score / max(reach, 1) if reach else score


def learn_from_performance() -> dict:
    """Recorded posts ka Instagram Insights padho → kaunsa hook/topic/hour best chala."""
    token   = os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip()
    acct    = os.getenv("INSTAGRAM_ACCOUNT_ID", "").strip()
    if not token or not acct:
        print("      [agentic] IG token/account nahi — learn skip")
        return {}

    recipes, rsha = _read_json(REC_PATH, [])
    if not recipes:
        print("      [agentic] koi recipe nahi — learn skip")
        return {}

    now = int(time.time())
    hook_scores, hour_scores, topic_scores, voice_scores = {}, {}, {}, {}
    updated = 0

    for rec in recipes:
        # sirf 6h+ purani posts score karo (fresh posts ka data adhoora hota hai)
        if now - rec.get("posted_at", now) < 6 * 3600:
            continue
        mid = rec.get("media_id")
        if not mid:
            continue
        try:
            r = requests.get(f"https://graph.facebook.com/v25.0/{mid}",
                             params={"fields": "like_count,comments_count", "access_token": token},
                             timeout=15).json()
            likes = r.get("like_count", 0) or 0
            comments = r.get("comments_count", 0) or 0
            reach = saved = shares = 0
            ins = requests.get(f"https://graph.facebook.com/v25.0/{mid}/insights",
                               params={"metric": "reach,saved,shares", "access_token": token},
                               timeout=15).json()
            for item in ins.get("data", []):
                val = (item.get("values", [{}])[0] or {}).get("value", 0) or 0
                if item["name"] == "reach":  reach = val
                if item["name"] == "saved":  saved = val
                if item["name"] == "shares": shares = val
        except Exception:
            continue

        eng = _engagement(likes, comments, saved, shares, reach)
        updated += 1

        def _acc(d, key):
            if key in (None, "", -1):
                return
            k = str(key)
            s = d.setdefault(k, {"sum": 0.0, "n": 0})
            s["sum"] += eng; s["n"] += 1

        _acc(hook_scores, rec.get("hook_idx"))
        _acc(hour_scores, rec.get("hour"))
        _acc(voice_scores, rec.get("voice"))
        for w in set((rec.get("topic", "").lower().split())):
            if len(w) > 3:
                _acc(topic_scores, w)

    def _avg(d):
        return {k: round(v["sum"] / v["n"], 3) for k, v in d.items() if v["n"] > 0}

    learnings = {
        "updated":      now,
        "posts_scored": updated,
        "hook_avg":     _avg(hook_scores),
        "hour_avg":     _avg(hour_scores),
        "voice_avg":    _avg(voice_scores),
        "topic_avg":    dict(sorted(_avg(topic_scores).items(),
                                    key=lambda x: x[1], reverse=True)[:20]),
    }
    _, lsha = _read_json(LEARN_PATH, {})
    _write_json(LEARN_PATH, learnings, lsha, f"agentic: learn ({updated} posts)")
    print(f"      [agentic] learned from {updated} posts | "
          f"best hook: {best_key(learnings.get('hook_avg', {}))}")
    return learnings


# ─── 3. DECIDE — seekh use karo ──────────────────────────────────────────────
_LEARN_CACHE = None


def get_learnings() -> dict:
    global _LEARN_CACHE
    if _LEARN_CACHE is None:
        _LEARN_CACHE, _ = _read_json(LEARN_PATH, {})
    return _LEARN_CACHE or {}


def best_key(avg: dict):
    return max(avg, key=avg.get) if avg else None


def choose_hook(n_hooks: int, default_idx: int) -> int:
    """Epsilon-greedy: zyadatar best-performing hook, kabhi-kabhi naya try (explore)."""
    avg = get_learnings().get("hook_avg", {})
    if not avg or random.random() < EXPLORE_RATE:
        return default_idx        # explore — default (time rotation) use karo
    try:
        best = int(best_key(avg))
        if 0 <= best < n_hooks:
            print(f"      [agentic] best hook #{best+1} use kar raha hoon (learned)")
            return best
    except Exception:
        pass
    return default_idx


def rank_topics(topics: list) -> list:
    """Topics ko learned performance se sort karo — best pehle."""
    avg = get_learnings().get("topic_avg", {})
    if not avg:
        return topics
    def score(t):
        return sum(avg.get(w.lower(), 0) for w in t.split() if len(w) > 3)
    ranked = sorted(topics, key=score, reverse=True)
    if ranked != topics:
        print(f"      [agentic] topics learned-order mein: '{ranked[0][:30]}' pehle")
    return ranked


if __name__ == "__main__":
    learn_from_performance()
