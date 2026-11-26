"""Autonomous Daily Community & Debate Post Engine for YouTube Channels.
Generates, stores, and organizes high-virality polls and debate posts to maximize Community & Posts Tab engagement.
"""
import json
import logging
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from . import accounts, config

log = logging.getLogger("community")

DATA_FILE = config.DATA_DIR / "community_polls.json"

GTA_VIRAL_POLLS = [
    {
        "question": "Which GTA stunt vehicle has the MOST broken physics? 🚗🔥",
        "options": ["Scramjet (Rocket Jump)", "Oppressor Mk2", "Vigilante (Batmobile)", "Toreador (Boost King)"],
        "tag": "Physics Debate",
        "reason": "Debates between boost vehicles always trigger massive comment wars."
    },
    {
        "question": "If you could pick ONE GTA 6 feature on Day 1, what would it be? 🌴🎮",
        "options": ["100% Enterable Buildings", "Ultra-Realistic Car Physics", "Undercover Police System", "Custom Gang Wars"],
        "tag": "GTA 6 Trend",
        "reason": "GTA 6 anticipation is at peak momentum worldwide."
    },
    {
        "question": "Which GTA 5 heist payout method was the absolute BEST grind? 💰",
        "options": ["Cayo Perico Solo Stealth", "Diamond Casino Heist", "Doomsday Heist Act II", "Original Pacific Standard"],
        "tag": "Heist Strategy",
        "reason": "GTA grinders have strong opinions on money-making efficiency."
    },
    {
        "question": "What is the SINGLE hardest stunt to land in GTA Online? 🤯",
        "options": ["Maze Bank sniper parachute", "Tunnel jet flight inverted", "Mount Chiliad BMX slide", "Cargo plane sky car landing"],
        "tag": "Stunt Challenge",
        "reason": "High skill stunts challenge viewers to share their personal records."
    },
    {
        "question": "Which protagonist had the COLDDEST mindset in GTA history? 👑🥶",
        "options": ["Trevor Philips (Pure Chaos)", "Michael De Santa (Mastermind)", "Franklin Clinton (Loyalty & Grind)", "Niko Bellic (Cold Revenge)"],
        "tag": "Lore Debate",
        "reason": "Character loyalty brings high voter participation."
    },
    {
        "question": "Most annoying thing that ruins a GTA Online lobby? 💀🤬",
        "options": ["Oppressor Mk2 griefer", "Orbital cannon spammer", "Cop shooting for no reason", "Getting disconnected during delivery"],
        "tag": "Relatable Frustration",
        "reason": "Relatable pain points drive maximum likes and comments."
    },
    {
        "question": "Which GTA 6 character will you play the most? 🌴🔫",
        "options": ["Lucia (Bonnie & Clyde vibe)", "Jason (Tactical & Muscle)", "Both equally", "Only waiting for GTA 6 Online"],
        "tag": "GTA 6 Hype",
        "reason": "First look trailer sparked character loyalty debates."
    }
]

FF_VIRAL_POLLS = [
    {
        "question": "Best AR gun combination for Grandmaster CS Rank Push? 💥👑",
        "options": ["M4A1-III + MP40", "Woodpecker + Shotgun", "Groza + UMP", "AK-47 (Dragon) + MAG-7"],
        "tag": "Rank Meta",
        "reason": "Rank pushers constantly search for weapon meta consensus."
    },
    {
        "question": "Who is the ULTIMATE character active skill in Free Fire? ⚡",
        "options": ["Alok (Speed & Health Boost)", "Tatsuya (Double Dash Rush)", "Chrono (Shield King)", "Dimitri (Self Revive Zone)"],
        "tag": "Skill Debate",
        "reason": "Alok vs Tatsuya is the most contested debate in the Free Fire community."
    },
    {
        "question": "Which 1v4 clutch situation gives you the MOST adrenaline? 💀🩸",
        "options": ["Last zone with 0 Gloo Wall", "10 HP clutch against 4 rushers", "Headshot only with Desert Eagle", "Reviving squad in final circle"],
        "tag": "Clutch Moments",
        "reason": "Emotional gaming memories generate high voter resonance."
    },
    {
        "question": "Which evo gun skin gives the CLEANEST red numbers? 🎯🔥",
        "options": ["MP40 Predatory Cobra", "M1014 Green Flame Draco", "UMP Art of War", "XM8 Destiny Guardian"],
        "tag": "Skin Flex",
        "reason": "Evo skins are the biggest status symbols in Free Fire."
    },
    {
        "question": "What's the most toxic player move in CS Ranked? 🤬",
        "options": ["Stealing your Airdrop/Guns", "Going AFK after losing Round 1", "Toxic emote after 4v1 spray", "Never buying Gloo Wall for team"],
        "tag": "Gamer Rants",
        "reason": "Toxic teammate rants attract passionate agreement in comments."
    },
    {
        "question": "Best Sniper in Free Fire for 1-shot elimination? 🎯💥",
        "options": ["AWM (The OG King)", "M82B (Wall Breaker)", "Kar98k (Biometric Scope)", "Barrett (Heavy Impact)"],
        "tag": "Sniper Meta",
        "reason": "Sniper mains love defending their favorite rifle."
    },
    {
        "question": "What is your main role in Free Fire Squad? 🛡️⚔️",
        "options": ["Rusher (First in, first kill)", "Sniper / Long Range Cover", "Support / Healer / Reviver", "IGL / Brain of the squad"],
        "tag": "Squad Identity",
        "reason": "Players love claiming their specific squad identity."
    }
]


