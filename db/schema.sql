-- STATUS: DORMANT / v2.4.0-PREP / NOT ACTIVE
-- Purpose: Future PostgreSQL state management for users, jobs, swarm nodes.
-- Safe to commit. Does not affect current C++ daemon, local jobs, or renders.
-- Activation requires manual Docker/PG setup + explicit agent wiring.

CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email VARCHAR(255) UNIQUE,
  api_key_hash VARCHAR(64) UNIQUE NOT NULL,
  tier VARCHAR(20) DEFAULT 'free',
  renders_used INT DEFAULT 0,
  monthly_limit INT DEFAULT 10,
  stripe_customer_id VARCHAR(255),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS jobs (
  id VARCHAR(64) PRIMARY KEY,
  user_id UUID REFERENCES users(id),
  status VARCHAR(20) DEFAULT 'SUBMITTED',
  style VARCHAR(50),
  prompt TEXT,
  video_path TEXT,
  priority INT DEFAULT 0,
  qc_score FLOAT DEFAULT 0.0,
  s3_url TEXT,
  s3_synced BOOLEAN DEFAULT false,
  assigned_node TEXT,
  submitted_at TIMESTAMPTZ DEFAULT NOW(),
  finished_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS swarm_nodes (
  id VARCHAR(64) PRIMARY KEY,
  role VARCHAR(20) NOT NULL,
  status VARCHAR(20) DEFAULT 'online',
  capacity INT DEFAULT 3,
  current_load INT DEFAULT 0,
  last_heartbeat TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_users_key ON users(api_key_hash);
CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_swarm_role ON swarm_nodes(role, status);
