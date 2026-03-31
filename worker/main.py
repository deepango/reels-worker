import os
import json
import time
import requests
import subprocess
import redis
import boto3
import psycopg2
from botocore.client import Config
from pathlib import Path

# Load environment variables
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
DATABASE_URL = os.environ.get("DATABASE_URL")
N8N_CALLBACK_URL = os.environ.get("N8N_CALLBACK_URL")
TEMP_DIR = os.environ.get("TEMP_DIR", "/tmp/reels")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")

B2_ENDPOINT = os.environ.get("B2_ENDPOINT")
B2_APPLICATION_KEY_ID = os.environ.get("B2_APPLICATION_KEY_ID")
B2_APPLICATION_KEY = os.environ.get("B2_APPLICATION_KEY")
B2_BUCKET_NAME = os.environ.get("B2_BUCKET_NAME")

QUEUE_NAME = "reels:jobs"

# Set up Redis connection
try:
    r = redis.from_url(REDIS_URL)
    print(f"Connected to Redis at {REDIS_URL.split('@')[-1] if '@' in REDIS_URL else REDIS_URL}")
except Exception as e:
    print(f"Failed to connect to Redis: {e}")
    exit(1)

# Ensure temp directory exists
Path(TEMP_DIR).mkdir(parents=True, exist_ok=True)

