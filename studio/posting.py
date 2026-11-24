"""Post queue: decides when each finished clip goes to which account, posts it, and tracks views."""
import secrets
import threading
import time
from pathlib import Path

import json

from . import accounts, alerts, auth, config, creators, ctx, jobs, live

_lock = threading.RLock()
_FILE = config.DATA_DIR / "posts.json"
try:
    _posts: list = json.loads(_FILE.read_text(encoding="utf-8")) if _FILE.exists() else []
except ValueError:
    _posts = []


def _mine() -> list:
    u = ctx.uid()
    return [p for p in _posts if p.get("user") == u]
OPEN = ("scheduled", "posting")


def _save():
    with _lock:
        tmp = _FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_posts, indent=1), encoding="utf-8")
        tmp.replace(_FILE)


def forget(uid: str):
    """Drop everything queued or recorded for a deleted account, so nothing is posted or retried for it any more."""
    with _lock:
        for p in _posts:
            if p.get("user") == uid:
                p["status"] = "deleted"          # the posting loop may be holding this one already: it then skips it
        _posts[:] = [p for p in _posts if p.get("user") != uid]
        _save()


def _day(t):
    return time.strftime("%Y-%m-%d", time.localtime(t))


def _mirror(p):
    """Copy the post's state onto its clip so the clip card can show it.
    Cleans up any stale/deleted posts so the card NEVER displays duplicate or ghost badges."""
    job = jobs.get(p["job"])
    clip = next((c for c in job["clips"] if c["id"] == p["clip"]), None) if job else None
    if clip is not None:
        valid_ids = {post["id"] for post in _posts if post.get("job") == p["job"] and post.get("clip") == p["clip"]}
        clip_posts = {pid: v for pid, v in clip.get("posts", {}).items() if pid in valid_ids}
        clip_posts[p["id"]] = {k: p.get(k) for k in ("account_name", "platform", "status", "url", "due_at", "error")}
        clip["posts"] = clip_posts
        jobs.save(job)


DAILY_SLOTS = [(7, 30), (11, 30), (15, 30), (19, 30)]
DAILY_CAP = 4
DAILY_YOUTUBE_UPLOADS = 12


def is_gta_clip(title: str) -> bool:
    t = (title or "").upper()
    return any(k in t for k in ["GTA", "GRAND THEFT AUTO", "VICE CITY", "LOS SANTOS", "TREVOR", "FRANKLIN", "MICHAEL", "SUPERCAR", "BIGFOOT", "CRAB", "YACHT", "STUNT", "CAR", "ESCAPE", "TRUCK"])


def _is_slot_occupied(account_id: str, slot_timestamp: float, margin_seconds: int = 60 * 60) -> bool:
    """Checks whether an existing post is already scheduled or posted for this slot on this specific account."""
    for p in _posts:
        if p.get("account") != account_id:
            continue
        if p.get("status") not in OPEN + ("posted",):
            continue
        pub_t = p.get("target_publish_at") or p.get("scheduled_publish_at") or p.get("due_at", 0)
        if abs(pub_t - slot_timestamp) <= margin_seconds:
            return True
    return False


def _next_slot(account_id, gap_min=105, cap=4):
    """Strict Per-Day Fixed Slot Allocator (07:30 AM to 07:30 PM):
    4 fixed daily slots:
      Slot 1: 07:30 AM (7, 30)
      Slot 2: 11:30 AM (11, 30)
      Slot 3: 03:30 PM (15, 30)
      Slot 4: 07:30 PM (19, 30)

    1. Checks dates sequentially starting from today onwards.
    2. If Date D ALREADY has >= cap (4) posts for this account, it is 100% full; skip to Date D+1.
    3. Any occupied slot on this account is SKIPPED to prevent duplicate/same-time scheduling.
    4. If target_date is today and a slot's time has already passed in real-time, it is skipped.
    5. Picks the FIRST unoccupied valid slot.
    6. Only when all 4 slots for Date D are completely full does it move to Date D+1.
    """
    import datetime
    now = time.time()
    now_lt = time.localtime(now)
    start_date = datetime.date(now_lt.tm_year, now_lt.tm_mon, now_lt.tm_mday)

    for offset in range(30):
        target_date = start_date + datetime.timedelta(days=offset)
        date_str = target_date.strftime("%Y-%m-%d")

        # Find all posts currently scheduled or posted for this date FOR THIS ACCOUNT
        day_posts = [p for p in _posts if p.get("account") == account_id
                     and _day(p.get("target_publish_at") or p.get("scheduled_publish_at") or p.get("due_at", 0)) == date_str
                     and p.get("status") in OPEN + ("posted",)]

        # STRICT GATE: If this date already has >= cap (4) posts, this day is 100% full! Move to next date!
        if len(day_posts) >= cap:
            continue

        for h, m in DAILY_SLOTS:
            slot_t = time.mktime((target_date.year, target_date.month, target_date.day, h, m, 0, 0, 0, -1))
            # If today and slot time has already passed in real-time (with 2m buffer), cannot schedule in past
            if target_date == start_date and slot_t <= now + 120:
                continue
            # If slot is already occupied by an existing post on this account, skip it
            if _is_slot_occupied(account_id, slot_t):
                continue
            return slot_t

    return now + 86400 * 30


