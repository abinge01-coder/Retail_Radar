#!/usr/bin/env python3
"""
Hearthstone Battlegrounds Meta Scanner
Aggregates daily meta insights from Reddit, HSReplay, and Blizzard news.
Optionally uses Claude API to synthesize community data into meta analysis.

Environment variables (all optional, set as GitHub Secrets):
  ANTHROPIC_API_KEY   — enables AI meta insights via Claude
  REDDIT_CLIENT_ID    — Reddit OAuth app client ID (increases rate limits)
  REDDIT_CLIENT_SECRET — Reddit OAuth app client secret
"""

import base64
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

DATA_FILE = Path("data/hs_meta.json")
REQUEST_DELAY = 1.5

# Reddit's required User-Agent format: <platform>:<app_id>:<version> (by /u/<username>)
REDDIT_UA = "linux:hs-bg-meta-radar:v1.0 (by /u/HSBGMetaBot)"
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

BG_KEYWORDS = [
    "quilboar", "beast", "mech", "naga", "undead", "elemental",
    "murloc", "pirate", "dragon", "demon",
    "tavern tier", "triple", "discover", "buddy", "hero power",
    "tier list", "op", "nerf", "buff", "patch", "balance",
    "leapfrogger", "sellemental", "brann", "nozdormu", "yogg",
    "millhouse", "sindragosa", "george", "patchwerk", "maiev",
    "finley", "alexstrasza", "ragnaros", "jandice", "reno",
    "tavish", "lich king", "ysera", "shudderwock",
    "gold", "freeze", "scouting party", "armor", "tavern spell",
]

HERO_NAMES = [
    "A.F. Kay", "Alexstrasza", "Ambassador Faelin", "Ana Warsong",
    "Aranna Starseeker", "Arch-Villain Rafaam", "Bartendotron",
    "Brann Bronzebeard", "Captain Eudora", "Cookie the Cook",
    "C'Thun", "Dancin' Deryl", "Death Speaker Blackthorn",
    "Deathwing", "Dinotamer Brann", "Drakonid Operative",
    "Edwin VanCleef", "Finley", "Forest Warden Omu",
    "George the Fallen", "Greybough", "Guff Runetotem",
    "Inge the Iron Hymn", "Ini Stormcoil", "Jandice Barov",
    "Kael'thas Sunstrider", "Kurtrus Ashfallen", "Lady Vashj",
    "Lexy", "Lich King", "Lord Jaraxxus", "Maiev Shadowsong",
    "Malygos", "Millhouse Manastorm", "Murozond", "N'Zoth",
    "Nefarian", "Nozdormu", "Overlord Saurfang", "Patchwerk",
    "Queen Wagtoggle", "Ragnaros", "Reno Jackson", "Rokara",
    "Scabbs Cutterbutter", "Shudderwock", "Silas Darkmoon",
    "Sindragosa", "Sir Finley Mrrgglton", "Skycap'n Kragg",
    "Steve", "Tavio", "The Great Akazamzarak", "The Lich King",
    "Tickatus", "Togwaggle", "Trade Prince Gallywix",
    "Vol'jin", "Waxrider Togwaggle", "Whispers of EVIL",
    "Xyrella", "Ysera", "Yogg-Saron",
]


def fetch_url(url: str, headers: dict = None, data: bytes = None, timeout: int = 30) -> str | None:
    req_headers = {"User-Agent": BROWSER_UA}
    if headers:
        req_headers.update(headers)
    req = Request(url, data=data, headers=req_headers)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        print(f"  HTTP {e.code}: {url}")
    except (URLError, TimeoutError) as e:
        print(f"  Network error ({url}): {e}")
    return None


# ─── Reddit OAuth ─────────────────────────────────────────────────────────────

def get_reddit_token() -> str | None:
    """Obtain a Reddit OAuth bearer token using app credentials."""
    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    if not (client_id and client_secret):
        return None

    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    data = urlencode({"grant_type": "client_credentials"}).encode()
    content = fetch_url(
        "https://www.reddit.com/api/v1/access_token",
        headers={
            "Authorization": f"Basic {creds}",
            "User-Agent": REDDIT_UA,
        },
        data=data,
    )
    if not content:
        return None
    try:
        return json.loads(content).get("access_token")
    except json.JSONDecodeError:
        return None


# ─── Reddit Posts ─────────────────────────────────────────────────────────────

def fetch_reddit_oauth(subreddit: str, token: str, sort: str = "hot", limit: int = 25) -> list[dict]:
    url = f"https://oauth.reddit.com/r/{subreddit}/{sort}?limit={limit}"
    content = fetch_url(url, headers={
        "Authorization": f"Bearer {token}",
        "User-Agent": REDDIT_UA,
    })
    return _parse_reddit_listing(content, subreddit)