def download_file_with_auth(url, filepath):
    """Download a file from a URL to a local path, handling B2 Auth if needed."""
    print(f"Downloading {url} to {filepath}...")
    
    # If it's a Backblaze B2 url, and we have credentials, use boto3 to download
    if "backblazeb2.com" in url and B2_ENDPOINT and B2_APPLICATION_KEY_ID and B2_APPLICATION_KEY and B2_BUCKET_NAME:
        print("Using Boto3 to download from B2...")
        try:
            # Boto3 endpoints must strictly start with https://
            endpoint_url = B2_ENDPOINT
            if not endpoint_url.startswith('http'):
                endpoint_url = 'https://' + endpoint_url
                
            b2 = boto3.client(
                service_name='s3',
                endpoint_url=endpoint_url,
                aws_access_key_id=B2_APPLICATION_KEY_ID,
                aws_secret_access_key=B2_APPLICATION_KEY,
                config=Config(signature_version='s3v4')
            )
            # Extact object key from URL: https://s3.../bucket-name/object/key.mpga
            # Example url: https://s3.us-east-005.backblazeb2.com/reels-output/0/1/audio.mpga
            url_parts = url.split(B2_BUCKET_NAME + '/')
            if len(url_parts) > 1:
                object_key = url_parts[1]
                b2.download_file(B2_BUCKET_NAME, object_key, filepath)
                return filepath
        except Exception as e:
            print(f"Boto3 download failed, falling back to public request: {e}")

    # Fallback to public HTTP request
    response = requests.get(url, stream=True)
    response.raise_for_status()
    with open(filepath, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    return filepath

def download_elevenlabs_audio_by_id(audio_file_id, filepath):
    """Download ElevenLabs audio from a filesystem-v2 reference by extracting the history item id."""
    if not ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY is missing in worker environment")

    # n8n reference format:
    # filesystem-v2:workflows/<workflow_id>/executions/<id>/binary_data/<history_item_id>
    history_item_id = audio_file_id.split("/")[-1]
    if not history_item_id:
        raise ValueError(f"Invalid audio_file_id: {audio_file_id}")

    url = f"https://api.elevenlabs.io/v1/history/{history_item_id}/audio"
    headers = {"xi-api-key": ELEVENLABS_API_KEY}
    print(f"Downloading ElevenLabs audio by history id: {history_item_id}")
    response = requests.get(url, headers=headers, stream=True, timeout=60)
    response.raise_for_status()

    with open(filepath, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    return filepath

def resolve_and_download_audio(scene, audio_path):
    """Resolve audio from either direct URL or ElevenLabs file reference."""
    audio_url = scene.get("audio_url")
    audio_file_id = scene.get("audio_file_id")

    if audio_url:
        if not audio_url.startswith('http'):
            audio_url = 'https://' + audio_url.lstrip('/')
        return download_file_with_auth(audio_url, audio_path)

    if audio_file_id:
        if isinstance(audio_file_id, str) and audio_file_id.startswith("filesystem-v2:"):
            return download_elevenlabs_audio_by_id(audio_file_id, audio_path)
        raise ValueError(f"Unsupported audio_file_id format: {audio_file_id}")

    raise ValueError("Scene is missing both 'audio_url' and 'audio_file_id'")

def process_job(job_data):
    """Process a single video job: download assets, run ffmpeg, trigger callback."""
    # n8n might send an array or wrap it in queue_payload
    if isinstance(job_data, list) and len(job_data) > 0:
        job_data = job_data[0]
        
    if "queue_payload" in job_data:
        job_data = job_data["queue_payload"]

    job_id = job_data.get("job_id")
    scenes = job_data.get("scenes", [])
    topic = job_data.get("topic", "Unknown Topic")
    
    print(f"\n--- Starting Job {job_id}: {topic} ---")
    
    job_dir = os.path.join(TEMP_DIR, f"job_{job_id}")
    Path(job_dir).mkdir(parents=True, exist_ok=True)
    
    try:
        # Step 1: Download all assets
        scene_files = []
        for i, scene in enumerate(scenes):
            # These keys match your n8n output assignments
            image_url = scene.get("image_url")
            
            image_path = os.path.join(job_dir, f"scene_{i}.jpg")
            audio_path = os.path.join(job_dir, f"scene_{i}.mp3")
            
            if image_url:
                download_file_with_auth(image_url, image_path)
            else:
                raise ValueError(f"Scene {i} is missing image_url")

            resolve_and_download_audio(scene, audio_path)
            
            scene_files.append((image_path, audio_path))
            
        print(f"Downloaded assets for {len(scene_files)} scenes.")

        # Step 2: Generate FFMPEG Concat File
        concat_file = os.path.join(job_dir, "concat.txt")
        with open(concat_file, "w") as f:
            for i, (img, aud) in enumerate(scene_files):
                # Ensure files exist and have content
                if not os.path.exists(img) or os.path.getsize(img) == 0:
                    raise FileNotFoundError(f"Missing or empty image file: {img}")
                if not os.path.exists(aud) or os.path.getsize(aud) == 0:
                    raise FileNotFoundError(f"Missing or empty audio file: {aud}")

                scene_video = os.path.join(job_dir, f"out_{i}.mp4")
                cmd = [
                    "ffmpeg", "-y",
                    "-loop", "1", "-framerate", "2", "-i", img,
                    "-i", aud,
                    "-c:v", "libx264", "-tune", "stillimage", "-c:a", "aac",
                    "-b:a", "192k", "-pix_fmt", "yuv420p", "-shortest",
                    scene_video
                ]
                print(f"Running FFmpeg for scene {i}...")
                
                # Capture standard error to surface exact FFmpeg failure
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    print(f"FFmpeg failed with exit code {result.returncode}")
                    print(f"FFmpeg STDERR:\n{result.stderr}")
                    result.check_returncode() # Raise error to break out of try-block
                
                f.write(f"file '{scene_video}'\n")

        # Step 3: Concat all scenes into final video
        final_video = os.path.join(job_dir, f"final_{job_id}.mp4")
        concat_cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", 
            "-i", concat_file, "-c", "copy", final_video
        ]
        print("Concatenating final video...")
        subprocess.run(concat_cmd, check=True, capture_output=True)
        
        print(f"Video generated successfully: {final_video}")

        # Step 4: Upload to Backblaze B2
        final_b2_url = None
        if B2_ENDPOINT and B2_APPLICATION_KEY_ID and B2_APPLICATION_KEY and B2_BUCKET_NAME:
            print("Uploading final video to Backblaze B2...")
            endpoint_url = B2_ENDPOINT
            if not endpoint_url.startswith('http'):
                endpoint_url = 'https://' + endpoint_url
                
            b2 = boto3.client(
                service_name='s3',
                endpoint_url=endpoint_url,
                aws_access_key_id=B2_APPLICATION_KEY_ID,
                aws_secret_access_key=B2_APPLICATION_KEY,
                config=Config(signature_version='s3v4')
            )
            object_name = f"{job_id}/final.mp4"
            b2.upload_file(final_video, B2_BUCKET_NAME, object_name)
            
            # Construct public URL
            # Format: https://<endpoint_host>/<bucket_name>/<object_name>
            endpoint_host = B2_ENDPOINT.replace("https://", "").replace("http://", "")
            final_b2_url = f"https://{endpoint_host}/{B2_BUCKET_NAME}/{object_name}"
            print(f"Uploaded successfully. B2 URL: {final_b2_url}")
        else:
            print("B2 credentials missing, skipping upload.")
            final_b2_url = f"file://{final_video}"

        # Step 5: Update Database directly (or fallback to webhook)
        if DATABASE_URL:
            print("Updating job status in Postgres...")
            conn = psycopg2.connect(DATABASE_URL)
            cur = conn.cursor()
            cur.execute(
                "UPDATE video_jobs SET status = %s, b2_url = %s, completed_at = NOW() WHERE id = %s",
                ("completed", final_b2_url, job_id)
            )
            conn.commit()
            cur.close()
            conn.close()
            print("Postgres updated successfully.")

        if N8N_CALLBACK_URL and N8N_CALLBACK_URL != "https://replace_me/webhook/render-callback":
            print(f"Sending success callback to n8n: {N8N_CALLBACK_URL}")
            requests.post(N8N_CALLBACK_URL, json={
                "job_id": job_id,
                "status": "completed",
                "b2_url": final_b2_url
            })
            
        print(f"--- Job {job_id} Completed Successfully ---")

    except Exception as e:
        print(f"Error processing job {job_id}: {e}")
        # Send failure callback
        if DATABASE_URL:
            try:
                conn = psycopg2.connect(DATABASE_URL)
                cur = conn.cursor()
                cur.execute(
                    "UPDATE video_jobs SET status = %s, error_logs = %s, updated_at = NOW() WHERE id = %s",
                    ("failed", str(e), job_id)
                )
                conn.commit()
                cur.close()
                conn.close()
            except Exception as db_err:
                print(f"Failed to update db on error: {db_err}")

        if N8N_CALLBACK_URL and N8N_CALLBACK_URL != "https://replace_me/webhook/render-callback":
            requests.post(N8N_CALLBACK_URL, json={
                "job_id": job_id,
                "status": "failed",
                "b2_url": None,
                "error": str(e)
            })

def main():
    print(f"Worker started. Listening for jobs on '{QUEUE_NAME}'...")
    while True:
        try:
            # Block until a job is available in the queue (timeout 0 means wait forever)
            result = r.blpop(QUEUE_NAME, timeout=0)
            if result:
                _, data_bytes = result
                job_payload = json.loads(data_bytes.decode('utf-8'))
                
                # If wrapped in an array, take the first element
                if isinstance(job_payload, list) and len(job_payload) > 0:
                    job_payload = job_payload[0]
                    
                # The actual data is inside 'queue_payload' key based on n8n output
                if "queue_payload" in job_payload:
                    job_data = job_payload["queue_payload"]
                else:
                    job_data = job_payload
                    
                process_job(job_data)
        except Exception as e:
            print(f"Queue polling error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
