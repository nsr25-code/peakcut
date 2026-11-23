"""Autopilot engine: automatically hunts for top-performing viral videos,
queues them in PeakCut, extracts 30-40s viral clips, and auto-posts to YouTube Shorts."""
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import accounts, auth, config, creators, ctx, jobs

_lock = threading.RLock()
_FILE = config.DATA_DIR / "autopilot.json"

TARGET_CHANNELS = [
    # Global / Worldwide Top GTA Creators (Millions of views & global audience)
    {"name": "Jelly", "url": "https://www.youtube.com/@Jelly/videos"},
    {"name": "Kwebbelkop", "url": "https://www.youtube.com/@kwebbelkop/videos"},
    {"name": "Slogo", "url": "https://www.youtube.com/@slogo/videos"},
    {"name": "DarkViperAU", "url": "https://www.youtube.com/@DarkViperAU/videos"},
    {"name": "TGG", "url": "https://www.youtube.com/@TGG_/videos"},
    {"name": "Whatever57010", "url": "https://www.youtube.com/@whatever57010/videos"},
    # Top Asian / Indian GTA Creators
    {"name": "Techno Gamerz", "url": "https://www.youtube.com/@technogamerzofficial/videos"},
    {"name": "Mythpat", "url": "https://www.youtube.com/@mythpat/videos"},
    {"name": "Total Gaming", "url": "https://www.youtube.com/@TotalGamingOfficial/videos"},
    {"name": "Live Insaan", "url": "https://www.youtube.com/@LiveInsaan/videos"},
    {"name": "Chapati Hindustani Gamer", "url": "https://www.youtube.com/@ChapatiGamer/videos"},
]

GTA_SEARCH_QUERIES = [
    "ytsearch20:GTA 6 viral gameplay leaks reaction trending",
    "ytsearch20:GTA 5 funny moments best viral clips",
    "ytsearch20:GTA 5 impossible stunt race challenge",
    "ytsearch20:GTA 5 darkviperau chaos mod best moments",
    "ytsearch20:GTA 5 biggest heist funny crazy moments",
    "ytsearch20:GTA 5 5 star police chase impossible escape",
    "ytsearch20:GTA 5 jelly funny stunts and fails viral",
    "ytsearch20:GTA 5 kwebbelkop impossible race moments",
    "ytsearch20:GTA 5 techno gamerz funny moments",
    "ytsearch20:GTA 5 mythpat funny challenge",
]

GTA_KEYWORDS = [
    "GTA", "GRAND THEFT AUTO", "GTA5", "GTA 5", "GTA V", "GTAV",
    "GTA6", "GTA 6", "GTA VI", "GTAVI", "GTA ONLINE", "GTA RP",
    "LOS SANTOS", "VICE CITY", "TREVOR", "FRANKLIN", "MICHAEL",
    "LESTER", "CHOP", "LAMAR"
]


def is_gta_video(title: str) -> bool:
    """Strictly checks whether a video title belongs to GTA."""
    t = (title or "").upper()
    return any(k in t for k in GTA_KEYWORDS)


def public_demand_score(item: dict) -> float:
    """Calculate the global public demand, view velocity, and viral interest score of a video."""
    views = float(item.get("views") or 0)
    title = (item.get("title") or "").upper()
    multiplier = 1.0

    # High viewer demand multipliers for trending themes:
    if any(k in title for k in ["GTA 6", "GTA VI", "VICE CITY"]):
        multiplier *= 2.5  # Worldwide #1 trending interest
    if any(k in title for k in ["IMPOSSIBLE", "HARDEST", "WORLD RECORD", "WORLD'S"]):
        multiplier *= 2.0  # Challenge & stunt virality
    if any(k in title for k in ["FUNNY", "FAILS", "MOMENTS", "HILARIOUS"]):
        multiplier *= 1.8  # Comedy & fail moments have maximum Short retention
    if any(k in title for k in ["POLICE", "CHASE", "5 STAR", "BUSTED", "ESCAPE"]):
        multiplier *= 1.6  # High-adrenaline police chases
    if any(k in title for k in ["HEIST", "STEALING", "STOLE", "SUPERCAR", "SECRET", "MYTH"]):
        multiplier *= 1.5  # Heists, rare cars & secrets

    return views * multiplier


def _read_state() -> dict:
    if not _FILE.exists():
        return {"processed_ids": [], "last_run": 0.0, "last_title": ""}
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"processed_ids": [], "last_run": 0.0, "last_title": ""}


def _save_state(state: dict):
    with _lock:
        tmp = _FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(_FILE)


