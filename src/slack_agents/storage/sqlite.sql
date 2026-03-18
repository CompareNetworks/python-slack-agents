-- Schema for the SQLite storage layer.

-- Generic key-value store for plugins and future use.
CREATE TABLE IF NOT EXISTS kv_store (
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (namespace, key)
);

-- Generic list store for ordered collections.
CREATE TABLE IF NOT EXISTS list_store (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_list_store_ns_key ON list_store(namespace, key);

-- conversations: Top-level thread tracking.
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    channel_name TEXT,
    thread_id TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE (agent_name, channel_id, thread_id)
);

-- messages: One row per user-triggered exchange in a conversation.
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    user_id TEXT NOT NULL,
    user_name TEXT NOT NULL,
    user_handle TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

-- blocks: Unified block storage (text, file, tool, usage).
CREATE TABLE IF NOT EXISTS blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES messages(id),
    block_type TEXT NOT NULL,
    content TEXT NOT NULL,
    is_user BOOLEAN,
    source_file_id INTEGER,
    tool_block_id INTEGER,
    filename TEXT,
    mimetype TEXT,
    size_bytes INTEGER,
    tool_call_id TEXT,
    tool_name TEXT,
    tool_input TEXT,
    tool_output TEXT,
    is_error BOOLEAN,
    created_at TEXT DEFAULT (datetime('now'))
);

-- agent_heartbeats: Tracks Socket Mode ping/pong health per agent.
CREATE TABLE IF NOT EXISTS agent_heartbeats (
    agent_name TEXT PRIMARY KEY,
    last_ping_pong_time REAL NOT NULL
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_blocks_msg ON blocks(message_id);
CREATE INDEX IF NOT EXISTS idx_blocks_tool_call ON blocks(tool_call_id);
