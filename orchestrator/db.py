import psycopg2
import psycopg2.extras
from config import DATABASE_URL


def _conn():
    return psycopg2.connect(DATABASE_URL)


def check_quota(customer_id: int) -> int:
    """Returns videos_remaining for customer. Raises ValueError if customer not found."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT videos_remaining FROM customers WHERE id = %s", (customer_id,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"Customer {customer_id} not found")
            return row[0]


def insert_video_job(customer_id: int, topic: str) -> dict:
    """Inserts a pending video_jobs row. Returns dict with id, topic, customer_id, created_at."""
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
            return dict(cur.fetchone())


def mark_processing(job_id: int):
    """Updates job to processing with hardcoded cost_breakdown."""
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


def mark_failed(job_id: int, error: str):
    """Updates job to failed with error message."""
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


def decrement_quota(customer_id: int):
    """Decrements videos_remaining by 1 (floor at 0)."""
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


def update_callback_received(job_id: int):
    """Records that render-callback was received."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE video_jobs SET callback_received_at = NOW() WHERE id = %s",
                (job_id,),
            )
