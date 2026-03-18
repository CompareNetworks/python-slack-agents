-- Schema for the slack-agents storage layer.
-- All tables use SERIAL primary keys and TIMESTAMPTZ for timestamps.

-- conversations: Top-level thread tracking. One row per thread the agent participates in.
CREATE TABLE IF NOT EXISTS conversations (
    id SERIAL PRIMARY KEY,
    agent_name TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    channel_name TEXT,
    thread_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (agent_name, channel_id, thread_id)
);

-- messages: One row per user-triggered exchange in a conversation.
CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    user_name TEXT NOT NULL,
    user_handle TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- tool_blocks: LLM tool invocations and their results.
CREATE TABLE IF NOT EXISTS tool_blocks (
    id SERIAL PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    tool_call_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_input JSONB NOT NULL,
    tool_output TEXT NOT NULL,
    is_error BOOLEAN NOT NULL,
    schema_version TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- file_blocks: Binary file content (images, documents) within a message.
CREATE TABLE IF NOT EXISTS file_blocks (
    id SERIAL PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    is_user BOOLEAN NOT NULL,
    filename TEXT NOT NULL,
    mimetype TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    content JSONB NOT NULL,
    tool_block_id INTEGER REFERENCES tool_blocks(id) ON DELETE SET NULL,
    schema_version TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- text_blocks: Text content within a message.
CREATE TABLE IF NOT EXISTS text_blocks (
    id SERIAL PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    is_user BOOLEAN NOT NULL,
    text TEXT NOT NULL,
    source_file_id INTEGER REFERENCES file_blocks(id) ON DELETE SET NULL,
    schema_version TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- usage_blocks: Token usage and cost tracking per message.
CREATE TABLE IF NOT EXISTS usage_blocks (
    id SERIAL PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    version TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_creation_input_tokens INTEGER NOT NULL,
    cache_read_input_tokens INTEGER NOT NULL,
    peak_single_call_input_tokens INTEGER NOT NULL,
    estimated_cost_usd NUMERIC,
    schema_version TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- agent_heartbeats: Tracks Socket Mode ping/pong health per agent.
CREATE TABLE IF NOT EXISTS agent_heartbeats (
    agent_name TEXT PRIMARY KEY,
    last_socket_ping_at TIMESTAMPTZ NOT NULL,
    schema_version TEXT NOT NULL
);

-- Generic key-value store for plugins and future use.
CREATE TABLE IF NOT EXISTS kv_store (
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (namespace, key)
);

-- Generic list store for ordered collections.
CREATE TABLE IF NOT EXISTS list_store (
    id SERIAL PRIMARY KEY,
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_text_blocks_message_id ON text_blocks(message_id);
CREATE INDEX IF NOT EXISTS idx_text_blocks_source_file_id
    ON text_blocks(source_file_id) WHERE source_file_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_file_blocks_message_id ON file_blocks(message_id);
CREATE INDEX IF NOT EXISTS idx_file_blocks_filename ON file_blocks(filename);
CREATE INDEX IF NOT EXISTS idx_file_blocks_mimetype ON file_blocks(mimetype);
CREATE INDEX IF NOT EXISTS idx_file_blocks_size_bytes ON file_blocks(size_bytes);
CREATE INDEX IF NOT EXISTS idx_file_blocks_tool_block_id
    ON file_blocks(tool_block_id) WHERE tool_block_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tool_blocks_message_id ON tool_blocks(message_id);
CREATE INDEX IF NOT EXISTS idx_tool_blocks_tool_call_id ON tool_blocks(tool_call_id);
CREATE INDEX IF NOT EXISTS idx_usage_blocks_message_id ON usage_blocks(message_id);
CREATE INDEX IF NOT EXISTS idx_list_store_ns_key ON list_store(namespace, key);
