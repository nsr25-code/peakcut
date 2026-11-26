"""Autonomous Background Trend & Event Research Scout.
When PeakCut's video buffer is full, the engine utilizes idle time to continuously scout YouTube,
track breakout view velocity, monitor upcoming gaming events, and pre-cache tomorrow's top viral video leads.
"""
import datetime
import json
import logging
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

from . import config

log = logging.getLogger("researcher")

DATA_FILE = config.DATA_DIR / "trend_intelligence.json"

# Last time scouting ran
_last_scout_time = 0
SCOUT_INTERVAL = 1800  # Run scouting at most once every 30 minutes during idle time

GTA_SCOUT_CHANNELS = [
    {"name": "DarkViperAU", "url": "https://www.youtube.com/@DarkViperAU/videos"},
    {"name": "Jelly", "url": "https://www.youtube.com/@Jelly/videos"},
    {"name": "Whatever57010", "url": "https://www.youtube.com/@whatever57010/videos"},
    {"name": "TGG", "url": "https://www.youtube.com/@TGG_/videos"},
    {"name": "Techno Gamerz", "url": "https://www.youtube.com/@technogamerzofficial/videos"},
]

GTA_SCOUT_QUERIES = [
    "ytsearch10:GTA 6 latest news gameplay leaks viral",
    "ytsearch10:GTA 5 impossible stunt race challenge viral",
    "ytsearch10:GTA 5 funniest fails and police escapes",
]

FF_SCOUT_CHANNELS = [
    {"name": "Total Gaming", "url": "https://www.youtube.com/@TotalGaming093/videos"},
    {"name": "Desi Gamers", "url": "https://www.youtube.com/@DesiGamers_/videos"},
    {"name": "Gyan Gaming", "url": "https://www.youtube.com/@GyanGaming/videos"},
    {"name": "Raistar", "url": "https://www.youtube.com/@raistar/videos"},
    {"name": "Nobru", "url": "https://www.youtube.com/@Nobru/videos"},
]

FF_SCOUT_QUERIES = [
    "ytsearch10:Free Fire 1v4 clutch headshot montage 2026",
    "ytsearch10:Free Fire new update event tournament highlights",
    "ytsearch10:Free Fire grandmaster rank push funny moments",
]

UPCOMING_GAMING_EVENTS = [
    {"event": "GTA 6 Next Trailer & Release Roadmap", "game": "GTA 6", "impact": "High Viral", "date": "Coming Soon"},
    {"event": "Free Fire OB Update & Evo Gun Launch", "game": "Free Fire", "impact": "High Viral", "date": "Weekly Event"},
    {"event": "GTA Online Weekly Update & Double Money Heist", "game": "GTA 5", "impact": "Medium", "date": "Every Thursday"},
    {"event": "Free Fire Clash Squad Ranked Season Reset", "game": "Free Fire", "impact": "High Search Demand", "date": "Seasonal"},
]


def _read_data() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("Could not parse trend_intelligence.json: %s", e)
    return {
        "status": "idle",
        "last_scouted_at": 0,
        "scouted_date": "",
        "hot_leads": {"gta": [], "freefire": []},
        "trending_topics": [],
        "upcoming_events": UPCOMING_GAMING_EVENTS,
        "summary": "Idle. Will scout when quota/buffer is full."
    }


def _write_data(data: dict):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_intelligence() -> dict:
    """Returns current trend intelligence data for dashboard and autopilot."""
    return _read_data()


def get_top_leads(game_type: str = "gta", limit: int = 3) -> list:
    """Returns top pre-researched video leads ready for immediate clipping."""
    data = _read_data()
    leads = data.get("hot_leads", {}).get(game_type, [])
    # Filter out already processed
    state_file = config.DATA_DIR / "autopilot.json"
    processed = set()
    if state_file.exists():
        try:
            st = json.loads(state_file.read_text(encoding="utf-8"))
            processed = set(st.get("processed_ids", []))
        except Exception:
            pass
    return [l for l in leads if l.get("id") not in processed][:limit]


