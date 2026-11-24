"""Autopilot engine: automatically hunts for top-performing viral videos,
queues them in PeakCut, extracts 30-40s viral clips, and auto-posts to YouTube Shorts.

Supports dual dedicated pipelines:
1. GTA Channel: "Hype Drip Clips" (yt_UC5Ds3s_ijysctdQIULSRxOQ) -> GTA V / GTA 6 viral moments
2. Free Fire Channel: "NSR FF LIVE" (yt_UCBfk2Dy1TuLk4Hh4ADzOZqw) -> Garena Free Fire clutches, headshots & funny moments

Core Rules for both channels:
- Strictly 4 clips per day
- Fixed slots: 07:30 AM, 11:30 AM, 03:30 PM, 07:30 PM
- Only score >= 90%
- Immediate local mp4 deletion after YouTube upload
- 4-day rolling buffer ahead
"""
import datetime
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

# ==========================================
# GTA CONFIGURATION (Hype Drip Clips)
# ==========================================
GTA_ACCOUNT_ID = "yt_UC5Ds3s_ijysctdQIULSRxOQ"
GTA_CREATOR_ID = "viral-podcasts-mindset"

GTA_TARGET_CHANNELS = [
    # Global / Worldwide Top GTA Creators
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


def gta_public_demand_score(item: dict) -> float:
    """Calculate the public demand, view velocity, and viral interest score of a GTA video."""
    views = float(item.get("views") or 0)
    title = (item.get("title") or "").upper()
    multiplier = 1.0

    if any(k in title for k in ["GTA 6", "GTA VI", "VICE CITY"]):
        multiplier *= 2.5
    if any(k in title for k in ["IMPOSSIBLE", "HARDEST", "WORLD RECORD", "WORLD'S"]):
        multiplier *= 2.0
    if any(k in title for k in ["FUNNY", "FAILS", "MOMENTS", "HILARIOUS"]):
        multiplier *= 1.8
    if any(k in title for k in ["POLICE", "CHASE", "5 STAR", "BUSTED", "ESCAPE"]):
        multiplier *= 1.6
    if any(k in title for k in ["HEIST", "STEALING", "STOLE", "SUPERCAR", "SECRET", "MYTH"]):
        multiplier *= 1.5

    return views * multiplier


# ==========================================
# FREE FIRE CONFIGURATION (NSR FF LIVE)
# ==========================================
FREEFIRE_ACCOUNT_ID = "yt_UCBfk2Dy1TuLk4Hh4ADzOZqw"
FREEFIRE_CREATOR_ID = "freefire-viral-moments"

FREEFIRE_TARGET_CHANNELS = [
    {"name": "Total Gaming", "url": "https://www.youtube.com/@TotalGaming093/videos"},
    {"name": "Desi Gamers", "url": "https://www.youtube.com/@DesiGamers_/videos"},
    {"name": "Gyan Gaming", "url": "https://www.youtube.com/@GyanGaming/videos"},
    {"name": "A_S Gaming", "url": "https://www.youtube.com/@A_S_Gaming/videos"},
    {"name": "Lokesh Gamer", "url": "https://www.youtube.com/@LokeshGamer/videos"},
    {"name": "Raistar", "url": "https://www.youtube.com/@raistar/videos"},
    {"name": "TWO-SIDE GAMERS", "url": "https://www.youtube.com/@TwoSideGamers/videos"},
    {"name": "Nobru", "url": "https://www.youtube.com/@Nobru/videos"},
    {"name": "TheDonato", "url": "https://www.youtube.com/@TheDonato/videos"},
    {"name": "Cerol", "url": "https://www.youtube.com/@Cerol/videos"},
]

FREEFIRE_SEARCH_QUERIES = [
    "ytsearch20:Free Fire 1v4 clutch gameplay headshot montage",
    "ytsearch20:Free Fire grandmaster rank push funny moments",
    "ytsearch20:Free Fire total gaming best clutch moments",
    "ytsearch20:Free Fire raistar insane fast movement headshot",
    "ytsearch20:Free Fire desi gamers funny gameplay moments",
    "ytsearch20:Free Fire as gaming challenge funny moments",
    "ytsearch20:Free Fire insane tournament clutch highlights",
    "ytsearch20:Free Fire max funny fails and pro moments",
]

FREEFIRE_KEYWORDS = [
    "FREE FIRE", "FREEFIRE", "GARENA", "TOTAL GAMING", "AJJUBHAI",
    "RAISTAR", "DESI GAMERS", "GYAN GAMING", "AS GAMING", "LOKESH GAMER",
    "NOBRU", "THEDONATO", "CEROL", "BOOYAH", "FREE FIRE MAX", "FF MAX",
    "SOLO VS SQUAD", "CUSTOM ROOM"
]
FREEFIRE_WORD_TOKENS = {"FF", "AWM", "1V4", "1VS4", "CLUTCH", "HEADSHOT", "GRANDMASTER"}


def is_freefire_video(title: str, description: str = "") -> bool:
    """Strictly checks whether a video belongs to Free Fire."""
    t = (title or "").upper()
    d = (description or "").upper()
    if any(k in t for k in FREEFIRE_KEYWORDS):
        return True
    words = set(re.findall(r"\b[A-Z0-9]+\b", t))
    if bool(words & FREEFIRE_WORD_TOKENS):
        return True
    if any(k in d for k in ["FREE FIRE", "FREEFIRE", "GARENA", "BOOYAH", "FF MAX", "#FREEFIRE"]):
        return True
    return False


def freefire_public_demand_score(item: dict) -> float:
    """Calculate the public demand and viral interest score of a Free Fire video."""
    views = float(item.get("views") or 0)
    title = (item.get("title") or "").upper()
    multiplier = 1.0

    if any(k in title for k in ["1V4", "CLUTCH", "HEADSHOT", "AWM", "BOOYAH"]):
        multiplier *= 2.5
    if any(k in title for k in ["GRANDMASTER", "WORLD RECORD", "FASTEST", "CUSTOM ROOM"]):
        multiplier *= 2.0
    if any(k in title for k in ["FUNNY", "FAILS", "TROLL", "MOMENTS"]):
        multiplier *= 1.8
    if any(k in title for k in ["AJJUBHAI", "TOTAL GAMING", "RAISTAR", "NOBRU"]):
        multiplier *= 1.6

    return views * multiplier


# ==========================================
# STATE & PERSISTENCE
# ==========================================
def _read_state() -> dict:
    if not _FILE.exists():
        return {"processed_ids": [], "last_run": 0.0, "last_title": "", "last_channel": ""}
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"processed_ids": [], "last_run": 0.0, "last_title": "", "last_channel": ""}


