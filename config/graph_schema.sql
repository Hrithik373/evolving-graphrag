-- evolving-graphrag :: ArcadeDB schema (blueprint section 5)
-- Applied by src/egraph/store/schema.py. Idempotent: every statement is IF NOT EXISTS,
-- and the migrator swallows "already exists" so `make up` can re-run it safely.

-- ------------------------------------------------------------------ vertices
CREATE VERTEX TYPE SourceDocument IF NOT EXISTS;
CREATE PROPERTY SourceDocument.doc_id IF NOT EXISTS STRING;
CREATE PROPERTY SourceDocument.uri IF NOT EXISTS STRING;
CREATE PROPERTY SourceDocument.content_hash IF NOT EXISTS STRING;
CREATE PROPERTY SourceDocument.version IF NOT EXISTS INTEGER;
CREATE PROPERTY SourceDocument.status IF NOT EXISTS STRING;
CREATE PROPERTY SourceDocument.text IF NOT EXISTS STRING;
CREATE PROPERTY SourceDocument.ingested_at IF NOT EXISTS STRING;
CREATE PROPERTY SourceDocument.updated_at IF NOT EXISTS STRING;
CREATE INDEX IF NOT EXISTS ON SourceDocument (doc_id) UNIQUE;
CREATE INDEX IF NOT EXISTS ON SourceDocument (content_hash) NOTUNIQUE;

CREATE VERTEX TYPE Chunk IF NOT EXISTS;
CREATE PROPERTY Chunk.chunk_id IF NOT EXISTS STRING;
CREATE PROPERTY Chunk.doc_id IF NOT EXISTS STRING;
CREATE PROPERTY Chunk.text IF NOT EXISTS STRING;
CREATE PROPERTY Chunk.position IF NOT EXISTS INTEGER;
CREATE PROPERTY Chunk.content_hash IF NOT EXISTS STRING;
CREATE PROPERTY Chunk.embedding IF NOT EXISTS LIST OF DOUBLE;
CREATE INDEX IF NOT EXISTS ON Chunk (chunk_id) UNIQUE;
CREATE INDEX IF NOT EXISTS ON Chunk (doc_id) NOTUNIQUE;
CREATE INDEX IF NOT EXISTS ON Chunk (content_hash) NOTUNIQUE;

CREATE VERTEX TYPE Entity IF NOT EXISTS;
CREATE PROPERTY Entity.entity_id IF NOT EXISTS STRING;
CREATE PROPERTY Entity.canonical_name IF NOT EXISTS STRING;
CREATE PROPERTY Entity.type IF NOT EXISTS STRING;
CREATE PROPERTY Entity.description IF NOT EXISTS STRING;
-- Provenance-keyed descriptions: chunk_id -> the sentence that chunk supported. The flat
-- `description` above is derived from this map, so a survivor never keeps prose from a
-- document that was deleted.
CREATE PROPERTY Entity.descriptions IF NOT EXISTS MAP;
CREATE PROPERTY Entity.embedding IF NOT EXISTS LIST OF DOUBLE;
CREATE PROPERTY Entity.degree IF NOT EXISTS INTEGER;
CREATE PROPERTY Entity.community_id IF NOT EXISTS STRING;
CREATE PROPERTY Entity.mentions IF NOT EXISTS LIST OF STRING;
CREATE PROPERTY Entity.aliases IF NOT EXISTS LIST OF STRING;
CREATE PROPERTY Entity.created_at IF NOT EXISTS STRING;
CREATE PROPERTY Entity.updated_at IF NOT EXISTS STRING;
CREATE INDEX IF NOT EXISTS ON Entity (entity_id) UNIQUE;
CREATE INDEX IF NOT EXISTS ON Entity (community_id) NOTUNIQUE;

