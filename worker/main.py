import os
import json
import time
import requests
import subprocess
import redis
from pathlib import Path

# Load environment variables
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
N8N_CALLBACK_URL = os.environ.get("N8N_CALLBACK_URL")
TEMP_DIR = os.environ.get("TEMP_DIR", "/tmp/reels")
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

def download_file(url, filepath):
    """Download a file from a URL to a local path."""
    print(f"Downloading {url} to {filepath}...")
    response = requests.get(url, stream=True)
    response.raise_for_status()
    with open(filepath, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    return filepath

def process_job(job_data):
    """Process a single video job: download assets, run ffmpeg, trigger callback."""
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
            audio_url = scene.get("audio_url") # Assuming n8n passes URL, or you saved binary to a temp host
            
            image_path = os.path.join(job_dir, f"scene_{i}.jpg")
            audio_path = os.path.join(job_dir, f"scene_{i}.mp3")
            
            # Note: For production, you need to handle n8n binaries. 
            # If n8n gives raw binary data, you'll save it directly instead of requests.get()
            if image_url: download_file(image_url, image_path)
            if audio_url: download_file(audio_url, audio_path)
            
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

        # Step 4: Upload to Backblaze B2 (Placeholder for actual B2 API call)
        # TODO: Implement B2 upload using boto3 or b2sdk
        final_b2_url = f"https://s3.us-west-000.backblazeb2.com/reels-output/final_{job_id}.mp4"
        print(f"Simulated upload to B2: {final_b2_url}")

        # Step 5: Send Callback to n8n
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
                job_data = json.loads(data_bytes.decode('utf-8'))
                process_job(job_data)
        except Exception as e:
            print(f"Queue polling error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