def format_post_text(question: str, options: list, is_post: bool = False) -> str:
    """Formats text for direct clipboard copy and paste into YouTube."""
    if is_post:
        # Single-box Text Debate Post for Hype Drip Clips
        return (
            f"{question}\n\n"
            f"1️⃣ {options[0]}\n"
            f"2️⃣ {options[1]}\n"
            f"3️⃣ {options[2]}\n"
            f"4️⃣ {options[3]}\n\n"
            f"Comment your vote (1, 2, 3 or 4) below! 👇💬\n"
            f"#GTA5 #GTA6 #Shorts"
        )
    # Interactive Poll format for NSR FF LIVE
    return (
        f"{question}\n\n"
        f"1. {options[0]}\n"
        f"2. {options[1]}\n"
        f"3. {options[2]}\n"
        f"4. {options[3]}"
    )


def _read_data() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("Could not parse community_polls.json: %s", e)
    return {"channels": {}, "updated_at": 0}


def _write_data(data: dict):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_polls() -> dict:
    """Returns today's polls, upcoming polls, and channel links."""
    ensure_daily_polls()
    return _read_data()


def ensure_daily_polls() -> dict:
    """Checks each connected YouTube channel and ensures a fresh viral post/poll is ready for today."""
    data = _read_data()
    accs = accounts.list_accounts()
    yt_accs = [a for a in accs if a.get("platform") == "youtube"]
    today_str = time.strftime("%Y-%m-%d")

    changed = False
    for acc in yt_accs:
        aid = acc["id"]
        ch_id = aid.replace("yt_", "")
        name = acc.get("name", "YouTube Channel")
        is_ff = "ff" in name.lower() or "free fire" in name.lower()
        pool = FF_VIRAL_POLLS if is_ff else GTA_VIRAL_POLLS
        game_label = "Free Fire" if is_ff else "GTA 5 & 6"

        # Hype Drip Clips uses standard "post" format (since polls are restricted/disabled on it)
        # NSR FF LIVE uses interactive "poll" format
        post_type = "poll" if is_ff else "post"
        type_label = "Interactive Poll" if is_ff else "Debate Post"
        comm_url = f"https://www.youtube.com/channel/{ch_id}/community" if is_ff else f"https://www.youtube.com/channel/{ch_id}/posts"

        ch_data = data.setdefault("channels", {}).setdefault(aid, {
            "account_id": aid,
            "channel_id": ch_id,
            "name": name,
            "game": game_label,
            "post_type": post_type,
            "type_label": type_label,
            "community_url": comm_url,
            "today_poll": None,
            "upcoming_polls": [],
            "history": []
        })
        ch_data["name"] = name
        ch_data["post_type"] = post_type
        ch_data["type_label"] = type_label
        ch_data["community_url"] = comm_url

        today_poll = ch_data.get("today_poll")
        if not today_poll or today_poll.get("date") != today_str:
            if today_poll:
                ch_data.setdefault("history", []).append(today_poll)

            upcoming = ch_data.get("upcoming_polls", [])
            picked = None
            if upcoming and upcoming[0].get("date") == today_str:
                picked = upcoming.pop(0)
            else:
                used_questions = {p.get("question") for p in ch_data.get("history", [])[-10:]}
                candidates = [p for p in pool if p["question"] not in used_questions]
                if not candidates:
                    candidates = pool
                selected = random.choice(candidates)
                picked = {
                    "id": f"poll_{int(time.time())}_{random.randint(1000, 9999)}",
                    "date": today_str,
                    "question": selected["question"],
                    "options": selected["options"],
                    "formatted_text": format_post_text(selected["question"], selected["options"], is_post=(post_type == "post")),
                    "tag": selected["tag"],
                    "reason": selected["reason"],
                    "game": game_label,
                    "post_type": post_type,
                    "created_at": time.time()
                }

            ch_data["today_poll"] = picked
            changed = True
        else:
            # Update formatted text and post_type if not set
            if "formatted_text" not in today_poll or today_poll.get("post_type") != post_type:
                today_poll["post_type"] = post_type
                today_poll["formatted_text"] = format_post_text(today_poll["question"], today_poll["options"], is_post=(post_type == "post"))
                changed = True

        # Ensure upcoming polls have formatted_text
        upcoming = ch_data.get("upcoming_polls", [])
        if len(upcoming) < 5:
            used_questions = {ch_data["today_poll"]["question"]} if ch_data.get("today_poll") else set()
            used_questions.update(p.get("question") for p in upcoming)
            for day_offset in range(1, 6):
                future_date = time.strftime("%Y-%m-%d", time.localtime(time.time() + day_offset * 86400))
                if any(p.get("date") == future_date for p in upcoming):
                    continue
                candidates = [p for p in pool if p["question"] not in used_questions]
                if not candidates:
                    candidates = pool
                cand = random.choice(candidates)
                used_questions.add(cand["question"])
                upcoming.append({
                    "id": f"poll_{future_date.replace('-', '')}_{random.randint(100, 999)}",
                    "date": future_date,
                    "question": cand["question"],
                    "options": cand["options"],
                    "formatted_text": format_post_text(cand["question"], cand["options"], is_post=(post_type == "post")),
                    "tag": cand["tag"],
                    "reason": cand["reason"],
                    "game": game_label,
                    "post_type": post_type,
                    "created_at": time.time()
                })
            ch_data["upcoming_polls"] = sorted(upcoming, key=lambda x: x["date"])
            changed = True

    if changed or not data.get("updated_at"):
        data["updated_at"] = time.time()
        _write_data(data)

    return data
