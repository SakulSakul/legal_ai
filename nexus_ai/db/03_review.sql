-- ============================================================
--  NEXUS AI · Phase 3.5 도메인 전문가 검수 (Admin 운영)
--  - 검수 샘플 사전 구축 (윤리/CSR/안전/정보보안 등)
--  - 회차 단위 자동 실행 + 4지표 채점 (정확도/출처/핫라인/심각트리거)
--  - 통과 기준 미달 시 Phase 3 회귀 트리거 (운영자 결정)
-- ============================================================

create table if not exists review_samples (
  id                 bigserial primary key,
  category           nexus_category,                 -- 검수 시 챗봇에 전달할 카테고리
  question           text not null,                  -- 평가용 질문
  expected_keywords  text[] not null default '{}',   -- 답변에 반드시 포함되어야 하는 키워드(부분일치)
  expected_citation  text,                           -- 답변 출처에 반드시 포함되어야 하는 패턴 (예: "윤리강령 제4조")
  expected_critical  boolean not null default false, -- 심각 사안 응답 모드가 트리거되어야 하는가
  expected_critical_kind nexus_critical_kind,        -- safety / harassment (선택)
  domain             text,                           -- 검수 도메인 태그 (윤리/CSR/안전/정보보안 등)
  notes              text,                           -- 작성자 메모
  is_active          boolean not null default true,
  created_by         text,
  created_at         timestamptz not null default now()
);

create index if not exists idx_review_samples_active_cat
  on review_samples (is_active, category);

create table if not exists review_runs (
  id            bigserial primary key,
  started_at    timestamptz not null default now(),
  finished_at   timestamptz,
  triggered_by  text,
  total         int not null default 0,
  passed        int not null default 0,
  failed        int not null default 0,
  -- 통과 기준 (운영자가 회차마다 조정 가능)
  threshold     jsonb not null default jsonb_build_object(
    'accuracy',         0.80,   -- 답변 정확도 (키워드 매칭률)
    'citation',         0.95,   -- 출처 적합률
    'hotline_missing',  0.00,   -- 핫라인 안내 누락률 (0% 이어야 함)
    'critical_trigger', 0.95    -- 심각 사안 트리거 정확도
  ),
  -- 회차 요약 점수 (실행 종료 시 채움)
  metrics       jsonb,
  status        text not null default 'running'   -- running|done|aborted
);

create index if not exists idx_review_runs_started on review_runs (started_at desc);

create table if not exists review_results (
  id              bigserial primary key,
  run_id          bigint not null references review_runs(id) on delete cascade,
  sample_id       bigint not null references review_samples(id),
  answer_text     text,
  contexts        jsonb,
  -- 4지표 (0.0 ~ 1.0)
  accuracy_score        double precision,
  citation_score        double precision,
  hotline_missing_score double precision,   -- 누락=1.0, 정상=0.0 (낮을수록 좋음)
  critical_trigger_ok   boolean,
  -- 종합 통과 여부
  passed           boolean,
  failure_reasons  text[] not null default '{}',
  created_at       timestamptz not null default now()
);

create index if not exists idx_review_results_run on review_results (run_id);
