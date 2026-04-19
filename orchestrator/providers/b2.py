import boto3
from botocore.client import Config
from config import B2_ENDPOINT, B2_APPLICATION_KEY_ID, B2_APPLICATION_KEY, B2_BUCKET_NAME


def _b2_region() -> str:
    if not B2_ENDPOINT:
        return "us-east-1"
    host = B2_ENDPOINT.replace("https://", "").replace("http://", "").split("/")[0]
    parts = host.split(".")
    return parts[1] if len(parts) >= 4 else "us-east-1"


def get_client():
    return boto3.client(
        "s3",
        endpoint_url=B2_ENDPOINT,
        aws_access_key_id=B2_APPLICATION_KEY_ID,
        aws_secret_access_key=B2_APPLICATION_KEY,
        region_name=_b2_region(),
        config=Config(signature_version="s3v4"),
    )