def fetch_reddit_public(subreddit: str, query: str = None, sort: str = "hot", limit: int = 25) -> list[dict]:
    if query:
        url = (
            f"https://www.reddit.com/r/{subreddit}/search.json"
            f"?q={query}&sort={sort}&restrict_sr=1&limit={limit}&t=week"
        )
    else:
        url = f"https://www.reddit.com/r/{subreddit}/{sort}.json?limit={limit}"

    content = fetch_url(url, headers={"User-Agent": REDDIT_UA})
    return _parse_reddit_listing(content, subreddit)


def fetch_pullpush(subreddit: str, size: int = 25) -> list[dict]:
    """PullPush.io is a public Reddit data archive (no auth required)."""
    # Get posts from the last 7 days, sorted by score
    after = int(time.time()) - 7 * 86400
    url = (
        f"https://api.pullpush.io/reddit/search/submission/"
        f"?subreddit={subreddit}&sort=score&order=desc&size={size}&after={after}"
    )
    content = fetch_url(url)
    if not content:
        return []
    try:
        data = json.loads(content)
        posts = []
        for p in data.get("data", []):
            if p.get("stickied"):
                continue
            posts.append({
                "title": p.get("title", ""),
                "url": f"https://reddit.com{p.get('permalink', '')}",
                "score": p.get("score", 0),
                "num_comments": p.get("num_comments", 0),
                "author": p.get("author", ""),
                "created_utc": int(p.get("created_utc", 0)),
                "flair": p.get("link_flair_text") or "",
                "subreddit": subreddit,
                "preview": (p.get("selftext") or "")[:280].strip(),
            })
        return posts
    except (json.JSONDecodeError, KeyError):
        return []


def _parse_reddit_listing(content: str | None, subreddit: str) -> list[dict]:
    if not content:
        return []
    try:
        data = json.loads(content)
        posts = []
        for item in data.get("data", {}).get("children", []):
            p = item.get("data", {})
            if p.get("stickied") or p.get("distinguished"):
                continue
            posts.append({
                "title": p.get("title", ""),
                "url": "https://reddit.com" + p.get("permalink", ""),
                "score": p.get("score", 0),
                "num_comments": p.get("num_comments", 0),
                "author": p.get("author", ""),
                "created_utc": int(p.get("created_utc", 0)),
                "flair": p.get("link_flair_text") or "",
                "subreddit": subreddit,
                "preview": (p.get("selftext") or "")[:280].strip(),
            })
        return posts
    except (json.JSONDecodeError, KeyError):
        return []


def gather_reddit_posts(subreddits: list[tuple]) -> list[dict]:
    """Gather posts, trying multiple access strategies."""
    token = get_reddit_token()
    if token:
        print("  Using Reddit OAuth")
    else:
        print("  No Reddit OAuth credentials — trying public API + PullPush fallback")

    all_posts: list[dict] = []

    for subreddit, query in subreddits:
        got = []
        if token:
            got = fetch_reddit_oauth(subreddit, token)
        if not got:
            got = fetch_reddit_public(subreddit, query=query)
        if not got:
            print(f"  Public API blocked, trying PullPush for r/{subreddit}...")
            got = fetch_pullpush(subreddit)
        print(f"  r/{subreddit}: {len(got)} posts")
        all_posts.extend(got)
        time.sleep(REQUEST_DELAY)

    # Deduplicate and sort by score
    seen: set[str] = set()
    unique: list[dict] = []
    for p in sorted(all_posts, key=lambda x: x["score"], reverse=True):
        key = p["title"].lower()[:70]
        if key not in seen:
            seen.add(key)
            unique.append(p)

    return unique[:30]


# ─── HSReplay ─────────────────────────────────────────────────────────────────

def fetch_hsreplay_heroes() -> list[dict] | None:
    for url in [
        "https://hsreplay.net/api/v1/battlegrounds/heroes/?GameType=BGT_RANKED",
        "https://hsreplay.net/api/v1/battlegrounds/heroes/",
    ]:
        content = fetch_url(url, headers={
            "Accept": "application/json",
            "Referer": "https://hsreplay.net/battlegrounds/",
        })
        if not content:
            continue
        try:
            data = json.loads(content)
            heroes = data if isinstance(data, list) else (data.get("results") or data.get("heroes"))
            if heroes and isinstance(heroes, list):
                return [{
                    "name": h.get("name") or h.get("card_name", ""),
                    "dbf_id": h.get("dbf_id") or h.get("id"),
                    "avg_final_placement": h.get("avg_final_placement"),
                    "pick_rate": h.get("pick_rate"),
                    "top4_rate": h.get("top_4_rate") or h.get("top4_rate"),
                    "win_rate": h.get("win_rate"),
                } for h in heroes[:30]]
        except (json.JSONDecodeError, AttributeError):
            continue
    return None