def _save_state(state: dict):
    with _lock:
        tmp = _FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(_FILE)


# ==========================================
# CREATOR PROFILES
# ==========================================
def ensure_gta_creator() -> dict:
    """Make sure the GTA Viral Moments creator profile exists and is pinned to Hype Drip Clips."""
    uid = auth.local_user().id
    with ctx.as_user(uid):
        all_c = creators.all_creators()
        found = next((c for c in all_c if c.get("id") == GTA_CREATOR_ID or "gta" in c.get("name", "").lower() or "mindset" in c.get("id", "").lower()), None)
        gta_footer = "\n\n👉 Follow for daily crazy GTA moments & funny gameplay!\n#GTA5 #GTA6 #Gaming #Shorts #GTARoleplay #RockstarGames"
        target_accs = [GTA_ACCOUNT_ID]
        if found:
            updated = False
            if found.get("accounts") != target_accs:
                found["accounts"] = target_accs
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
            if found.get("post_min_score") != 90 or found.get("min_score") != 90:
                found["post_min_score"] = 90
                found["min_score"] = 90
                updated = True
            if found.get("daily_cap") != 4 or found.get("max_clips") != 4:
                found["daily_cap"] = 4
                found["max_clips"] = 4
                updated = True
            if found.get("post_gap_min") != 105:
                found["post_gap_min"] = 105
                updated = True
            if not found.get("autopost"):
                found["autopost"] = True
                updated = True
            if updated:
                creators._write(found)
            return found
        c = creators.create({
            "id": GTA_CREATOR_ID,
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
            "accounts": target_accs,
            "post_min_score": 90,
            "daily_cap": 4,
            "post_gap_min": 105,
            "privacy": "public",
            "post_footer": gta_footer,
        })
        return c