def ensure_creator() -> dict:
    """Make sure the GTA Viral Moments creator profile exists and has autopost enabled."""
    uid = auth.local_user().id
    with ctx.as_user(uid):
        all_c = creators.all_creators()
        found = next((c for c in all_c if "autopilot" in c.get("id", "").lower() or "mindset" in c.get("name", "").lower() or "gta" in c.get("name", "").lower()), None)
        yt_accounts = [a["id"] for a in accounts._all() if a.get("platform") == "youtube"]
        gta_footer = "\n\n👉 Follow for daily crazy GTA moments & funny gameplay!\n#GTA5 #GTA6 #Gaming #Shorts #GTARoleplay #RockstarGames"
        if found:
            updated = False
            if set(found.get("accounts", [])) != set(yt_accounts):
                found["accounts"] = yt_accounts
                updated = True
            if found.get("game") != "Grand Theft Auto V":
                found["game"] = "Grand Theft Auto V"
                updated = True
            if "GTA" not in found.get("name", ""):
                found["name"] = "GTA Viral Moments & Gaming"
                updated = True
            if found.get("post_footer") != gta_footer:
                found["post_footer"] = gta_footer
                updated = True
            if found.get("post_min_score") != 90:
                found["post_min_score"] = 90
                updated = True
            if found.get("min_score") != 90:
                found["min_score"] = 90
                updated = True
            if found.get("daily_cap") != 4:
                found["daily_cap"] = 4
                updated = True
            if found.get("max_clips") != 4:
                found["max_clips"] = 4
                updated = True
            if found.get("post_gap_min") != 105:
                found["post_gap_min"] = 105
                updated = True
            if found.get("min_len") != 20 or found.get("max_len") != 60:
                found["min_len"] = 20
                found["max_len"] = 60
                updated = True
            if not found.get("autopost"):
                found["autopost"] = True
                updated = True
            if updated:
                creators._write(found)
            return found
        c = creators.create({
            "name": "GTA Viral Moments & Gaming",
            "style": "bounce",
            "layout": "blur",
            "captions": True,
            "min_len": 20,
            "max_len": 60,
            "max_clips": 4,
            "min_score": 90,
            "autopost": True,
            "game": "Grand Theft Auto V",
            "accounts": yt_accounts,
            "post_min_score": 90,
            "daily_cap": 4,
            "post_gap_min": 105,
            "privacy": "public",
            "post_footer": gta_footer,
        })
        return c


def fetch_channel_videos(url: str, limit: int = 5) -> list:
    """Query recent videos from a channel using yt-dlp, extracting views and filtering strictly for GTA."""
    cmd = [sys.executable, "-m", "yt_dlp", "--flat-playlist", "-I", f"1:{limit}", "--dump-json", url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90, encoding="utf-8", errors="replace")
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
                # Ignore shorts (< 90 seconds) or live streams in waiting state, and strictly filter for GTA
                if vid and is_gta_video(title) and (dur >= 600 or dur == 0):
                    items.append({
                        "id": vid,
                        "title": title,
                        "duration": dur,
                        "views": int(d.get("view_count") or 0),
                        "channel": d.get("uploader") or d.get("channel") or "",
                    })
            except Exception:
                continue
        return items
    except Exception:
        return []


def fetch_search_videos(query: str, limit: int = 5) -> list:
    """Query search results using yt-dlp, extracting views and filtering strictly for GTA."""
    cmd = [sys.executable, "-m", "yt_dlp", "--flat-playlist", "-I", f"1:{limit}", "--dump-json", query]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90, encoding="utf-8", errors="replace")
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
                if vid and is_gta_video(title) and (dur >= 600 or dur == 0):
                    items.append({
                        "id": vid,
                        "title": title,
                        "duration": dur,
                        "views": int(d.get("view_count") or 0),
                        "channel": d.get("uploader") or d.get("channel") or "",
                    })
            except Exception:
                continue
        return items
    except Exception:
        return []


