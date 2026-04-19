import os
import json
import time
import signal
import shutil
import requests
import subprocess
import redis
import boto3
import psycopg2
from botocore.client import Config
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
DATABASE_URL = os.environ.get("DATABASE_URL")
N8N_CALLBACK_URL = os.environ.get("N8N_CALLBACK_URL")
TEMP_DIR = os.environ.get("TEMP_DIR", "/tmp/reels")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
REPLICATE_API_TOKEN = os.environ.get("REPLICATE_API_TOKEN")

B2_ENDPOINT = os.environ.get("B2_ENDPOINT")
B2_APPLICATION_KEY_ID = os.environ.get("B2_APPLICATION_KEY_ID")
B2_APPLICATION_KEY = os.environ.get("B2_APPLICATION_KEY")
B2_BUCKET_NAME = os.environ.get("B2_BUCKET_NAME")

def _b2_region():
    """Extract region from B2 endpoint: s3.us-east-005.backblazeb2.com -> us-east-005"""
    if not B2_ENDPOINT:
        return "us-east-1"
    host = B2_ENDPOINT.replace("https://", "").replace("http://", "").split("/")[0]
    parts = host.split(".")
    # format: s3.<region>.backblazeb2.com
    return parts[1] if len(parts) >= 4 else "us-east-1"

# Background music: set to B2 base URL, e.g. https://s3.us-west-000.backblazeb2.com/my-bucket/music
# Expects ambient.mp3 / upbeat.mp3 / cinematic.mp3 uploaded to that prefix.
BACKGROUND_MUSIC_BASE_URL = os.environ.get("BACKGROUND_MUSIC_BASE_URL")
# Text watermark burned into top-right corner of every video.
BRAND_NAME = os.environ.get("BRAND_NAME")

QUEUE_NAME = "reels:jobs"
VIDEO_FPS = 25
CROSSFADE_DURATION = 0.4  # seconds overlap between scenes

# Fonts — installed via Dockerfile. Use ``fontfile`` in drawtext rather than
# ``font`` + ``style`` because FFmpeg drawtext doesn't have a ``style`` option
# and ``font`` relies on fontconfig lookup which is fragile inside containers.
CAPTION_FONT   = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
WATERMARK_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

try:
    r = redis.from_url(REDIS_URL)
    print(f"Connected to Redis at {REDIS_URL.split('@')[-1] if '@' in REDIS_URL else REDIS_URL}")
except Exception as e:
    print(f"Failed to connect to Redis: {e}")
    exit(1)

Path(TEMP_DIR).mkdir(parents=True, exist_ok=True)

# Graceful shutdown: Render sends SIGTERM before killing the container.
# Finish the current job, then exit cleanly instead of mid-render termination.
_shutdown = False

def _handle_sigterm(signum, frame):
    global _shutdown
    print("SIGTERM received — will exit after current job completes.")
    _shutdown = True

signal.signal(signal.SIGTERM, _handle_sigterm)


# ---------------------------------------------------------------------------
# Text helpers for captions and watermarks
# ---------------------------------------------------------------------------

def _wrap_text(text: str, max_chars: int = 22) -> str:
    """Hard-wrap at word boundaries. Tighter wrap (22 chars) for large font on 1080px."""
    words = text.split()
    lines, line = [], []
    for w in words:
        if sum(len(x) for x in line) + len(line) + len(w) > max_chars and line:
            lines.append(" ".join(line))
            line = []
        line.append(w)
    if line:
        lines.append(" ".join(line))
    return r"\n".join(lines)


def _esc(s: str) -> str:
    """Escape for FFmpeg drawtext filter_complex values.

    Escaping layers (inner → outer):
      1. Backslash must be doubled first (FFmpeg filter string level).
      2. Colon is the option separator — escape as \\:.
      3. Single quotes inside text='...' cannot be escaped as \\' within the
         quoted value — replace with U+2019 (right single quotation mark,
         visually identical). This prevents the parser from closing the
         text= value early and corrupting the filter chain.
    """
    return (
        s.replace("\\", "\\\\")
         .replace("'", "\u2019")
         .replace(":", "\\:")
    )


# ---------------------------------------------------------------------------
# Music selection
# ---------------------------------------------------------------------------

def _select_music_key(topic: str) -> str:
    """Return filename for background music track based on topic keywords."""
    t = topic.lower()
    if any(w in t for w in ["luxury", "premium", "penthouse", "villa", "mansion"]):
        return "cinematic.mp3"
    if any(w in t for w in ["studio", "compact", "affordable", "budget"]):
        return "upbeat.mp3"
    return "ambient.mp3"