def _update_schedule_state():
    """Maintains a persistent record of scheduled dates and times in data/schedule_state.json for all channels."""
    state_file = config.DATA_DIR / "schedule_state.json"
    try:
        now = time.time()
        import datetime
        uid = auth.local_user().id
        with ctx.as_user(uid):
            all_accounts = accounts._all()
            yt_accs = [a for a in all_accounts if a.get("platform") == "youtube"]

        accounts_data = {}
        for acc in yt_accs:
            aid = acc["id"]
            aname = acc.get("name", aid)
            acc_posts = [p for p in _posts if p.get("account") == aid and p.get("status") in OPEN + ("posted",)]
            acc_posts.sort(key=lambda p: p.get("target_publish_at") or p.get("scheduled_publish_at") or p.get("due_at", 0))
            last_p = acc_posts[-1] if acc_posts else None
            last_slot_str = None
            if last_p:
                last_t = last_p.get("target_publish_at") or last_p.get("scheduled_publish_at") or last_p.get("due_at", 0)
                last_slot_str = datetime.datetime.fromtimestamp(last_t).strftime("%Y-%m-%d %I:%M %p")
            next_t = _next_slot(aid, cap=DAILY_CAP)
            next_slot_str = datetime.datetime.fromtimestamp(next_t).strftime("%Y-%m-%d %I:%M %p")
            accounts_data[aname] = {
                "account_id": aid,
                "last_scheduled_slot": last_slot_str,
                "last_scheduled_title": last_p.get("title") if last_p else None,
                "next_available_slot": next_slot_str,
                "scheduled_count": len(acc_posts),
            }

        now_lt = time.localtime(now)
        start_date = datetime.date(now_lt.tm_year, now_lt.tm_mon, now_lt.tm_mday)
        buffer_start = start_date.strftime("%Y-%m-%d (%A)")
        buffer_end = (start_date + datetime.timedelta(days=3)).strftime("%Y-%m-%d (%A)")

        state = {
            "daily_cap": DAILY_CAP,
            "window": "07:30 AM to 07:30 PM",
            "rolling_buffer_rule": "Rolling 4-Day Buffer: Today + 3 days ahead (e.g. Mon -> Thu). As days advance, past day publishes and next day stocks up.",
            "current_buffer_window": f"{buffer_start} to {buffer_end}",
            "slots": [
                {"slot": 1, "time": "07:30 AM", "hour": 7, "minute": 30},
                {"slot": 2, "time": "11:30 AM", "hour": 11, "minute": 30},
                {"slot": 3, "time": "03:30 PM", "hour": 15, "minute": 30},
                {"slot": 4, "time": "07:30 PM", "hour": 19, "minute": 30}
            ],
            "channels": accounts_data,
            "updated_at": now,
        }
        state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        return state
    except Exception:
        return {}


def _delete_clip_files(job, clip):
    """Delete all file artifacts (.mp4, .jpg, .ass, .src.mp4) of an unused or discarded clip."""
    try:
        d = Path(job["dir"]) / "clips"
        cid = clip["id"]
        for ext in ("mp4", "jpg", "ass", "src.mp4"):
            (d / f"{cid}.{ext}").unlink(missing_ok=True)
    except Exception:
        pass