CREATE VERTEX TYPE Community IF NOT EXISTS;
CREATE PROPERTY Community.community_id IF NOT EXISTS STRING;
CREATE PROPERTY Community.level IF NOT EXISTS INTEGER;
CREATE PROPERTY Community.title IF NOT EXISTS STRING;
CREATE PROPERTY Community.summary IF NOT EXISTS STRING;
CREATE PROPERTY Community.summary_hash IF NOT EXISTS STRING;
CREATE PROPERTY Community.dirty IF NOT EXISTS BOOLEAN;
CREATE PROPERTY Community.members IF NOT EXISTS LIST OF STRING;
CREATE PROPERTY Community.summarized_members IF NOT EXISTS LIST OF STRING;
CREATE PROPERTY Community.member_count IF NOT EXISTS INTEGER;
CREATE PROPERTY Community.embedding IF NOT EXISTS LIST OF DOUBLE;
CREATE PROPERTY Community.dirty_since IF NOT EXISTS STRING;
-- Ordering key for the recompute queue; dirty_since ties too easily to order by.
CREATE PROPERTY Community.dirty_seq IF NOT EXISTS INTEGER;
CREATE PROPERTY Community.updated_at IF NOT EXISTS STRING;
CREATE INDEX IF NOT EXISTS ON Community (community_id) UNIQUE;
-- fast dirty scan: the maintenance loop hits this on every tick
CREATE INDEX IF NOT EXISTS ON Community (dirty) NOTUNIQUE;

-- ------------------------------------------------------------------ audit trails
CREATE DOCUMENT TYPE ResolutionDecision IF NOT EXISTS;
CREATE PROPERTY ResolutionDecision.candidate_name IF NOT EXISTS STRING;
CREATE PROPERTY ResolutionDecision.decision IF NOT EXISTS STRING;
CREATE PROPERTY ResolutionDecision.at IF NOT EXISTS STRING;

CREATE DOCUMENT TYPE CostRecord IF NOT EXISTS;
CREATE PROPERTY CostRecord.operation IF NOT EXISTS STRING;
CREATE PROPERTY CostRecord.tokens_in IF NOT EXISTS INTEGER;
CREATE PROPERTY CostRecord.tokens_out IF NOT EXISTS INTEGER;
CREATE PROPERTY CostRecord.wall_ms IF NOT EXISTS DOUBLE;
CREATE PROPERTY CostRecord.usd IF NOT EXISTS DOUBLE;
CREATE PROPERTY CostRecord.at IF NOT EXISTS STRING;
CREATE INDEX IF NOT EXISTS ON CostRecord (operation) NOTUNIQUE;

-- ------------------------------------------------------------------ edges
CREATE EDGE TYPE PART_OF IF NOT EXISTS;

CREATE EDGE TYPE MENTIONS IF NOT EXISTS;
CREATE PROPERTY MENTIONS.confidence IF NOT EXISTS DOUBLE;

-- The provenance list on RELATES_TO is the retraction lever: strip chunk ids on delete,
-- and when the list empties the edge is garbage.
CREATE EDGE TYPE RELATES_TO IF NOT EXISTS;
CREATE PROPERTY RELATES_TO.relation_id IF NOT EXISTS STRING;
CREATE PROPERTY RELATES_TO.relation_type IF NOT EXISTS STRING;
CREATE PROPERTY RELATES_TO.description IF NOT EXISTS STRING;
CREATE PROPERTY RELATES_TO.descriptions IF NOT EXISTS MAP;
CREATE PROPERTY RELATES_TO.weight IF NOT EXISTS DOUBLE;
CREATE PROPERTY RELATES_TO.provenance IF NOT EXISTS LIST OF STRING;
CREATE PROPERTY RELATES_TO.source_id IF NOT EXISTS STRING;
CREATE PROPERTY RELATES_TO.target_id IF NOT EXISTS STRING;
CREATE PROPERTY RELATES_TO.updated_at IF NOT EXISTS STRING;
CREATE INDEX IF NOT EXISTS ON RELATES_TO (relation_id) UNIQUE;

CREATE EDGE TYPE IN_COMMUNITY IF NOT EXISTS;