def _fetch_candidates(url_or_query: str, game_type: str, limit: int = 5) -> list:
    """Lightweight metadata fetch using yt-dlp flat-playlist mode."""
    cmd = [sys.executable, "-m", "yt_dlp", "--flat-playlist", "-I", f"1:{limit}", "--dump-json", url_or_query]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            return []
        items = []
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                vid = d.get("id")
                title = d.get("title", "")
                dur = float(d.get("duration") or 0)
                if not vid or (dur > 0 and (dur < 240 or dur > 2400)):
                    continue
                ud = str(d.get("upload_date") or "")
                days_ago = 14.0
                if len(ud) == 8:
                    try:
                        days_ago = max(1.0, float((datetime.datetime.now() - datetime.datetime.strptime(ud, "%Y%m%d")).days))
                    except Exception:
                        pass
                views = int(d.get("view_count") or 0)
                # View Velocity calculation
                velocity = round(views / (days_ago ** 0.6), 1)
                items.append({
                    "id": vid,
                    "title": title,
                    "views": views,
                    "days_ago": round(days_ago, 1),
                    "velocity": velocity,
                    "channel": d.get("uploader") or d.get("channel") or "",
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "game": game_type,
                    "discovered_at": time.time()
                })
            except Exception:
                continue
        return items
    except Exception:
        return []


def scout_idle_trends():
    """Main research function. Called when video buffer is full and worker is free.
    Scouts trending creator uploads and queries, saves intelligence report for next day.
    """
    global _last_scout_time
    now = time.time()
    if now - _last_scout_time < SCOUT_INTERVAL:
        return  # Cooldown active, don't spam requests

    _last_scout_time = now
    today_str = time.strftime("%Y-%m-%d")
    print(f"[Researcher] 🔍 Buffer is satisfied. Starting Autonomous Trend & Event Scouting for tomorrow...", flush=True)

    data = _read_data()
    data["status"] = "scouting"
    data["last_scouted_at"] = now
    data["scouted_date"] = today_str
    _write_data(data)

    # 1. Scout GTA Candidates
    gta_leads = []
    for ch in random.sample(GTA_SCOUT_CHANNELS, min(3, len(GTA_SCOUT_CHANNELS))):
        gta_leads.extend(_fetch_candidates(ch["url"], "gta", limit=4))
    for q in random.sample(GTA_SCOUT_QUERIES, min(2, len(GTA_SCOUT_QUERIES))):
        gta_leads.extend(_fetch_candidates(q, "gta", limit=4))

    # Deduplicate & Sort GTA by Velocity
    gta_unique = {}
    for item in gta_leads:
        if item["id"] not in gta_unique or item["velocity"] > gta_unique[item["id"]]["velocity"]:
            gta_unique[item["id"]] = item
    top_gta = sorted(gta_unique.values(), key=lambda x: -x["velocity"])[:8]

    # 2. Scout Free Fire Candidates
    ff_leads = []
    for ch in random.sample(FF_SCOUT_CHANNELS, min(3, len(FF_SCOUT_CHANNELS))):
        ff_leads.extend(_fetch_candidates(ch["url"], "freefire", limit=4))
    for q in random.sample(FF_SCOUT_QUERIES, min(2, len(FF_SCOUT_QUERIES))):
        ff_leads.extend(_fetch_candidates(q, "freefire", limit=4))

    # Deduplicate & Sort Free Fire by Velocity
    ff_unique = {}
    for item in ff_leads:
        if item["id"] not in ff_unique or item["velocity"] > ff_unique[item["id"]]["velocity"]:
            ff_unique[item["id"]] = item
    top_ff = sorted(ff_unique.values(), key=lambda x: -x["velocity"])[:8]

    # 3. Trending Topics Summary
    topics = [
        {"topic": "GTA 6 Physics & Map Leaks", "momentum": "Explosive 🔥", "demand": "Very High"},
        {"topic": "GTA 5 Impossible Stunt Races", "momentum": "Steady Viral 🚀", "demand": "High"},
        {"topic": "Free Fire 1v4 CS Clutch Moments", "momentum": "High Engagement ⚡", "demand": "Very High"},
        {"topic": "Free Fire Sniper Fast Movement (AWM/M82B)", "momentum": "Trending 🎯", "demand": "High"},
        {"topic": "Grandmaster Rank Push Trolling", "momentum": "Viral Fails 💀", "demand": "Medium-High"},
    ]

    data["status"] = "ready"
    data["hot_leads"] = {"gta": top_gta, "freefire": top_ff}
    data["trending_topics"] = topics
    data["upcoming_events"] = UPCOMING_GAMING_EVENTS
    data["summary"] = f"Researched {len(top_gta)} GTA leads and {len(top_ff)} Free Fire leads with peak view velocity ready for tomorrow."
    _write_data(data)

    print(f"[Researcher] ✅ Scouting complete! Discovered {len(top_gta)} GTA and {len(top_ff)} Free Fire top velocity leads.", flush=True)
