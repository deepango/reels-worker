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
    return f"https://{host}/{B2_BUCKET_NAME}/{key}"


def get_or_generate_audio(text: str) -> str:
    """Returns a B2 URL for the TTS audio, using MD5-keyed cache."""
    key = _cache_key(text)
    s3 = get_client()

    try:
        s3.head_object(Bucket=B2_BUCKET_NAME, Key=key)
        log.info(f"TTS cache HIT: {key}")
        return _audio_url(key)
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("404", "NoSuchKey"):
            raise

    log.info(f"TTS cache MISS — generating audio for: {text[:60]}")
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
        resp.raise_for_status()
        audio_bytes = resp.content

    s3.put_object(Bucket=B2_BUCKET_NAME, Key=key, Body=audio_bytes, ContentType="audio/mpeg")
    log.info(f"TTS uploaded to B2: {key}")
    return _audio_url(key)