def enqueue(job, clip, account_ids, now=False) -> list:
    c = creators.get(job["creator"]) or creators.DEFAULTS
    threshold = max(90, int(c.get("post_min_score", 90)))
    score = int(clip.get("score") or 0)
    if score < threshold:
        return []
    made = []
    with _lock:
        for aid in account_ids:
            acc = accounts.get(aid)
            if not acc:
                continue
            if any(p["job"] == job["id"] and p["clip"] == clip["id"] and p["account"] == aid
                   and p["status"] in OPEN + ("posted",) for p in _mine()):
                continue  # never post the same clip to the same account twice
            due = time.time() if now else _next_slot(aid, c["post_gap_min"], c["daily_cap"])
            p = {"id": secrets.token_hex(8), "user": ctx.uid(), "job": job["id"], "clip": clip["id"], "creator": job["creator"],
                 "account": aid, "account_name": acc["name"], "platform": acc["platform"], "status": "scheduled",
                 "due_at": due, "target_publish_at": due, "scheduled_publish_at": due, "created": time.time(),
                 "title": clip.get("title") or clip.get("hook") or "Clip",
                 "score": clip.get("score", 0), "tries": 0, "url": "", "remote_id": "", "error": None, "views": None}
            _posts.append(p)
            made.append(p)
        _save()
    for p in made:
        _mirror(p)
    return made


def on_clip_ready(job, clip):
    """Called once when a single clip finishes rendering.
    Logs progress; waits until ALL clips for the video job are rendered before assigning timing slots."""
    c = creators.get(job["creator"])
    score = int(clip.get("score") or 0)
    if c:
        creators.log(c["id"], f"Clip rendered ({score}% virality): {clip.get('title') or clip.get('hook')}")


def on_job_done(job):
    """Called once when ALL clips for a video job finish rendering.
    Enforces user rule: PeakCut par saare clips banne ke baad hi unpar timing assign karna!
    1. Collects all clips that finished rendering (status='done') with score >= 90%.
    2. Sorts them by virality score descending.
    3. Discards & cleans files of clips < 90%.
    4. For top clips (up to DAILY_CAP=4), sequentially determines and assigns the next free daily slots
       (07:30 AM, 11:30 AM, 03:30 PM, 07:30 PM).
    5. Saves the assigned posts and schedule state tracker.
    6. Triggers YouTube cloud-scheduling.
    """
    c = creators.get(job["creator"])
    if not c or not c.get("autopost") or not c.get("accounts"):
        return

    threshold = max(90, int(c.get("post_min_score", 90)))
    raw_clips = list(job.get("clips", []))

    qualified = []
    for clip in raw_clips:
        score = int(clip.get("score") or 0)
        if clip.get("status") == "done" and score >= threshold:
            qualified.append(clip)
        else:
            _delete_clip_files(job, clip)
            clip.update(status="discarded", file="", cover="")
    jobs.save(job)

    if not qualified:
        creators.log(c["id"], f"No clips qualified (>= {threshold}% score) from: {job.get('title')}")
        return

    # Sort highest virality score first
    qualified.sort(key=lambda x: int(x.get("score") or 0), reverse=True)

    # Only take up to DAILY_CAP (4) clips per day batch
    cap = int(c.get("daily_cap", DAILY_CAP))
    to_schedule = qualified[:cap]
    for extra in qualified[cap:]:
        _delete_clip_files(job, extra)
        extra.update(status="discarded", file="", cover="")
    jobs.save(job)

    # Batch-assign timing slots now!
    import datetime
    with _lock:
        for aid in c["accounts"]:
            acc = accounts.get(aid)
            if not acc:
                continue
            for clip in to_schedule:
                if any(p.get("job") == job["id"] and p.get("clip") == clip["id"] and p.get("account") == aid for p in _posts):
                    continue
                slot_t = _next_slot(aid, c["post_gap_min"], cap=cap)
                dt_str = datetime.datetime.fromtimestamp(slot_t).strftime("%Y-%m-%d %I:%M %p")
                p = {
                    "id": secrets.token_hex(8), "user": job.get("user", "u_local"), "job": job["id"],
                    "clip": clip["id"], "creator": job["creator"], "account": aid,
                    "account_name": acc["name"], "platform": acc["platform"], "status": "scheduled",
                    "due_at": slot_t, "target_publish_at": slot_t, "scheduled_publish_at": slot_t,
                    "created": time.time(), "title": clip.get("title") or clip.get("hook") or "Clip",
                    "score": clip.get("score", 0), "tries": 0, "url": "", "remote_id": "", "error": None, "views": None
                }
                _posts.append(p)
                _mirror(p)
                creators.log(c["id"], f"Assigned timing {dt_str} (Score {p['score']}): {p['title']}")
        _save()
    _update_schedule_state()

    # Immediately trigger YouTube Cloud-Scheduling for assigned posts
    for post in list(_posts):
        if post.get("status") == "scheduled" and post.get("job") == job["id"] and not post.get("remote_id"):
            try:
                with ctx.as_user(post.get("user", "u_local")):
                    with _lock:
                        if post.get("status") != "scheduled" or post.get("remote_id"):
                            continue
                        post["status"] = "posting"
                    _post(post)
            except Exception as pe:
                print(f"[Posting] Cloud-schedule immediate trigger note: {pe}", flush=True)