# ─── Blizzard News ─────────────────────────────────────────────────────────────

def fetch_blizzard_news() -> list[dict]:
    content = fetch_url("https://hearthstone.blizzard.com/en-us/news")
    if not content:
        # Try alternate news API endpoint
        content = fetch_url(
            "https://us.forums.blizzard.com/en/hearthstone/c/hearthstone-general-discussion/7.json",
            headers={"Accept": "application/json"},
        )
        if content:
            try:
                data = json.loads(content)
                articles = []
                for t in (data.get("topic_list", {}).get("topics") or [])[:8]:
                    articles.append({
                        "title": t.get("title", ""),
                        "url": f"https://us.forums.blizzard.com/en/hearthstone/t/{t.get('slug', '')}/{t.get('id', '')}",
                        "date": t.get("created_at", "")[:10],
                    })
                return articles
            except (json.JSONDecodeError, KeyError):
                pass
        return []

    articles = []

    # Try JSON-LD structured data
    jsonld_pat = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.DOTALL)
    for m in jsonld_pat.finditer(content):
        try:
            node = json.loads(m.group(1))
            items = node if isinstance(node, list) else [node]
            for item in items:
                if isinstance(item, dict) and item.get("@type") in ("NewsArticle", "Article", "BlogPosting"):
                    t = item.get("headline") or item.get("name", "")
                    if t:
                        articles.append({
                            "title": t,
                            "url": item.get("url", "https://hearthstone.blizzard.com/en-us/news"),
                            "date": (item.get("datePublished") or "")[:10],
                        })
        except json.JSONDecodeError:
            pass

    if articles:
        return articles[:8]

    # HTML heading fallback
    for m in re.finditer(r'<h[23][^>]*>(.*?)</h[23]>', content, re.DOTALL | re.IGNORECASE):
        text = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        if text and len(text) > 8 and any(
            kw in text.lower() for kw in ("patch", "balance", "update", "hearthstone", "battleground")
        ):
            articles.append({"title": text, "url": "https://hearthstone.blizzard.com/en-us/news", "date": ""})

    return articles[:8]


# ─── Trend Analysis ───────────────────────────────────────────────────────────

def extract_trends(posts: list[dict]) -> dict:
    keyword_counts: dict[str, int] = {}
    hero_counts: dict[str, int] = {}
    sentiment_pos = 0
    sentiment_neg = 0
    total_score = sum(p.get("score", 0) for p in posts)

    positive_words = {"great", "strong", "op", "broken", "best", "amazing", "overpowered", "insane", "meta", "busted"}
    negative_words = {"weak", "bad", "nerfed", "terrible", "worst", "hate", "unfun", "frustrating", "garbage", "trash"}

    for post in posts:
        text = (post.get("title", "") + " " + post.get("preview", "")).lower()

        for kw in BG_KEYWORDS:
            if kw in text:
                keyword_counts[kw] = keyword_counts.get(kw, 0) + 1

        for hero in HERO_NAMES:
            # Match full name or first significant word
            first = hero.split()[0].lower()
            if hero.lower() in text or (len(first) > 4 and first in text):
                hero_counts[hero] = hero_counts.get(hero, 0) + 1

        words = set(text.split())
        sentiment_pos += len(words & positive_words)
        sentiment_neg += len(words & negative_words)

    sentiment = "neutral"
    if sentiment_pos > sentiment_neg * 1.5:
        sentiment = "positive"
    elif sentiment_neg > sentiment_pos * 1.5:
        sentiment = "negative"

    return {
        "top_keywords": sorted(
            [{"topic": k, "mentions": v} for k, v in keyword_counts.items()],
            key=lambda x: x["mentions"], reverse=True,
        )[:12],
        "discussed_heroes": sorted(
            [{"hero": k, "mentions": v} for k, v in hero_counts.items()],
            key=lambda x: x["mentions"], reverse=True,
        )[:8],
        "sentiment": sentiment,
        "avg_post_score": round(total_score / len(posts), 1) if posts else 0,
    }


# ─── Claude Insights ──────────────────────────────────────────────────────────

