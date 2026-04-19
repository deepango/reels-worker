import hashlib
import logging
import httpx
from botocore.exceptions import ClientError
from config import ELEVENLABS_API_KEY, VOICE_ID, B2_BUCKET_NAME, B2_ENDPOINT
from providers.b2 import get_client

log = logging.getLogger(__name__)

TTS_CACHE_PREFIX = f"tts-cache/{VOICE_ID}"


def _cache_key(text: str) -> str:
    h = hashlib.md5(text.encode()).hexdigest()
    return f"{TTS_CACHE_PREFIX}/{h}.mpga"


def _audio_url(key: str) -> str:
    host = B2_ENDPOINT.replace("https://", "").replace("http://", "").rstrip("/")
    url = f"https://{host}/{B2_BUCKET_NAME}/{key}"
    return url


def get_or_generate_audio(text: str) -> str:
    key = _cache_key(text)
    log.info(f"[elevenlabs] checking TTS cache | key={key} | text='{text[:60]}'")
    log.debug(f"[elevenlabs] api_key_prefix={ELEVENLABS_API_KEY[:8] if ELEVENLABS_API_KEY else 'MISSING'} | bucket={B2_BUCKET_NAME} | endpoint={B2_ENDPOINT}")

    s3 = get_client()
    try:
        s3.head_object(Bucket=B2_BUCKET_NAME, Key=key)
        url = _audio_url(key)
        log.info(f"[elevenlabs] cache HIT | url={url}")
        return url
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code not in ("404", "NoSuchKey"):
            log.error(f"[elevenlabs] B2 head_object unexpected error code={code}: {e}")
            raise
        log.info(f"[elevenlabs] cache MISS (code={code}) — calling ElevenLabs API")

    log.info(f"[elevenlabs] POST /v1/text-to-speech/{VOICE_ID} | model=eleven_turbo_v2_5")
    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}?output_format=mp3_44100_128",
                headers={
                    "xi-api-key": ELEVENLABS_API_KEY,
                    "content-type": "application/json",
                },
                json={
                    "text": text,
                    "model_id": "eleven_turbo_v2_5",
                    "voice_settings": {
                        "stability": 0.45,
                        "similarity_boost": 0.82,
                        "style": 0.15,
                        "use_speaker_boost": True,
                    },
                },
            )
            log.info(f"[elevenlabs] TTS response status={resp.status_code} | content_length={len(resp.content)} bytes")
            if resp.status_code != 200:
                log.error(f"[elevenlabs] TTS error body: {resp.text[:500]}")
            resp.raise_for_status()
            audio_bytes = resp.content
    except httpx.HTTPStatusError as e:
        log.error(f"[elevenlabs] HTTP error {e.response.status_code}: {e.response.text[:500]}")
        raise
    except Exception as e:
        log.error(f"[elevenlabs] request failed: {type(e).__name__}: {e}")
        raise

    log.info(f"[elevenlabs] uploading {len(audio_bytes)} bytes to B2 | key={key}")
    try:
        s3.put_object(Bucket=B2_BUCKET_NAME, Key=key, Body=audio_bytes, ContentType="audio/mpeg")
        log.info(f"[elevenlabs] B2 upload complete")
    except Exception as e:
        log.error(f"[elevenlabs] B2 upload failed: {type(e).__name__}: {e}")
        raise

    url = _audio_url(key)
    log.info(f"[elevenlabs] audio ready | url={url}")
    return url
