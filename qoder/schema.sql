-- ScholarPulse Qoder Sites 托管库结构（Function backend / app schema）
-- 仅包含受支持的 DDL 子集：CREATE TABLE 与普通列索引。语料种子数据由 Function 侧写入。

CREATE TABLE app.scholar_documents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  doc_id text NOT NULL UNIQUE,
  scope_id text NOT NULL,
  paper_title text NOT NULL,
  section text NOT NULL,
  topic text NOT NULL,
  content text NOT NULL,
  key_takeaway text NOT NULL,
  flashcard_json jsonb NOT NULL,
  powercontext_source_ref text NOT NULL,
  embedding jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX scholar_documents_scope_topic_idx ON app.scholar_documents (scope_id, topic);

CREATE TABLE app.learner_memories (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  learner_id text NOT NULL UNIQUE,
  scope_id text NOT NULL,
  weak_topics jsonb NOT NULL,
  mastered_doc_ids jsonb NOT NULL,
  preferred_depth text NOT NULL,
  review_note text NOT NULL,
  cards jsonb NOT NULL,
  powercontext_source_ref text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX learner_memories_scope_idx ON app.learner_memories (scope_id);
