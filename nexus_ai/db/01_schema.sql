-- ============================================================
--  NEXUS AI · Phase 1 DB Schema
--  Supabase (PostgreSQL + pgvector) — 사규/사례/징계 통합 + 버전관리
-- ============================================================

create extension if not exists vector;
create extension if not exists pg_trgm;
-- pg_bigm 은 Supabase 가용성에 따라 활성화. 미가용 시 본 라인 주석 처리하고
--   parser/docx_parser.py 의 한국어 토크나이저 fallback 경로를 사용한다.
create extension if not exists pg_bigm;

-- ── 카테고리 enum (9개 대분류) ───────────────────────────────
do $$ begin
  create type nexus_category as enum (
    '공통','CSR','공정거래','정보보안','안전','재무','영업','총무','환경'
  );
exception when duplicate_object then null; end $$;

-- 문서 메타 종류 (사규/사례/징계). 신고절차 문서는 의도적으로 제외하기 위해
-- ingestion 단계에서 별도 'excluded' 태그를 달고 본 테이블에 적재하지 않는다.
do $$ begin
  create type nexus_doc_kind as enum ('rule','case','penalty');
exception when duplicate_object then null; end $$;

do $$ begin
  create type nexus_doc_status as enum ('active','archived');
exception when duplicate_object then null; end $$;

-- ── 문서(Document) ───────────────────────────────────────────
create table if not exists nexus_documents (
  id              uuid primary key default gen_random_uuid(),
  title           text not null,
  doc_kind        nexus_doc_kind not null,
  source_filename text,
  version         text not null default 'v1',
  effective_date  date,
  superseded_by   uuid references nexus_documents(id),
  status          nexus_doc_status not null default 'active',
  uploaded_by     text,
  uploaded_at     timestamptz not null default now(),
  meta            jsonb not null default '{}'::jsonb
);

create index if not exists idx_nexus_documents_status
  on nexus_documents (status);
create index if not exists idx_nexus_documents_kind_status
  on nexus_documents (doc_kind, status);

-- ── 청크(Chunk) ─ 조항 단위 임베딩 + FTS ─────────────────────
-- 임베딩 차원은 환경변수 NEXUS_EMBED_DIM 과 일치해야 한다.
-- 기본은 768 (gemini-embedding-001 / MRL 768).
create table if not exists nexus_chunks (
  id            uuid primary key default gen_random_uuid(),
  document_id   uuid not null references nexus_documents(id) on delete cascade,
  chunk_idx     int  not null,
  article_no    text,                       -- 예: "제4조"
  case_no       text,                       -- 사례집 영구번호 (예: "#102")
  categories    nexus_category[] not null,  -- 한 청크 다중 카테고리 허용
  text          text not null,
  text_tsv      tsvector,
  embedding     vector(768),
  token_count   int,
  created_at    timestamptz not null default now()
);

-- 카테고리 GIN
create index if not exists idx_nexus_chunks_categories
  on nexus_chunks using gin (categories);

-- 한국어 BM-style 검색을 위한 bigm/trgm 인덱스. pg_bigm 미가용 환경에서는
-- pg_trgm 인덱스만으로도 동작 가능 (정확도 일부 저하).
do $$ begin
  execute 'create index if not exists idx_nexus_chunks_text_bigm '
       || 'on nexus_chunks using gin (text gin_bigm_ops)';
exception when undefined_object then
  execute 'create index if not exists idx_nexus_chunks_text_trgm '
       || 'on nexus_chunks using gin (text gin_trgm_ops)';
end $$;

-- tsvector FTS 인덱스 (영문/숫자 토큰 + 보조용)
create index if not exists idx_nexus_chunks_tsv
  on nexus_chunks using gin (text_tsv);

-- 벡터 검색용 IVF 인덱스 (데이터 적재 후 수동 reindex 권장)
do $$ begin
  execute 'create index if not exists idx_nexus_chunks_embedding '
       || 'on nexus_chunks using ivfflat (embedding vector_cosine_ops) with (lists = 100)';
exception when others then null; end $$;

-- 자동 tsvector 유지
create or replace function nexus_chunks_tsv_trigger() returns trigger as $$
begin
  new.text_tsv := to_tsvector('simple', coalesce(new.text,''));
  return new;
end $$ language plpgsql;

drop trigger if exists trg_nexus_chunks_tsv on nexus_chunks;
create trigger trg_nexus_chunks_tsv
  before insert or update of text on nexus_chunks
  for each row execute function nexus_chunks_tsv_trigger();

-- ── 심각 사안 키워드 사전 ───────────────────────────────────
do $$ begin
  create type nexus_critical_kind as enum ('safety','harassment');
exception when duplicate_object then null; end $$;

create table if not exists critical_keywords (
  id          bigserial primary key,
  kind        nexus_critical_kind not null,
  keyword     text not null,
  is_active   boolean not null default true,
  updated_at  timestamptz not null default now(),
  unique (kind, keyword)
);

-- 초안 시드 (Phase 5 후속 확정 항목 — 도메인 전문가 검수 후 보강)
insert into critical_keywords (kind, keyword) values
  ('safety','중대재해'),('safety','사망'),('safety','부상'),('safety','추락'),
  ('safety','화재'),('safety','누출'),('safety','응급'),
  ('harassment','갑질'),('harassment','따돌림'),('harassment','폭언'),
  ('harassment','폭행'),('harassment','모욕'),('harassment','성희롱'),
  ('harassment','성추행'),('harassment','강제추행'),('harassment','스토킹')
on conflict (kind, keyword) do nothing;

-- ── 핫라인/안내 문구 중앙 관리 ──────────────────────────────
create table if not exists hotline_config (
  key         text primary key,
  value       text not null,
  description text,
  updated_at  timestamptz not null default now()
);

insert into hotline_config (key, value, description) values
  ('internal_report_url',   'https://example.invalid/report',   '사내 익명 제보채널 URL (placeholder)'),
  ('external_hotline',      '고용노동부 1350',                   '외부 상담채널 (placeholder)'),
  ('ethics_hotline_url',    'https://example.invalid/ethics',   '윤리팀 익명 제보채널 (placeholder)'),
  ('hr_contact_text',       '신고·조사 절차 등 인사 행정 사항은 인사팀에 직접 문의하시기 바랍니다.',
                            '인사 챗봇 오픈 시 본 문구 교체하면 즉시 반영'),
  ('hr_chatbot_url',        '',                                 '인사 챗봇 URL (오픈 시점 미정)')
on conflict (key) do nothing;

-- ── 질의 로그(마스킹 후) — 트렌드 레이더용 ──────────────────
create table if not exists query_logs (
  id            bigserial primary key,
  ts            timestamptz not null default now(),
  category      nexus_category,
  query_masked  text not null,
  is_critical   boolean not null default false,
  critical_kind nexus_critical_kind,
  hit_chunk_ids uuid[],
  -- 부서/사용자 식별자는 저장하지 않거나, 저장 시 해시만 저장한다.
  dept_hash     text
);

create index if not exists idx_query_logs_ts on query_logs (ts desc);
create index if not exists idx_query_logs_category_ts on query_logs (category, ts desc);

-- 사례집-사규 인용 정합성 점검용 뷰 (간단 버전)
create or replace view v_case_citations as
select
  c.id            as case_chunk_id,
  c.document_id   as case_doc_id,
  c.case_no,
  regexp_matches(c.text, '제\s*([0-9]+)\s*조', 'g') as cited_article
from nexus_chunks c
join nexus_documents d on d.id = c.document_id
where d.doc_kind = 'case' and d.status = 'active';