def _caption(clip, creator):
    tags = " ".join("#" + h for h in clip.get("hashtags", []))
    return "\n\n".join(x for x in (clip.get("description", ""), tags, creator.get("post_footer", "")) if x)


def _post(p):
    job = jobs.get(p["job"])
    clip = next((c for c in job["clips"] if c["id"] == p["clip"]), None) if job else None
    acc = accounts.get(p["account"])
    try:
        if not clip or clip.get("status") != "done":
            raise RuntimeError("The clip no longer exists or is not finished.")
        if not acc:
            raise RuntimeError("That account was disconnected.")
        # HARD SAFETY GATE: Never upload anything with score below 90%
        score = int(clip.get("score") or p.get("score") or 0)
        if score < 90:
            p.update(status="cancelled", error="Cancelled: virality score below 90% threshold.")
            _delete_clip_files(job, clip)
            clip.update(status="discarded", file="", cover="")
            jobs.save(job)
            creators.log(p["creator"], f"Skipped & deleted unused clip: score {score} < 90% threshold")
            _save()
            _mirror(p)
            return
        path = Path(job["dir"]) / "clips" / clip["file"]
        creator = creators.get(job["creator"]) or dict(creators.DEFAULTS, name="clips")
        title, cap = clip.get("title") or clip.get("hook") or "Clip", _caption(clip, creator)
        target_slot = p.get("target_publish_at") or p.get("scheduled_publish_at") or p.get("due_at")
        publish_time = target_slot if target_slot and target_slot > time.time() + 60 else None
        if acc["platform"] == "youtube":
            rid, url = accounts.youtube_upload(acc, path, title, cap + "\n\n#Shorts", clip.get("hashtags", []), creator["privacy"], publish_at=publish_time)
            if rid:
                try:
                    game_type = creator.get("game", "")
                    comment_text = accounts.generate_engaging_comment(title, game_type)
                    accounts.youtube_post_comment(acc, rid, comment_text)
                    p["comment"] = comment_text
                except Exception as ce:
                    try:
                        from . import healer
                        healer.record_pending_comment(rid, comment_text)
                    except Exception:
                        pass
                    print(f"[YouTube] Note on comment posting (queued for when video goes live): {ce}", flush=True)
        elif acc["platform"] == "instagram":
            rid, url = accounts.instagram_upload(acc, path, f"{title}\n\n{cap}", job["id"])
        elif acc["platform"] == "tiktok":
            rid, url = accounts.tiktok_upload(acc, path, f"{title}\n\n{cap}", creator["privacy"])
        else:
            raise RuntimeError("That kind of account is no longer supported.")
        p.update(status="posted", posted_at=time.time(), remote_id=rid, url=url, error=None, scheduled_publish_at=publish_time, target_publish_at=target_slot)
        if publish_time:
            dt_str = time.strftime('%Y-%m-%d %H:%M', time.localtime(publish_time))
            creators.log(p["creator"], f"Cloud-Scheduled on {acc['name']} for {dt_str}: {title}")
        else:
            creators.log(p["creator"], f"Posted to {acc['name']}: {title}")
        # Large .mp4 video is now permanently published/scheduled on YouTube Shorts: clean up local mp4 to save disk space
        try:
            path.unlink(missing_ok=True)
            path.with_suffix(".ass").unlink(missing_ok=True)
        except Exception:
            pass
    except Exception as e:  # noqa: BLE001
        p["tries"] += 1
        p["error"] = str(e)[-300:]
        err_str = str(e).lower()
        if "getaddrinfo" in err_str or "connection" in err_str or "timeout" in err_str or "temporary" in err_str:
            # Network hiccup: keep scheduled and retry upload in 3 minutes without corrupting target slot time!
            p.update(status="scheduled", retry_at=time.time() + 180)
            creators.log(p["creator"], f"Network connection retry for {p['account_name']}: {p['error'][:100]}")
        elif "quotaexceeded" in err_str or "rate limit" in err_str:
            # Daily quota limit: resume in 1 hour without corrupting target slot time!
            p.update(status="scheduled", retry_at=time.time() + 3600)
            creators.log(p["creator"], f"YouTube daily quota limit reached, will resume in 1h: {title}")
        elif p["tries"] < 5 and clip and acc:
            p.update(status="scheduled", retry_at=time.time() + 300)
        else:
            p["status"] = "failed"
            creators.log(p["creator"], f"Post failed on {p['account_name']}: {p['error']}")
            alerts.notify(f"post failed on {p['account_name']} ({p['title']}): {p['error'][:200]}", kind="post")
    _save()
    _mirror(p)


