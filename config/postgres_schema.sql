-- evolving-graphrag :: PostgreSQL schema
--
-- Applied by src/egraph/store/postgres.py on startup. Idempotent, so it is safe to run on
-- every boot of every replica.
--
-- The design point worth noticing is the two GIN indexes at the bottom. Provenance is an
-- array of chunk ids, and the retraction walk asks "which relations does this chunk
-- support?". A GIN index over that array turns the central operation of the whole system
-- into an index scan rather than a table scan -- which is a better story than the graph
-- database gives us, where the same lookup relies on a collection predicate whose support
-- varies by version.

CREATE TABLE IF NOT EXISTS documents (
    doc_id        TEXT PRIMARY KEY,
    uri           TEXT NOT NULL DEFAULT '',
    content_hash  TEXT NOT NULL DEFAULT '',
    version       INTEGER NOT NULL DEFAULT 1,
    status        TEXT NOT NULL DEFAULT 'active',
    text          TEXT NOT NULL DEFAULT '',
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Idempotent ingest: the add path looks a document up by hash before doing any work.
CREATE INDEX IF NOT EXISTS documents_content_hash_idx ON documents (content_hash);
CREATE INDEX IF NOT EXISTS documents_status_idx ON documents (status);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id      TEXT PRIMARY KEY,
    doc_id        TEXT NOT NULL,
    text          TEXT NOT NULL DEFAULT '',
    position      INTEGER NOT NULL DEFAULT 0,
    content_hash  TEXT NOT NULL DEFAULT '',
    embedding     DOUBLE PRECISION[] NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS chunks_doc_id_idx ON chunks (doc_id);
CREATE INDEX IF NOT EXISTS chunks_content_hash_idx ON chunks (content_hash);

CREATE TABLE IF NOT EXISTS entities (
    entity_id       TEXT PRIMARY KEY,
    canonical_name  TEXT NOT NULL DEFAULT '',
    type            TEXT NOT NULL DEFAULT 'concept',
    description     TEXT NOT NULL DEFAULT '',
    -- Provenance-keyed descriptions: chunk_id -> the sentence that chunk supported. The
    -- flat `description` is derived from this, so an entity that survives a deletion never
    -- keeps prose from the document that was removed.
    descriptions    JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding       DOUBLE PRECISION[] NOT NULL DEFAULT '{}',
    degree          INTEGER NOT NULL DEFAULT 0,
    community_id    TEXT,
    mentions        TEXT[] NOT NULL DEFAULT '{}',
    aliases         TEXT[] NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS entities_community_idx ON entities (community_id);

CREATE TABLE IF NOT EXISTS relations (
    relation_id   TEXT PRIMARY KEY,
    source_id     TEXT NOT NULL,
    target_id     TEXT NOT NULL,
    relation_type TEXT NOT NULL DEFAULT 'related_to',
    description   TEXT NOT NULL DEFAULT '',
    descriptions  JSONB NOT NULL DEFAULT '{}'::jsonb,
    weight        DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    -- The retraction lever. When this array empties, the relation has no source in the
    -- corpus any more and is deleted.
    provenance    TEXT[] NOT NULL DEFAULT '{}',
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS relations_source_idx ON relations (source_id);
CREATE INDEX IF NOT EXISTS relations_target_idx ON relations (target_id);

CREATE TABLE IF NOT EXISTS communities (
    community_id       TEXT PRIMARY KEY,
    level              INTEGER NOT NULL DEFAULT 0,
    title              TEXT NOT NULL DEFAULT '',
    summary            TEXT NOT NULL DEFAULT '',
    summary_hash       TEXT NOT NULL DEFAULT '',
    dirty              BOOLEAN NOT NULL DEFAULT TRUE,
    members            TEXT[] NOT NULL DEFAULT '{}',
    summarized_members TEXT[] NOT NULL DEFAULT '{}',
    member_count       INTEGER NOT NULL DEFAULT 0,
    embedding          DOUBLE PRECISION[] NOT NULL DEFAULT '{}',
    dirty_since        TIMESTAMPTZ,
    -- Ordering key for the recompute queue. Timestamps tie under a coarse clock, which
    -- made oldest-first non-deterministic; a counter is a total order by construction.
    dirty_seq          BIGINT NOT NULL DEFAULT 0,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- The maintenance loop scans this on every tick; a partial index keeps it to the working set.
CREATE INDEX IF NOT EXISTS communities_dirty_idx
    ON communities (dirty_seq, community_id) WHERE dirty;

CREATE TABLE IF NOT EXISTS resolution_decisions (
    id                BIGSERIAL PRIMARY KEY,
    candidate_name    TEXT NOT NULL DEFAULT '',
    candidate_type    TEXT NOT NULL DEFAULT '',
    decision          TEXT NOT NULL DEFAULT 'create',
    matched_entity_id TEXT,
    similarity        DOUBLE PRECISION NOT NULL DEFAULT 0,
    name_match        BOOLEAN NOT NULL DEFAULT FALSE,
    reason            TEXT NOT NULL DEFAULT '',
    chunk_id          TEXT,
    at                TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS resolution_at_idx ON resolution_decisions (at);

CREATE TABLE IF NOT EXISTS cost_records (
    id           BIGSERIAL PRIMARY KEY,
    operation    TEXT NOT NULL,
    tokens_in    INTEGER NOT NULL DEFAULT 0,
    tokens_out   INTEGER NOT NULL DEFAULT 0,
    wall_ms      DOUBLE PRECISION NOT NULL DEFAULT 0,
    usd          DOUBLE PRECISION NOT NULL DEFAULT 0,
    model        TEXT NOT NULL DEFAULT '',
    cache_hit    BOOLEAN NOT NULL DEFAULT FALSE,
    doc_id       TEXT,
    community_id TEXT,
    at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS cost_at_idx ON cost_records (at);
CREATE INDEX IF NOT EXISTS cost_operation_idx ON cost_records (operation);

-- ------------------------------------------------------------------ the provenance walk
-- These two make deletion an index scan. Without them, retracting one document means a
-- sequential scan of every relation and every entity in the index.
CREATE INDEX IF NOT EXISTS relations_provenance_gin ON relations USING GIN (provenance);
CREATE INDEX IF NOT EXISTS entities_mentions_gin ON entities USING GIN (mentions);
