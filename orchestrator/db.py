import logging
import psycopg2
import psycopg2.extras
from config import DATABASE_URL

log = logging.getLogger(__name__)


def _conn():
    if not DATABASE_URL:
        log.error("[db] DATABASE_URL is not set!")
        raise RuntimeError("DATABASE_URL not configured")
    return psycopg2.connect(DATABASE_URL)


def check_quota(customer_id: int) -> int:
    log.info(f"[db] check_quota | customer_id={customer_id}")
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT videos_remaining FROM customers WHERE id = %s", (customer_id,))
            row = cur.fetchone()
            if row is None:
                log.warning(f"[db] customer {customer_id} not found")
                raise ValueError(f"Customer {customer_id} not found")
            log.info(f"[db] customer {customer_id} has {row[0]} videos remaining")
            return row[0]


def insert_video_job(customer_id: int, topic: str) -> dict:
    log.info(f"[db] insert_video_job | customer_id={customer_id} | topic='{topic}'")
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO video_jobs (customer_id, topic, status)
                VALUES (%s, %s, 'pending')
                RETURNING id, topic, customer_id, created_at
                """,
                (customer_id, topic),
            )
            row = dict(cur.fetchone())
            log.info(f"[db] job inserted | job_id={row['id']}")
            return row


def mark_processing(job_id: int):
    log.info(f"[db] mark_processing | job_id={job_id}")
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE video_jobs
                SET status = 'processing',
                    cost_breakdown = %s::jsonb,
                    updated_at = NOW()
                WHERE id = %s
                """,
                ('{"anthropic": 0.01, "replicate": 0.05, "elevenlabs": 0.03}', job_id),
            )
    log.info(f"[db] job {job_id} marked processing")


def mark_failed(job_id: int, error: str):
    log.error(f"[db] mark_failed | job_id={job_id} | error={error[:200]}")
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE video_jobs
                SET status = 'failed',
                    error_logs = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (error[:2000], job_id),
            )
    log.info(f"[db] job {job_id} marked failed")


def decrement_quota(customer_id: int):
    log.info(f"[db] decrement_quota | customer_id={customer_id}")
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE customers
                SET videos_remaining = GREATEST(0, videos_remaining - 1)
                WHERE id = %s
                """,
                (customer_id,),
            )
    log.info(f"[db] quota decremented for customer {customer_id}")


def update_callback_received(job_id: int):
    log.info(f"[db] update_callback_received | job_id={job_id}")
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE video_jobs SET callback_received_at = NOW() WHERE id = %s",
                (job_id,),
            )
    log.info(f"[db] callback_received_at set for job {job_id}")