DAILY_YOUTUBE_UPLOADS = 12


def _yt_uploaded_today_count(account_id=None):
    today = time.strftime("%Y-%m-%d")
    count = 0
    for p in _posts:
        if p.get("platform") == "youtube" and p.get("remote_id"):
            if account_id and p.get("account") != account_id:
                continue
            post_t = p.get("uploaded_at") or p.get("posted_at") or 0
            if _day(post_t) == today:
                count += 1
    return count


def _run():
    while True:
        time.sleep(15)
        now = time.time()
        # Immediate due posts (where retry timer has passed AND due_at has arrived)
        due = [p for p in list(_posts) if p.get("status") == "scheduled" and p.get("user") and not p.get("remote_id")
               and p.get("retry_at", 0) <= now and p.get("due_at", 0) <= now]

        # Native Cloud-Scheduling: Pick advance-scheduled YouTube posts up to 4 per channel per day
        advance = [p for p in list(_posts) if p.get("status") == "scheduled" and p.get("platform") == "youtube"
                   and p.get("user") and not p.get("remote_id") and p.get("score", 0) >= 90
                   and p.get("retry_at", 0) <= now]
        advance.sort(key=lambda p: p.get("target_publish_at") or p.get("due_at", 0))
        for p in advance:
            aid = p.get("account")
            ch_today = _yt_uploaded_today_count(aid)
            ch_due = sum(1 for x in due if x.get("account") == aid)
            if p not in due and (ch_today + ch_due) < DAILY_YOUTUBE_UPLOADS:
                due.append(p)

        for p in due:
            try:
                with ctx.as_user(p["user"]):
                    if accounts.settings()["paused"]:
                        continue
                    with _lock:
                        if p.get("status") != "scheduled" or p.get("remote_id"):
                            continue
                        p["status"] = "posting"
                    _post(p)
            except Exception:  # noqa: BLE001
                pass


def _stats_for(recent):
    for aid in {p["account"] for p in recent if p["platform"] == "youtube"}:
        acc = accounts.get(aid)
        if acc:
            views = accounts.youtube_views(acc, [p["remote_id"] for p in recent if p["account"] == aid])
            for p in recent:
                if p["account"] == aid and p["remote_id"] in views:
                    p["views"] = views[p["remote_id"]]
    for p in recent:
        acc = accounts.get(p["account"]) if p["platform"] == "instagram" else None
        if acc:
            v = accounts.instagram_views(acc, p["remote_id"])
            if v is not None:
                p["views"] = v


def _stats():
    while True:
        for uid in {p.get("user") for p in _posts if p.get("user")}:
            try:
                with ctx.as_user(uid):
                    _stats_for([p for p in _mine() if p["status"] == "posted" and time.time() - p["posted_at"] < 14 * 86400])
            except Exception:  # noqa: BLE001
                pass
        _save()
        time.sleep(1800)


def cancel(pid):
    with _lock:
        p = next((p for p in _mine() if p["id"] == pid), None)
        if not p or p["status"] != "scheduled":
            raise ValueError("Only posts that are still waiting can be cancelled.")
        p["status"] = "cancelled"
        job = jobs.get(p["job"])
        if job:
            clip = next((c for c in job["clips"] if c["id"] == p["clip"]), None)
            if clip:
                _delete_clip_files(job, clip)
                clip.update(status="discarded", file="", cover="")
                jobs.save(job)
        _save()
    _mirror(p)


