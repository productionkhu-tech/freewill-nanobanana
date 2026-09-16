-- NanoBanana 사용량 집계 스키마 (Cloudflare D1)
--
-- 설계 원칙: 돈을 저장하지 않는다. 프로바이더가 돌려준 원본 사용량만 적고,
-- 단가는 조회 시점에 곱한다. 단가가 바뀌거나 잘못 넣었을 때 과거 데이터까지
-- 한 번에 바로잡을 수 있는 유일한 방법이다.

-- 팀: 관리자 페이지에서 추가/삭제하면 앱이 다음 실행에 바로 받아간다.
CREATE TABLE IF NOT EXISTS teams (
  id         TEXT PRIMARY KEY,          -- 'design', 'marketing' 같은 슬러그
  name       TEXT NOT NULL,             -- 화면에 보이는 이름
  active     INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

-- 프로젝트: 팀에 속한다. 비용 귀속의 단위.
CREATE TABLE IF NOT EXISTS projects (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  team_id    TEXT NOT NULL REFERENCES teams(id),
  active     INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_projects_team ON projects(team_id, active);

-- 이미지 한 장 = 한 행.
-- team_id 를 함께 박아두는 건 의도적인 비정규화다. 프로젝트가 나중에 다른 팀으로
-- 옮겨가도 "그 시점에 어느 팀이 썼는가" 라는 과거 사실이 바뀌면 안 된다.
CREATE TABLE IF NOT EXISTS usage_events (
  id              TEXT PRIMARY KEY,     -- 앱이 만든 고유값. 재전송해도 중복 안 쌓임
  ts              TEXT NOT NULL,        -- ISO8601 UTC
  day             TEXT NOT NULL,        -- 'YYYY-MM-DD', 집계 인덱스용
  team_id         TEXT NOT NULL,
  project_id      TEXT NOT NULL,
  token_id        TEXT,                 -- 게이트웨이 토큰 → 사람/PC 는 워커가 붙인다
  user            TEXT,
  machine         TEXT,
  provider        TEXT NOT NULL,        -- openai | seedream | vertex | studio
  model           TEXT NOT NULL,
  size            TEXT,
  quality         TEXT,
  images          INTEGER NOT NULL DEFAULT 1,
  in_text_tokens  INTEGER NOT NULL DEFAULT 0,
  in_image_tokens INTEGER NOT NULL DEFAULT 0,
  out_tokens      INTEGER NOT NULL DEFAULT 0,
  elapsed_ms      INTEGER,
  app_version     TEXT,
  created_at      TEXT NOT NULL         -- 워커가 받은 시각 (앱 시계와 분리)
);
CREATE INDEX IF NOT EXISTS idx_usage_day      ON usage_events(day);
CREATE INDEX IF NOT EXISTS idx_usage_team_day ON usage_events(team_id, day);
CREATE INDEX IF NOT EXISTS idx_usage_proj_day ON usage_events(project_id, day);
CREATE INDEX IF NOT EXISTS idx_usage_model    ON usage_events(model, day);