# ---------------------------------------------------------------------------
# FFmpeg helpers
# ---------------------------------------------------------------------------

def get_duration(filepath):
    """Return media duration in seconds using ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            filepath,
        ],
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def make_scene_video(img_path, audio_path, output_path, voiceover_text=None):
    """
    Render a single scene video:
      • Prescale to 1.5× target (1620×2880) for lossless Ken Burns headroom
      • Ken Burns: smooth slow push-in with eased start via min() clamp
      • EBU R128 loudnorm at -16 LUFS for broadcast-safe audio
      • Caption: word-wrapped, large semi-bold text with gradient scrim
        and subtle drop-shadow for legibility on any background
      • Output: H.264 High Profile, CRF 18 (high quality scene master),
        yuv420p for maximum device compatibility
    """
    duration = min(get_duration(audio_path), 30.0)
    # 0.5 s breathing room after the last word before the crossfade begins
    total_frames = max(int((duration + 0.5) * VIDEO_FPS), 10)

    # ── Prescale ──────────────────────────────────────────────────────────────
    # 1.5× target gives ~33% zoom headroom before hitting the source edge.
    # lanczos is the highest-quality FFmpeg downscale kernel for still imagery.
    prescale = (
        "scale=1620:2880:flags=lanczos"
        ":force_original_aspect_ratio=increase,"
        "crop=1620:2880"
    )

    # ── Ken Burns ─────────────────────────────────────────────────────────────
    # zoom+0.0015 per frame @ 25fps = 3.75%/s — clearly visible, not aggressive.
    # min(zoom,1.5) hard-caps so we never push past the prescaled boundary.
    # The centre-pivot x/y formula keeps the subject locked in frame.
    ken_burns = (
        f"zoompan="
        f"z='min(zoom+0.0015,1.5)':"
        f"d={total_frames}:"
        f"x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':"
        f"s=1080x1920:"
        f"fps={VIDEO_FPS},"
        "setsar=1"
    )

    vf = f"{prescale},{ken_burns}"

    # ── Caption ───────────────────────────────────────────────────────────────
    # Two-layer technique used in broadcast:
    #   Layer 1 — gradient scrim: a semi-transparent black rectangle behind the
    #             text block gives guaranteed contrast regardless of image content.
    #   Layer 2 — drawtext: white text with a tight 2px shadow offset for depth.
    #
    # shadowx/shadowy at (2,2) is the classic broadcast drop-shadow — enough to
    # read on white backgrounds without looking like a black halo.
    # alpha fade-in over first 6 frames (0.24 s) softens the text appearance.
    if voiceover_text:
        caption = _esc(_wrap_text(voiceover_text, max_chars=22))
        # ``fontfile`` is the reliable way to specify weight — FFmpeg drawtext
        # has no ``style`` option; ``font`` alone relies on fontconfig and is
        # fragile in containers. ``line_spacing`` requires FFmpeg ≥ 6.1 (we run 7.1).
        vf += (
            f",drawtext=text='{caption}'"
            f":fontfile={CAPTION_FONT}"
            ":fontcolor=white"
            ":fontsize=62"
            ":shadowcolor=black@0.75"
            ":shadowx=2:shadowy=2"
            ":borderw=0"
            ":line_spacing=8"
            ":x=(w-text_w)/2"
            ":y=h-text_h-180"
            ":expansion=none"
            # Fade the caption in over the first 6 frames (0.24 s) for a clean entry.
            f":alpha='if(lt(n,6),n/6,1)'"
        )

    # ── Audio ─────────────────────────────────────────────────────────────────
    # loudnorm two-pass would be ideal but adds latency; single-pass with
    # dual_mono=true handles mono TTS files correctly.
    audio_filter = (
        f"[1:a]atrim=end={duration},"
        "loudnorm=I=-16:LRA=7:TP=-2:dual_mono=true"
        "[a]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", img_path,
        "-i", audio_path,
        "-filter_complex", f"[0:v]{vf}[v];{audio_filter}",
        "-map", "[v]", "-map", "[a]",
        # CRF 18 = high-quality master for the scene — one re-encode only.
        # Intermediate merges use CRF 15 so quality is preserved without OOM risk.
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-profile:v", "high", "-level", "4.1",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-r", str(VIDEO_FPS),
        "-shortest",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg scene render failed for {output_path}:\n{result.stderr[-3000:]}"
        )
    return output_path


def _xfade_two(clip_a, clip_b, output_path, xfade_dur, final=False):
    """Merge exactly two clips with an xfade crossfade. Low memory: only 2 inputs.

    Intermediate merges use high-quality H.264 (CRF 15, ultrafast) so accumulated
    clips stay small (10-20× smaller than CRF 0) without visible quality loss.
    CRF 0 caused OOM because the 24 s accumulated clip bloated to 300-400 MB and
    FFmpeg's xfade decode buffers pushed the process over the 2 GB memory limit.
    Only the final merge uses the target quality CRF 22.
    """
    dur_a = get_duration(clip_a)
    offset = max(0.0, dur_a - xfade_dur)
    filter_complex = (
        f"[0:v][1:v]xfade=transition=fade:duration={xfade_dur:.3f}:offset={offset:.3f}[xvout];"
        f"[0:a][1:a]acrossfade=d={xfade_dur:.3f}[xaout]"
    )
    if final:
        codec = ["-c:v", "libx264", "-preset", "fast", "-crf", "22",
                 "-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    else:
        # High-quality intermediate (CRF 15): visually near-lossless but 10-20×
        # smaller than CRF 0, preventing OOM during xfade decode of accumulated clips.
        codec = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "15",
                 "-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p"]
    cmd = (
        ["ffmpeg", "-y", "-i", clip_a, "-i", clip_b,
         "-filter_complex", filter_complex,
         "-map", "[xvout]", "-map", "[xaout]"]
        + codec + [output_path]
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg xfade merge failed:\n{result.stderr[-3000:]}")


def concat_with_crossfade(scene_videos, output_path):
    """
    Concatenate scene videos with smooth crossfade dissolves using sequential
    pairwise merges. Each merge uses only 2 inputs at a time, keeping memory
    bounded regardless of scene count (avoids OOM on chained xfade).
    """
    n = len(scene_videos)
    if n == 1:
        shutil.copy(scene_videos[0], output_path)
        return

    durations = [get_duration(v) for v in scene_videos]
    xfade = min(CROSSFADE_DURATION, min(durations) * 0.2)
    tmp_dir = os.path.dirname(output_path)

    # Merge pairs sequentially: (A+B)→tmp, (tmp+C)→tmp2, ...
    current = scene_videos[0]
    for i in range(1, n):
        is_last = (i == n - 1)
        next_out = output_path if is_last else os.path.join(tmp_dir, f"_merge_{i}.mp4")
        print(f"Merging scene {i}/{n-1} into running clip...")
        _xfade_two(current, scene_videos[i], next_out, xfade, final=is_last)
        # Remove intermediate temp file (not the original scene files)
        if current != scene_videos[0] and os.path.exists(current):
            os.remove(current)
        current = next_out


def add_music_and_branding(video_path, output_path, topic="", music_path=None, brand_name=None):
    """
    Single FFmpeg pass over the concatenated video:
    - Mixes background music at -18 dB under voiceover (fades in/out)
    - Burns brand name watermark in top-right corner
    Both are optional: if neither is set, output is a simple copy.
    """
    has_music = bool(music_path and os.path.exists(music_path))
    has_brand = bool(brand_name)

    if not has_music and not has_brand:
        shutil.copy(video_path, output_path)
        return

    inputs = []
    if has_music:
        inputs += ["-stream_loop", "-1", "-i", music_path]
    inputs += ["-i", video_path]

    # Index of the main video input
    vid_idx = 1 if has_music else 0

    filters = []
    # Direct stream refs use no brackets; filter output refs use [label] brackets.
    vmap = f"{vid_idx}:v"
    amap = f"{vid_idx}:a"

    if has_brand:
        brand = _esc(brand_name)
        # Watermark: subtle white@0.6 with a 1px drop shadow — legible but
        # unobtrusive. Top-right at 40px margin matches the Instagram story safe zone.
        filters.append(
            f"[{vid_idx}:v]drawtext=text='{brand}'"
            f":fontfile={WATERMARK_FONT}"
            ":fontcolor=white@0.6"
            ":fontsize=34"
            ":shadowcolor=black@0.5"
            ":shadowx=1:shadowy=1"
            ":borderw=0"
            ":x=w-text_w-40:y=40"
            ":expansion=none[vout]"
        )
        vmap = "[vout]"

    if has_music:
        total = get_duration(video_path)
        fade_start = max(0.0, total - 2.0)
        filters.append(
            f"[0:a]volume=0.12,"
            f"afade=t=in:st=0:d=1,"
            f"afade=t=out:st={fade_start:.2f}:d=2[music];"
            f"[{vid_idx}:a][music]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
        amap = "[aout]"

    cmd = (
        ["ffmpeg", "-y"]
        + inputs
        + (["-filter_complex", ";".join(filters)] if filters else [])
        + ["-map", vmap, "-map", amap]
        + ["-c:v", "libx264", "-preset", "fast", "-crf", "22"]
        + ["-c:a", "aac", "-b:a", "192k"]
        + ["-pix_fmt", "yuv420p", "-shortest", "-movflags", "+faststart"]
        + [output_path]
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg music/brand pass failed:\n{result.stderr[-3000:]}")


# ---------------------------------------------------------------------------
# Asset download helpers
# ---------------------------------------------------------------------------

def download_file_with_auth(url, filepath):
    """Download a file from a URL to a local path, handling B2 auth if needed."""
    print(f"Downloading {url} to {filepath}...")

    # If it's a Replicate prediction URL, resolve to the actual image URL first.
    if url.startswith("https://api.replicate.com/v1/predictions/"):
        print("Detected Replicate prediction URL, resolving...")
        if not REPLICATE_API_TOKEN:
            raise RuntimeError("REPLICATE_API_TOKEN missing in worker environment")

        headers = {"Authorization": f"Bearer {REPLICATE_API_TOKEN}"}
        for _ in range(60):  # up to 5 minutes
            resp = requests.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status")
            if status == "succeeded":
                output = data.get("output")
                if isinstance(output, list) and output:
                    url = output[0]
                elif isinstance(output, str):
                    url = output
                else:
                    raise Exception(f"Unexpected Replicate output: {output}")
                print(f"Resolved image URL: {url}")
                break
            elif status in ("failed", "canceled"):
                raise Exception(f"Replicate prediction failed: {data.get('error')}")
            print(f"Replicate status: {status}, waiting 5s...")
            time.sleep(5)
        else:
            raise Exception("Timeout waiting for Replicate prediction")

    # Use boto3 for Backblaze B2 URLs when credentials are available.
    if (
        "backblazeb2.com" in url
        and B2_ENDPOINT
        and B2_APPLICATION_KEY_ID
        and B2_APPLICATION_KEY
        and B2_BUCKET_NAME
    ):
        print("Downloading from B2 via boto3...")
        try:
            endpoint_url = B2_ENDPOINT if B2_ENDPOINT.startswith("http") else f"https://{B2_ENDPOINT}"
            b2 = boto3.client(
                service_name="s3",
                endpoint_url=endpoint_url,
                aws_access_key_id=B2_APPLICATION_KEY_ID,
                aws_secret_access_key=B2_APPLICATION_KEY,
                region_name=_b2_region(),
                config=Config(signature_version="s3v4"),
            )
            parsed = urlparse(url)
            # Virtual-hosted-style: https://<bucket>.s3.<region>.backblazeb2.com/<key>
            if parsed.netloc.startswith(B2_BUCKET_NAME + "."):
                object_key = parsed.path.lstrip("/")
            else:
                # Path-style: https://s3.<region>.backblazeb2.com/<bucket>/<key>
                url_parts = url.split(B2_BUCKET_NAME + "/")
                object_key = url_parts[1] if len(url_parts) > 1 else None
            if object_key:
                b2.download_file(B2_BUCKET_NAME, object_key, filepath)
                return filepath
        except Exception as e:
            print(f"boto3 download failed, falling back to HTTP: {e}")

    resp = requests.get(url, stream=True)
    resp.raise_for_status()
    with open(filepath, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
    return filepath


def download_elevenlabs_audio_by_id(audio_file_id, filepath):
    """Download ElevenLabs audio via history item ID extracted from n8n filesystem reference."""
    if not ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY missing in worker environment")

    history_item_id = audio_file_id.split("/")[-1]
    if not history_item_id:
        raise ValueError(f"Invalid audio_file_id: {audio_file_id}")

    url = f"https://api.elevenlabs.io/v1/history/{history_item_id}/audio"
    headers = {"xi-api-key": ELEVENLABS_API_KEY}
    print(f"Downloading ElevenLabs audio history item: {history_item_id}")
    resp = requests.get(url, headers=headers, stream=True, timeout=60)
    resp.raise_for_status()
    with open(filepath, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    return filepath


def resolve_and_download_audio(scene, audio_path):
    """Resolve audio from either a direct URL or an ElevenLabs filesystem reference."""
    audio_url = scene.get("audio_url")
    audio_file_id = scene.get("audio_file_id")

    if audio_url:
        if not audio_url.startswith("http"):
            audio_url = "https://" + audio_url.lstrip("/")
        return download_file_with_auth(audio_url, audio_path)

    if audio_file_id:
        if isinstance(audio_file_id, str) and audio_file_id.startswith("filesystem-v2:"):
            return download_elevenlabs_audio_by_id(audio_file_id, audio_path)
        raise ValueError(f"Unsupported audio_file_id format: {audio_file_id}")

    raise ValueError("Scene missing both 'audio_url' and 'audio_file_id'")


def _download_scene(args):
    """Download image + audio for a single scene. Designed for ThreadPoolExecutor."""
    i, scene, job_dir = args
    img_path = os.path.join(job_dir, f"scene_{i}.jpg")
    audio_path = os.path.join(job_dir, f"scene_{i}.mp3")

    image_url = scene.get("image_url")
    if not image_url:
        raise ValueError(f"Scene {i} missing image_url")

    download_file_with_auth(image_url, img_path)
    resolve_and_download_audio(scene, audio_path)

    if not os.path.exists(img_path) or os.path.getsize(img_path) == 0:
        raise FileNotFoundError(f"Empty or missing image: {img_path}")
    if not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
        raise FileNotFoundError(f"Empty or missing audio: {audio_path}")

    return i, img_path, audio_path


# ---------------------------------------------------------------------------
# Job processor
# ---------------------------------------------------------------------------

def process_job(job_data):
    """Download assets, apply Ken Burns + crossfade, upload final video, trigger callback."""
    if isinstance(job_data, list) and job_data:
        job_data = job_data[0]
    if "queue_payload" in job_data:
        job_data = job_data["queue_payload"]

    job_id = job_data.get("job_id")
    scenes = job_data.get("scenes", [])
    topic = job_data.get("topic", "Unknown Topic")

    print(f"\n--- Starting Job {job_id}: {topic} ({len(scenes)} scenes) ---")
    job_start_time = time.time()

    job_dir = os.path.join(TEMP_DIR, f"job_{job_id}")
    Path(job_dir).mkdir(parents=True, exist_ok=True)

    try:
        # Step 1: Download all image + audio assets in parallel (I/O bound)
        print(f"Downloading assets for {len(scenes)} scenes in parallel...")
        raw_files = [None] * len(scenes)
        with ThreadPoolExecutor(max_workers=len(scenes)) as ex:
            futures = {
                ex.submit(_download_scene, (i, scene, job_dir)): i
                for i, scene in enumerate(scenes)
            }
            for fut in as_completed(futures):
                i, img_path, audio_path = fut.result()  # raises on error
                raw_files[i] = (img_path, audio_path)

        print(f"Downloaded assets for {len(raw_files)} scenes.")

        # Step 2: Render each scene — Ken Burns zoom + audio normalisation + captions
        scene_videos = []
        for i, (img, aud) in enumerate(raw_files):
            scene_video = os.path.join(job_dir, f"scene_{i}.mp4")
            voiceover = scenes[i].get("voiceover_text")
            print(f"Rendering scene {i + 1}/{len(raw_files)} with Ken Burns...")
            make_scene_video(img, aud, scene_video, voiceover_text=voiceover)
            scene_videos.append(scene_video)

        # Step 3: Concatenate with crossfade dissolves
        concat_video = os.path.join(job_dir, f"concat_{job_id}.mp4")
        print(f"Concatenating {len(scene_videos)} scenes with crossfade...")
        concat_with_crossfade(scene_videos, concat_video)

        # Step 3.5: Mix background music + burn brand watermark
        final_video = os.path.join(job_dir, f"final_{job_id}.mp4")
        music_path = None
        if BACKGROUND_MUSIC_BASE_URL:
            music_key = _select_music_key(topic)
            music_dl_path = os.path.join(job_dir, "bgm.mp3")
            try:
                download_file_with_auth(
                    f"{BACKGROUND_MUSIC_BASE_URL.rstrip('/')}/{music_key}",
                    music_dl_path,
                )
                music_path = music_dl_path
            except Exception as e:
                print(f"Background music download failed (skipping): {e}")

        print("Applying background music and branding watermark...")
        add_music_and_branding(
            concat_video, final_video,
            topic=topic,
            music_path=music_path,
            brand_name=BRAND_NAME,
        )
        print(f"Final video ready: {final_video}")

        # Step 4: Upload to Backblaze B2
        final_b2_url = None
        if B2_ENDPOINT and B2_APPLICATION_KEY_ID and B2_APPLICATION_KEY and B2_BUCKET_NAME:
            print("Uploading to Backblaze B2...")
            endpoint_url = B2_ENDPOINT if B2_ENDPOINT.startswith("http") else f"https://{B2_ENDPOINT}"
            b2 = boto3.client(
                service_name="s3",
                endpoint_url=endpoint_url,
                aws_access_key_id=B2_APPLICATION_KEY_ID,
                aws_secret_access_key=B2_APPLICATION_KEY,
                region_name=_b2_region(),
                config=Config(signature_version="s3v4"),
            )
            object_name = f"{job_id}/final.mp4"
            b2.upload_file(
                final_video,
                B2_BUCKET_NAME,
                object_name,
                ExtraArgs={"ContentType": "video/mp4"},
            )
            # Presigned URL valid for 7 days (works with private B2 buckets)
            final_b2_url = b2.generate_presigned_url(
                "get_object",
                Params={"Bucket": B2_BUCKET_NAME, "Key": object_name},
                ExpiresIn=604800,
            )
            print(f"Uploaded: {final_b2_url}")
        else:
            print("B2 credentials missing, skipping upload.")
            final_b2_url = f"file://{final_video}"

        # Step 5: Update Postgres
        generation_time = int(time.time() - job_start_time)
        if DATABASE_URL:
            print(f"Updating job status in Postgres (generation_time={generation_time}s)...")
            conn = psycopg2.connect(DATABASE_URL)
            cur = conn.cursor()
            cur.execute(
                "UPDATE video_jobs SET status = %s, b2_url = %s, completed_at = NOW(), generation_time_seconds = %s WHERE id = %s",
                ("completed", final_b2_url, generation_time, job_id),
            )
            conn.commit()
            cur.close()
            conn.close()
            print("Postgres updated.")

        # Step 6: Callback to n8n
        if N8N_CALLBACK_URL and N8N_CALLBACK_URL != "https://replace_me/webhook/render-callback":
            print(f"Sending success callback to n8n...")
            requests.post(N8N_CALLBACK_URL, json={
                "job_id": job_id,
                "status": "completed",
                "b2_url": final_b2_url,
            })

        print(f"--- Job {job_id} Completed in {generation_time}s ---")

    except Exception as e:
        print(f"Error processing job {job_id}: {e}")

        if DATABASE_URL:
            try:
                conn = psycopg2.connect(DATABASE_URL)
                cur = conn.cursor()
                cur.execute(
                    "UPDATE video_jobs SET status = %s, error_logs = %s, updated_at = NOW() WHERE id = %s",
                    ("failed", str(e), job_id),
                )
                conn.commit()
                cur.close()
                conn.close()
            except Exception as db_err:
                print(f"Failed to update DB on error: {db_err}")

        if N8N_CALLBACK_URL and N8N_CALLBACK_URL != "https://replace_me/webhook/render-callback":
            requests.post(N8N_CALLBACK_URL, json={
                "job_id": job_id,
                "status": "failed",
                "b2_url": None,
                "error": str(e),
            })

    finally:
        # Clean up temp files to avoid disk accumulation on Render
        try:
            shutil.rmtree(job_dir, ignore_errors=True)
            print(f"Cleaned up temp dir: {job_dir}")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main queue consumer loop
# ---------------------------------------------------------------------------

def main():
    print(f"Worker started. Listening on '{QUEUE_NAME}'...")
    while not _shutdown:
        try:
            # timeout=5 instead of 0 so SIGTERM can break the blocking call
            # within 5 seconds rather than waiting indefinitely for the next job.
            result = r.blpop(QUEUE_NAME, timeout=5)
            if result:
                _, data_bytes = result
                job_payload = json.loads(data_bytes.decode("utf-8"))

                if isinstance(job_payload, list) and job_payload:
                    job_payload = job_payload[0]
                if "queue_payload" in job_payload:
                    job_data = job_payload["queue_payload"]
                else:
                    job_data = job_payload

                process_job(job_data)
        except Exception as e:
            if _shutdown:
                break
            print(f"Queue polling error: {e}")
            time.sleep(5)
    print("Worker shutdown complete.")


if __name__ == "__main__":
    main()
