"""Autonomous Self-Healing and Monitoring Engine for PeakCut.
Continuously runs background checks to detect, heal, and auto-recover from any:
- Failed / stalled jobs
- Windows file locks (WinError 32)
- Zero-clip drops
- Channel buffer gaps
- Delayed comment posting for scheduled videos
- Disk bloat
"""
import json
import logging
import math
import shutil
import threading
import time
from pathlib import Path

from . import accounts, auth, config, ctx, jobs, posting

log = logging.getLogger("peakcut.healer")

_RUNNING = False
_THREAD = None
_CHECK_INTERVAL = 25  # Run self-healing loop every 25 seconds


def _read_posts():
    pfile = config.DATA_DIR / "posts.json"
    if pfile.exists():
        try:
            return json.loads(pfile.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def heal_failed_jobs():
    """Scans all video jobs. If a job crashed, self-heals it or discards and queues the next video."""
    base_users = config.DATA_DIR / "users"
    if not base_users.exists():
        return

    from . import autopilot

    ap_state = autopilot._read_state()
    processed_ids = set(ap_state.get("processed_ids", []))
    needs_requeue = False

    for jpath in base_users.glob("*/creators/*/videos/*/job.json"):
        try:
            jdir = jpath.parent
            job = json.loads(jpath.read_text(encoding="utf-8"))
            status = job.get("status")
            err_text = str(job.get("error") or "")

            if status == "error":
                vid_url = job.get("source", {}).get("url", "")
                vid_id = ""
                if "v=" in vid_url:
                    vid_id = vid_url.split("v=")[1].split("&")[0]

                # Check if it was a file lock error on Windows and audio/source part exists
                part_files = list(jdir.glob("*.part"))
                has_words = (jdir / "words.json").exists()
                has_audio = any(p.exists() and p.stat().st_size > 50000 for p in jdir.glob("audio_src*") if not p.name.endswith(".part"))

                if part_files or has_words or has_audio:
                    # Attempt to rescue part files
                    rescued = False
                    for pf in part_files:
                        target = pf.with_name(pf.name[:-5])
                        for _ in range(5):
                            try:
                                time.sleep(0.3)
                                if target.exists():
                                    target.unlink(missing_ok=True)
                                pf.rename(target)
                                rescued = True
                                break
                            except Exception:
                                pass

                    if rescued or has_audio or has_words:
                        print(f"[Self-Healer] Rescued locked media in job {job.get('id')}. Re-enqueuing job...", flush=True)
                        job["status"] = "queued"
                        job["error"] = None
                        job["stage"] = "Auto-healed and re-queued"
                        jobs.save(job)
                        jobs.enqueue(job)
                        continue

                # If unrecoverable download / bot-check error, cleanly discard and queue next viral video
                print(f"[Self-Healer] Job {job.get('id')} failed with: {err_text[:80]}. Cleaning up & advancing candidate...", flush=True)
                if vid_id:
                    processed_ids.add(vid_id)
                    ap_state["processed_ids"] = list(processed_ids)
                    autopilot._save_state(ap_state)

                try:
                    shutil.rmtree(jdir, ignore_errors=True)
                    if job.get("id") in jobs._jobs:
                        del jobs._jobs[job.get("id")]
                except Exception:
                    pass
                needs_requeue = True

        except Exception as e:
            log.warning(f"Error inspecting job {jpath}: {e}")

    if needs_requeue and jobs.busy_count() == 0:
        print("[Self-Healer] Triggering fresh candidate queue after self-healing failed job.", flush=True)
        autopilot.check_and_queue()


def heal_stuck_jobs():
    """Detects jobs stuck with zero progress for > 10 minutes and restarts or clears them."""
    base_users = config.DATA_DIR / "users"
    if not base_users.exists():
        return

    now = time.time()
    for jpath in base_users.glob("*/creators/*/videos/*/job.json"):
        try:
            mtime = jpath.stat().st_mtime
            job = json.loads(jpath.read_text(encoding="utf-8"))
            status = job.get("status")

            if status in ("running", "downloading", "cutting") and (now - mtime > 600):
                print(f"[Self-Healer] Job {job.get('id')} stuck for >10 mins in stage '{job.get('stage')}'. Auto-recovering...", flush=True)
                # Re-queue the job to pick up from where it left off
                job["status"] = "queued"
                job["stage"] = "Recovered from freeze, resuming..."
                jobs.save(job)
                jobs.enqueue(job)
        except Exception:
            pass


def heal_channel_buffers():
    """Ensures buffer health: whenever busy_count is 0, checks if either channel needs clips and queues immediately."""
    if jobs.busy_count() > 0:
        return

    from . import autopilot

    posts = _read_posts()
    gta_full = autopilot.is_account_buffer_full(autopilot.GTA_ACCOUNT_ID, posts)
    ff_full = autopilot.is_account_buffer_full(autopilot.FREEFIRE_ACCOUNT_ID, posts)

    if not gta_full or not ff_full:
        autopilot.check_and_queue()


def retry_pending_comments():
    """Checks scheduled YouTube videos. When publish time has arrived, posts the creator pinned comment."""
    posts = _read_posts()
    now = time.time()
    pending_file = config.DATA_DIR / "pending_comments.json"
    pending = {}
    if pending_file.exists():
        try:
            pending = json.loads(pending_file.read_text(encoding="utf-8"))
        except Exception:
            pending = {}

    # Check existing posts that are now public
    updated = False
    for p in posts:
        pid = p.get("id")
        video_url = p.get("url") or ""
        due_at = p.get("due_at", 0)

        if not pid or "youtube.com/shorts/" not in video_url:
            continue

        yt_id = video_url.split("/shorts/")[1].split("?")[0].split("/")[0]

        # If comment is pending and due_at has passed (video is now public!)
        if yt_id in pending and due_at <= now:
            comment_text = pending[yt_id]
            acct_id = p.get("account_id") or p.get("account")
            try:
                print(f"[Self-Healer] Posting scheduled creator comment for now-live video {yt_id}...", flush=True)
                accounts.youtube_post_comment(acct_id, yt_id, comment_text)
                del pending[yt_id]
                updated = True
            except Exception as e:
                print(f"[Self-Healer] Comment retry for {yt_id} failed: {e}", flush=True)

    if updated:
        pending_file.write_text(json.dumps(pending, indent=1), encoding="utf-8")


def heal_disk_cleanup():
    """Ensures zero MB disk waste by deleting local .mp4 files once scheduled or posted."""
    posts = _read_posts()
    scheduled_clips = set()
    for p in posts:
        if p.get("status") in ("scheduled", "posted", "posting") and p.get("clip"):
            scheduled_clips.add(p["clip"])

    base_users = config.DATA_DIR / "users"
    if not base_users.exists():
        return

    for cpath in base_users.glob("*/creators/*/videos/*/clips/*/output.mp4"):
        clip_id = cpath.parent.name
        if clip_id in scheduled_clips:
            try:
                cpath.unlink(missing_ok=True)
            except Exception:
                pass


def record_pending_comment(youtube_id: str, comment_text: str):
    """Registers a comment to be posted once the video's scheduled publish time arrives."""
    pending_file = config.DATA_DIR / "pending_comments.json"
    pending = {}
    if pending_file.exists():
        try:
            pending = json.loads(pending_file.read_text(encoding="utf-8"))
        except Exception:
            pending = {}
    pending[youtube_id] = comment_text
    pending_file.write_text(json.dumps(pending, indent=1), encoding="utf-8")


def _worker_loop():
    time.sleep(5)
    while _RUNNING:
        try:
            uid = auth.local_user().id
            with ctx.as_user(uid):
                heal_failed_jobs()
                heal_stuck_jobs()
                heal_channel_buffers()
                retry_pending_comments()
                heal_disk_cleanup()
        except Exception as e:
            log.warning(f"[Self-Healer] Worker cycle error: {e}")
        time.sleep(_CHECK_INTERVAL)


def start():
    """Starts the Self-Healing background watchdog daemon."""
    global _RUNNING, _THREAD
    if _RUNNING:
        return
    _RUNNING = True
    _THREAD = threading.Thread(target=_worker_loop, daemon=True, name="self-healer")
    _THREAD.start()
    print("[Self-Healer] Autonomous Self-Healing & Health Check Engine started.", flush=True)
