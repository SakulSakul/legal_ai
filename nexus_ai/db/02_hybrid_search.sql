-- ============================================================
--  NEXUS AI · Hybrid Search (Vector + Keyword) with RRF
--  - vector: pgvector cosine
--  - keyword: pg_bigm (없으면 pg_trgm fallback)
--  - 결합: Reciprocal Rank Fusion (k 기본 60)
-- ============================================================

create or replace function nexus_hybrid_search(
  query_text   text,
  query_embed  vector(768),
  filter_categories nexus_category[] default null,
  top_k        int  default 3,
  fanout       int  default 30,
  rrf_k        int  default 60,
  fallback_to_common boolean default true
)
returns table (
  chunk_id    uuid,
  document_id uuid,
  doc_title   text,
  doc_kind    nexus_doc_kind,
  article_no  text,
  case_no     text,
  text        text,
  score       double precision
) language plpgsql stable as $$
declare
  effective_categories nexus_category[];
  has_hits boolean;
begin
  effective_categories := filter_categories;

  -- 1차 시도: 지정 카테고리
  return query
  with vec as (
    select c.id, row_number() over (order by c.embedding <=> query_embed) as r
      from nexus_chunks c
      join nexus_documents d on d.id = c.document_id
     where d.status = 'active'
       and (effective_categories is null
            or c.categories && effective_categories)
     order by c.embedding <=> query_embed
     limit fanout
  ),
  kw as (
    select c.id,
           row_number() over (order by similarity(c.text, query_text) desc) as r
      from nexus_chunks c
      join nexus_documents d on d.id = c.document_id
     where d.status = 'active'
       and (effective_categories is null
            or c.categories && effective_categories)
       and (c.text % query_text or c.text_tsv @@ plainto_tsquery('simple', query_text))
     order by similarity(c.text, query_text) desc
     limit fanout
  ),
  fused as (
    select coalesce(vec.id, kw.id) as id,
           coalesce(1.0/(rrf_k + vec.r), 0) + coalesce(1.0/(rrf_k + kw.r), 0) as s
      from vec full outer join kw on vec.id = kw.id
  )
  select c.id, c.document_id, d.title, d.doc_kind, c.article_no, c.case_no, c.text, f.s
    from fused f
    join nexus_chunks c on c.id = f.id
    join nexus_documents d on d.id = c.document_id
   order by f.s desc
   limit top_k;

  get diagnostics has_hits = row_count;

  -- 카테고리 fallback: 결과 0건이고 fallback_to_common 옵션이 켜져 있으면
  -- '공통' 카테고리로 1회 재시도
  if (not has_hits) and fallback_to_common
     and (filter_categories is not null)
     and not ('공통' = any(filter_categories)) then

    return query
    with vec as (
      select c.id, row_number() over (order by c.embedding <=> query_embed) as r
        from nexus_chunks c
        join nexus_documents d on d.id = c.document_id
       where d.status = 'active' and c.categories && array['공통']::nexus_category[]
       order by c.embedding <=> query_embed
       limit fanout
    ),
    kw as (
      select c.id,
             row_number() over (order by similarity(c.text, query_text) desc) as r
        from nexus_chunks c
        join nexus_documents d on d.id = c.document_id
       where d.status = 'active' and c.categories && array['공통']::nexus_category[]
         and (c.text % query_text or c.text_tsv @@ plainto_tsquery('simple', query_text))
       order by similarity(c.text, query_text) desc
       limit fanout
    ),
    fused as (
      select coalesce(vec.id, kw.id) as id,
             coalesce(1.0/(rrf_k + vec.r), 0) + coalesce(1.0/(rrf_k + kw.r), 0) as s
        from vec full outer join kw on vec.id = kw.id
    )
    select c.id, c.document_id, d.title, d.doc_kind, c.article_no, c.case_no, c.text, f.s
      from fused f
      join nexus_chunks c on c.id = f.id
      join nexus_documents d on d.id = c.document_id
     order by f.s desc
     limit top_k;
  end if;
end;
$$;