def retry(pid):
    with _lock:
        p = next((p for p in _mine() if p["id"] == pid), None)
        if not p or p["status"] not in ("failed", "cancelled"):
            raise ValueError("Only failed or cancelled posts can be retried.")
        p.update(status="scheduled", due_at=time.time(), tries=0, error=None)
        _save()
    _mirror(p)


def overview() -> dict:
    today = _day(time.time())
    _posts = _mine()  # everything below is about the signed-in user only
    rows = []
    all_cs, all_js = creators.all_creators(), jobs.list_jobs()
    for c in all_cs:
        js = [j for j in all_js if j["creator"] == c["id"]]
        clips = [k for j in js for k in j["clips"] if k.get("status") == "done"]
        ps = [p for p in _posts if p["creator"] == c["id"]]
        views = sum(p["views"] or 0 for p in ps)
        rows.append({
            "id": c["id"], "name": c["name"],
            "state": "recording" if live.active_for(c["id"]) else "waiting" if c["auto_watch"] and c["channel"] else "idle",
            "autopost": c["autopost"] and bool(c["accounts"]),
            "clips_today": sum(1 for k in clips if _day(k.get("made_at", 0)) == today), "clips_total": len(clips),
            "posted_today": sum(1 for p in ps if p["status"] == "posted" and _day(p["posted_at"]) == today),
            "posted_total": sum(1 for p in ps if p["status"] == "posted"),
            "queued": sum(1 for p in ps if p["status"] in OPEN), "failed": sum(1 for p in ps if p["status"] == "failed"),
            "views": views, "earnings": round(views / 1000 * c["rate_per_1k"], 2)})
    names = {c["id"]: c["name"] for c in all_cs}
    queue = sorted((p for p in _posts if p["status"] in OPEN), key=lambda p: p["due_at"])
    done = sorted((p for p in _posts if p["status"] in ("posted", "failed")), key=lambda p: -(p.get("posted_at") or p["due_at"]))[:15]
    show = [{**{k: v for k, v in p.items() if k != "user"}, "creator_name": names.get(p["creator"], "")} for p in queue + done]
    # last 14 days, oldest first, for the home graph
    days = [_day(time.time() - 86400 * i) for i in range(13, -1, -1)]
    made, posted, views = {d: 0 for d in days}, {d: 0 for d in days}, {d: 0 for d in days}
    for j in all_js:
        for k in j["clips"]:
            d = _day(k.get("made_at") or j["created"])
            if k.get("status") == "done" and d in made:
                made[d] += 1
    for p in _posts:
        if p["status"] == "posted" and _day(p["posted_at"]) in posted:
            posted[_day(p["posted_at"])] += 1
            views[_day(p["posted_at"])] += p["views"] or 0
    daily = [{"day": d, "clips": made[d], "posted": posted[d], "views": views[d]} for d in days]
    return {"paused": accounts.settings()["paused"], "creators": rows, "posts": show, "daily": daily,
            "alerts": alerts.configured(), "recording": len(live.active_mine())}


def digest_text() -> str:
    o = overview()
    lines = ["PeakCut daily summary"]
    for r in o["creators"]:
        lines.append(f"- {r['name']}: {r['state']}, {r['clips_today']} clips today, {r['posted_today']} posted today, "
                     f"{r['queued']} waiting, {r['failed']} failed, {r['views']} views")
    if not o["creators"]:
        lines.append("No creators set up yet.")
    if o["paused"]:
        lines.append("Posting is paused.")
    return "\n".join(lines)


def start():
    with _lock:
        for p in _posts:
            if p["status"] == "posting":  # interrupted mid-upload last time
                p.update(status="failed", error="The studio closed during this upload. Check the account, then retry.")
        _save()
    jobs.on_clip_ready.append(on_clip_ready)
    jobs.on_job_done.append(on_job_done)
    _update_schedule_state()
    threading.Thread(target=_run, daemon=True).start()
    threading.Thread(target=_stats, daemon=True).start()
    threading.Thread(target=alerts.digest_loop, args=(digest_text,), daemon=True).start()
    threading.Thread(target=alerts.keep_awake_loop,
                     args=(lambda: bool(live.active()) or any(p["status"] in OPEN for p in _posts),), daemon=True).start()
