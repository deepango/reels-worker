import json
import redis as redis_lib
from config import REDIS_URL, QUEUE_NAME

_client = None


def _redis():
    global _client
    if _client is None:
        _client = redis_lib.from_url(REDIS_URL)
    return _client


def push_job(payload: dict):
    """RPUSHes job payload JSON to the reels:jobs Redis queue."""
    _redis().rpush(QUEUE_NAME, json.dumps(payload))