def ensure_freefire_creator() -> dict:
    """Make sure the Free Fire Viral Moments creator profile exists and is pinned to NSR FF LIVE."""
    uid = auth.local_user().id
    with ctx.as_user(uid):
        all_c = creators.all_creators()
        found = next((c for c in all_c if c.get("id") == FREEFIRE_CREATOR_ID or "freefire" in c.get("name", "").lower()), None)
        ff_footer = "\n\n👉 Follow for daily insane Free Fire clutches, 1v4 headshots & funny moments!\n#FreeFire #FFShorts #FreeFireHighlights #GarenaFreeFire #FFClutch #FreeFireMax #FF"
        target_accs = [FREEFIRE_ACCOUNT_ID]
        if found:
            updated = False
            if found.get("accounts") != target_accs:
                found["accounts"] = target_accs
                updated = True
            if found.get("game") != "Garena Free Fire":
                found["game"] = "Garena Free Fire"
                updated = True
            if "Free Fire" not in found.get("name", ""):
                found["name"] = "Free Fire Viral Moments & Highlights"
                updated = True
            if found.get("post_footer") != ff_footer:
                found["post_footer"] = ff_footer
                updated = True
            if found.get("post_min_score") != 90 or found.get("min_score") != 90:
                found["post_min_score"] = 90
                found["min_score"] = 90
                updated = True
            if found.get("daily_cap") != 4 or found.get("max_clips") != 4:
                found["daily_cap"] = 4
                found["max_clips"] = 4
                updated = True
            if found.get("post_gap_min") != 105:
                found["post_gap_min"] = 105
                updated = True
            if not found.get("autopost"):
                found["autopost"] = True
                updated = True
            if updated:
                creators._write(found)
            return found
        c = creators.create({
            "id": FREEFIRE_CREATOR_ID,
            "name": "Free Fire Viral Moments & Highlights",
            "style": "bounce",
            "layout": "blur",
            "captions": True,
            "min_len": 20,
            "max_len": 60,
            "max_clips": 4,
            "min_score": 90,
            "autopost": True,
            "game": "Garena Free Fire",
            "accounts": target_accs,
            "post_min_score": 90,
            "daily_cap": 4,
            "post_gap_min": 105,
            "privacy": "public",
            "post_footer": ff_footer,
        })
        return c


# ==========================================
# VIDEO SCRAPING & FETCHING
# ==========================================
def fetch_channel_videos(url: str, game_type: str = "gta", limit: int = 5) -> list:
    """Query recent videos from a channel using yt-dlp, filtering strictly for the requested game."""
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
                desc = d.get("description", "")
                dur = float(d.get("duration") or 0)
                is_match = is_freefire_video(title, desc) if game_type == "freefire" else is_gta_video(title)
                if vid and is_match and (dur >= 300 or dur == 0):
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


def fetch_search_videos(query: str, game_type: str = "gta", limit: int = 5) -> list:
    """Query search results using yt-dlp, filtering strictly for the requested game."""
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
                desc = d.get("description", "")
                dur = float(d.get("duration") or 0)
                is_match = is_freefire_video(title, desc) if game_type == "freefire" else is_gta_video(title)
                if vid and is_match and (dur >= 300 or dur == 0):
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


# ==========================================
# BUFFER CHECKER (INDEPENDENT PER ACCOUNT)
# ==========================================
def is_account_buffer_full(account_id: str, posts_data: list, buffer_days: int = 4, daily_cap: int = 4) -> bool:
    """Checks whether an account has all daily slots filled for the next buffer_days."""
    DAILY_SLOTS = [(7, 30), (11, 30), (15, 30), (19, 30)]
    now = time.time()
    now_lt = time.localtime(now)
    start_date = datetime.date(now_lt.tm_year, now_lt.tm_mon, now_lt.tm_mday)

    for day_offset in range(buffer_days):
        check_date = start_date + datetime.timedelta(days=day_offset)
        occupied_count = 0
        for h, m in DAILY_SLOTS:
            slot_t = time.mktime((check_date.year, check_date.month, check_date.day, h, m, 0, 0, 0, -1))
            is_occ = any(
                p.get("account") == account_id
                and p.get("status") in ("scheduled", "posted", "posting")
                and abs((p.get("target_publish_at") or p.get("scheduled_publish_at") or p.get("due_at") or 0) - slot_t) <= 60 * 60
                for p in posts_data
            )
            if is_occ or (day_offset == 0 and slot_t <= now + 120):
                occupied_count += 1
        if occupied_count < daily_cap:
            return False  # Open slot exists on this day!
    return True