def check_and_queue():
    """Look for any new GTA video from target channels or search, and queue it in PeakCut."""
    state = _read_state()
    processed = set(state.get("processed_ids", []))
    creator = ensure_creator()
    uid = auth.local_user().id

    with ctx.as_user(uid):
        if jobs.busy_count() > 0:
            return  # a video is already being processed, wait for it to complete

    # Future Buffer Check: Keep a rolling buffer of 4 days ahead, with strictly 4 clips per day.
    # Checks dates sequentially: Day D must have all 4 clips scheduled before Day D+1 is scheduled.
    posts_file = config.DATA_DIR / "posts.json"
    BUFFER_DAYS_AHEAD = 4
    DAILY_CAP = 4
    DAILY_SLOTS = [(7, 30), (11, 30), (15, 30), (19, 30)]
    now = time.time()
    now_lt = time.localtime(now)
    import datetime
    start_date = datetime.date(now_lt.tm_year, now_lt.tm_mon, now_lt.tm_mday)

    buffer_full = True
    if posts_file.exists():
        try:
            posts_data = json.loads(posts_file.read_text(encoding="utf-8"))
            for day_offset in range(BUFFER_DAYS_AHEAD):
                check_date = start_date + datetime.timedelta(days=day_offset)
                date_str = check_date.strftime("%Y-%m-%d")
                occupied_count = 0
                for h, m in DAILY_SLOTS:
                    slot_t = time.mktime((check_date.year, check_date.month, check_date.day, h, m, 0, 0, 0, -1))
                    is_occ = any(
                        p.get("status") in ("scheduled", "posted", "posting")
                        and abs((p.get("target_publish_at") or p.get("scheduled_publish_at") or p.get("due_at") or 0) - slot_t) <= 60 * 60
                        for p in posts_data
                    )
                    if is_occ or (day_offset == 0 and slot_t <= now + 120):
                        occupied_count += 1
                if occupied_count < DAILY_CAP:
                    buffer_full = False
                    break
        except Exception:
            buffer_full = False

    if buffer_full:
        return  # All 4 days in future buffer (16 clips) are complete; rest until slots open up.

    candidates = []

    # 1. Search top global target GTA channels
    for ch in TARGET_CHANNELS:
        videos = fetch_channel_videos(ch["url"], limit=3)
        for v in videos:
            if v["id"] not in processed:
                v["source_label"] = ch["name"]
                candidates.append(v)

    # 2. Add candidates from viral trending search queries
    for q in GTA_SEARCH_QUERIES:
        videos = fetch_search_videos(q, limit=3)
        for v in videos:
            if v["id"] not in processed:
                v["source_label"] = v.get("channel") or "Global Search"
                candidates.append(v)

    if not candidates:
        return

    # Rank all candidate videos strictly by Public Demand & Trending Virality Score
    candidates.sort(key=public_demand_score, reverse=True)
    best = candidates[0]
    vid = best["id"]
    title = best["title"]
    views = best.get("views", 0)
    source_name = best.get("source_label", "Worldwide")
    vurl = f"https://www.youtube.com/watch?v={vid}"

    with ctx.as_user(uid):
        job = jobs.create(
            source={"type": "url", "url": vurl, "name": title},
            options={
                "quality": 1080,
                "style": "bounce",
                "layout": "blur",
                "captions": True,
                "min_len": 20,
                "max_len": 60,
                "max_clips": 4,
                "auto_clips": True,
                "game": "Grand Theft Auto V",
            },
            creator=creator["id"],
        )
    processed.add(vid)
    state["processed_ids"] = list(processed)[-200:]
    state["last_run"] = time.time()
    state["last_title"] = title
    _save_state(state)
    print(f"\n[Autopilot] Queued top public-demand GTA video ({views:,} views) from {source_name}: {title} ({vurl})\n", flush=True)


def cleanup_old_sources():
    """Clean up any leftover raw source files (*.src.mp4) and any unused clips to save disk space."""
    try:
        users_dir = config.DATA_DIR / "users"
        if not users_dir.exists():
            return
        freed = 0
        # 1. Clean up temporary sliced source pieces
        for src_file in users_dir.glob("**/clips/*.src.mp4"):
            try:
                sz = src_file.stat().st_size
                src_file.unlink(missing_ok=True)
                freed += sz
            except Exception:
                pass

        # 2. Clean up any clip that has no use (score < 90% or status discarded/cancelled)
        posts_file = config.DATA_DIR / "posts.json"
        scheduled_clips = set()
        if posts_file.exists():
            try:
                posts_data = json.loads(posts_file.read_text(encoding="utf-8"))
                scheduled_clips = {(p.get("job"), p.get("clip")) for p in posts_data if p.get("status") == "scheduled" and p.get("score", 0) >= 90}
            except Exception:
                pass

        for jf in users_dir.glob("**/job.json"):
            try:
                job_data = json.loads(jf.read_text(encoding="utf-8"))
                jid = job_data.get("id")
                job_status = job_data.get("status")
                # Only clean finished jobs
                if job_status not in ("done", "error"):
                    continue
                cdir = jf.parent / "clips"
                for c in job_data.get("clips", []):
                    cid = c.get("id")
                    score = int(c.get("score") or 0)
                    status = c.get("status")
                    if (jid, cid) not in scheduled_clips and (score < 90 or status == "discarded"):
                        for ext in ("mp4", "jpg", "ass", "src.mp4"):
                            f = cdir / f"{cid}.{ext}"
                            if f.exists():
                                sz = f.stat().st_size
                                f.unlink(missing_ok=True)
                                freed += sz
                        c["status"] = "discarded"
                        c["file"] = ""
                        c["cover"] = ""
                jf.write_text(json.dumps(job_data, indent=1), encoding="utf-8")
            except Exception:
                pass

        if freed > 0:
            print(f"[Autopilot] Auto-cleaned unused clips & sources, freed {freed / (1024*1024):.1f} MB", flush=True)
    except Exception:
        pass


def _worker():
    time.sleep(10)  # let server finish booting
    while True:
        try:
            cleanup_old_sources()
            check_and_queue()
        except Exception as e:
            print(f"[Autopilot] Error checking channels: {e}", flush=True)
        time.sleep(1800)  # check every 30 minutes


def start():
    if config.env("PEAKCUT_AUTOPILOT", "1") != "0":
        threading.Thread(target=_worker, daemon=True, name="autopilot").start()