def generate_claude_insights(posts: list[dict], trends: dict, heroes_data: list | None) -> str | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    top_posts = sorted(posts, key=lambda x: x["score"], reverse=True)[:20]
    titles = [p["title"] for p in top_posts]
    discussed_heroes = [h["hero"] for h in trends.get("discussed_heroes", [])]
    top_keywords = [k["topic"] for k in trends.get("top_keywords", [])]

    heroes_section = ""
    if heroes_data:
        heroes_section = "\n\nHero stats (HSReplay avg placement, lower is better):\n" + "\n".join(
            f"- {h['name']}: {h.get('avg_final_placement', '?')}"
            for h in heroes_data[:10]
        )

    prompt = f"""You are a Hearthstone Battlegrounds expert analyst writing a daily meta report for {datetime.now(timezone.utc).strftime('%B %d, %Y')}.

Top community discussions today (sorted by upvotes):
{chr(10).join(f'- {t}' for t in titles)}

Most discussed heroes: {', '.join(discussed_heroes) if discussed_heroes else 'none identified'}
Trending topics: {', '.join(top_keywords) if top_keywords else 'none identified'}
{heroes_section}

Write a focused meta analysis covering:

**Meta Overview**
Current state — what strategies are dominant, what's emerging, overall meta tempo.

**Hero Tier Highlights**
2-3 strongest heroes right now and the key reason each is powerful.

**Best Compositions**
Top 1-2 end-game builds or tribe synergies players should aim for.

**Key Tips**
2-3 concrete, actionable tips for improving placement.

Keep it under 350 words. Use **bold** for section headers. Be specific."""

    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 900,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=45) as resp:
            result = json.loads(resp.read().decode())
            return result["content"][0]["text"]
    except Exception as e:
        print(f"  Claude API error: {e}")
        return None


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("🎮 Hearthstone Battlegrounds Meta Scanner")
    print(f"   {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    sources_checked: list[str] = []

    # ── Reddit ────────────────────────────────────────────────────────────────
    print("\n📱 Gathering Reddit community posts...")
    subreddits = [
        ("BobsTavern", None),
        ("hearthstone", "battlegrounds"),
    ]
    posts = gather_reddit_posts(subreddits)
    print(f"  Total unique posts collected: {len(posts)}")
    if posts:
        sources_checked.extend(["reddit.com/r/BobsTavern", "reddit.com/r/hearthstone"])

    # ── HSReplay ──────────────────────────────────────────────────────────────
    print("\n📊 Checking HSReplay hero stats...")
    heroes_data = fetch_hsreplay_heroes()
    time.sleep(REQUEST_DELAY)
    if heroes_data:
        print(f"  Got {len(heroes_data)} heroes")
        sources_checked.append("hsreplay.net/battlegrounds")
    else:
        print("  Unavailable (auth or rate limit)")

    # ── Blizzard News ─────────────────────────────────────────────────────────
    print("\n📰 Fetching Blizzard news...")
    patch_notes = fetch_blizzard_news()
    time.sleep(REQUEST_DELAY)
    print(f"  Found {len(patch_notes)} items")
    if patch_notes:
        sources_checked.append("hearthstone.blizzard.com/news")

    # ── Trend Analysis ────────────────────────────────────────────────────────
    print("\n🔥 Analyzing trends...")
    trends = extract_trends(posts)
    print(f"  {len(trends['top_keywords'])} keywords, {len(trends['discussed_heroes'])} heroes")

    # ── Claude Insights ───────────────────────────────────────────────────────
    print("\n🤖 Generating AI meta insights...")
    meta_insights = generate_claude_insights(posts, trends, heroes_data)
    if meta_insights:
        print("  Claude analysis complete")
    else:
        print("  Skipped — add ANTHROPIC_API_KEY secret to enable")

    # ── Save ──────────────────────────────────────────────────────────────────
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "meta_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "sources_checked": sources_checked,
        "community_posts": posts,
        "hsreplay_heroes": heroes_data,
        "patch_notes": patch_notes,
        "trends": trends,
        "meta_insights": meta_insights,
    }

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n{'=' * 60}")
    print(f"✅ Saved → {DATA_FILE}")
    print(f"   Posts:          {len(posts)}")
    print(f"   Heroes (stats): {len(heroes_data) if heroes_data else 0}")
    print(f"   Patch notes:    {len(patch_notes)}")
    print(f"   Trending topics:{len(trends['top_keywords'])}")
    print(f"   AI insights:    {'yes' if meta_insights else 'no'}")
    print(f"   Sources:        {', '.join(sources_checked) or 'none reached'}")


if __name__ == "__main__":
    main()
