-- Core schema for AI reels platform

CREATE TABLE IF NOT EXISTS customers (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    subscription_status VARCHAR(50) DEFAULT 'active',
    plan_type VARCHAR(50) DEFAULT 'pro',
    videos_remaining INT DEFAULT 20 CHECK (videos_remaining >= 0),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    last_video_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS video_jobs (
    id SERIAL PRIMARY KEY,
    customer_id INT REFERENCES customers(id) ON DELETE CASCADE,
    topic TEXT NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',
    b2_url TEXT,
    error_logs TEXT,
    cost_breakdown JSONB,
    generation_time_seconds INT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS usage_tracking (
    id SERIAL PRIMARY KEY,
    date DATE UNIQUE NOT NULL,
    total_videos INT DEFAULT 0,
    total_cost_inr DECIMAL(10,2) DEFAULT 0,
    api_breakdown JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Existing indexes
CREATE INDEX IF NOT EXISTS idx_video_jobs_status ON video_jobs(status);
CREATE INDEX IF NOT EXISTS idx_video_jobs_created_at ON video_jobs(created_at);
CREATE INDEX IF NOT EXISTS idx_usage_tracking_date ON usage_tracking(date);

-- New performance indexes
CREATE INDEX IF NOT EXISTS idx_video_jobs_customer_id ON video_jobs(customer_id);
