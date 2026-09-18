-- Chat history for DeepAgent sessions.
-- One row per (session_id, turn). Created automatically on container
-- startup via /docker-entrypoint-initdb.d/ in docker-compose.yml.

CREATE TABLE IF NOT EXISTS chat_messages (
    id           BIGSERIAL    PRIMARY KEY,
    session_id   TEXT         NOT NULL,
    user_id      TEXT,
    user_message TEXT         NOT NULL,
    bot_reply    TEXT         NOT NULL,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Most reads are "last N messages for a session" — this index serves that.
CREATE INDEX IF NOT EXISTS idx_chat_messages_session_recent
    ON chat_messages (session_id, created_at DESC);