# ==========================================
# AUTOPILOT QUEUE DISPATCHER
# ==========================================
def check_and_queue():
    """Checks buffer status for both channels and queues the top viral video for the channel needing clips most."""
    uid = auth.local_user().id
    with ctx.as_user(uid):
        if jobs.busy_count() > 0:
            return  # a video is already rendering, wait for completion

    state = _read_state()
    processed = set(state.get("processed_ids", []))

    posts_file = config.DATA_DIR / "posts.json"
    posts_data = []
    if posts_file.exists():
        try:
            posts_data = json.loads(posts_file.read_text(encoding="utf-8"))
        except Exception:
            posts_data = []

    gta_buffer_full = is_account_buffer_full(GTA_ACCOUNT_ID, posts_data)
    ff_buffer_full = is_account_buffer_full(FREEFIRE_ACCOUNT_ID, posts_data)

    last_chan = state.get("last_channel", "")
    # Strict Round-Robin Alternation Rule (Equality for both channels):
    # 1 video Channel 1 (GTA) -> 1 video Channel 2 (Free Fire) -> 1 video Channel 1 (GTA) -> 1 video Channel 2 (Free Fire)...
    if last_chan == "gta":
        preferred = ["freefire", "gta"]
    else:
        preferred = ["gta", "freefire"]

    queue_order = []
    for c in preferred:
        if c == "gta" and not gta_buffer_full:
            queue_order.append("gta")
        elif c == "freefire" and not ff_buffer_full:
            queue_order.append("freefire")

    for target in queue_order:
        if target == "freefire":
            ff_creator = ensure_freefire_creator()
            candidates = []
            for ch in FREEFIRE_TARGET_CHANNELS:
                videos = fetch_channel_videos(ch["url"], game_type="freefire", limit=3)
                for v in videos:
                    if v["id"] not in processed:
                        v["source_label"] = ch["name"]
                        candidates.append(v)
            for q in FREEFIRE_SEARCH_QUERIES:
                videos = fetch_search_videos(q, game_type="freefire", limit=3)
                for v in videos:
                    if v["id"] not in processed:
                        v["source_label"] = v.get("channel") or "Global Search"
                        candidates.append(v)

            if candidates:
                candidates.sort(key=freefire_public_demand_score, reverse=True)
                best = candidates[0]
                vid = best["id"]
                title = best["title"]
                views = best.get("views", 0)
                source_name = best.get("source_label", "Trending")
                vurl = f"https://www.youtube.com/watch?v={vid}"

                with ctx.as_user(uid):
                    jobs.create(
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
                            "game": "Garena Free Fire",
                        },
                        creator=ff_creator["id"],
                    )
                processed.add(vid)
                state["processed_ids"] = list(processed)[-250:]
                state["last_run"] = time.time()
                state["last_title"] = title
                state["last_channel"] = "freefire"
                _save_state(state)
                print(f"\n[Autopilot] Queued top Free Fire video ({views:,} views) from {source_name}: {title} ({vurl})\n", flush=True)
                return

        elif target == "gta":
            gta_creator = ensure_gta_creator()
            candidates = []
            for ch in GTA_TARGET_CHANNELS:
                videos = fetch_channel_videos(ch["url"], game_type="gta", limit=3)
                for v in videos:
                    if v["id"] not in processed:
                        v["source_label"] = ch["name"]
                        candidates.append(v)
            for q in GTA_SEARCH_QUERIES:
                videos = fetch_search_videos(q, game_type="gta", limit=3)
                for v in videos:
                    if v["id"] not in processed:
                        v["source_label"] = v.get("channel") or "Global Search"
                        candidates.append(v)

            if candidates:
                candidates.sort(key=gta_public_demand_score, reverse=True)
                best = candidates[0]
                vid = best["id"]
                title = best["title"]
                views = best.get("views", 0)
                source_name = best.get("source_label", "Trending")
                vurl = f"https://www.youtube.com/watch?v={vid}"

                with ctx.as_user(uid):
                    jobs.create(
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
                        creator=gta_creator["id"],
                    )
                processed.add(vid)
                state["processed_ids"] = list(processed)[-250:]
                state["last_run"] = time.time()
                state["last_title"] = title
                state["last_channel"] = "gta"
                _save_state(state)
                print(f"\n[Autopilot] Queued top GTA video ({views:,} views) from {source_name}: {title} ({vurl})\n", flush=True)
                return


# ==========================================
# DISK CLEANUP & WORKER
# ==========================================
def cleanup_old_sources():
    """Clean up any leftover raw source files (*.src.mp4) and any unused clips to save disk space."""
    try:
        users_dir = config.DATA_DIR / "users"
        if not users_dir.exists():
            return
        freed = 0
        for src_file in users_dir.glob("**/clips/*.src.mp4"):
            try:
                sz = src_file.stat().st_size
                src_file.unlink(missing_ok=True)
                freed += sz
            except Exception:
                pass

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
    time.sleep(5)
    while True:
        try:
            cleanup_old_sources()
            check_and_queue()
        except Exception as e:
            print(f"[Autopilot] Error checking channels: {e}", flush=True)
        time.sleep(60)


def start():
    if config.env("PEAKCUT_AUTOPILOT", "1") != "0":
        threading.Thread(target=_worker, daemon=True, name="autopilot").start()
