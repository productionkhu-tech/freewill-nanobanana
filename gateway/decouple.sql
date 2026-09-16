-- 프로젝트를 팀에 묶지 않는다.
--
-- 팀 목록(1~10팀, AFX, TA, AIP…)은 제작 조직이고 프로젝트 목록은 클라이언트
-- 건이다. 한 건에 여러 팀이 붙는 게 정상이라, 프로젝트에 팀을 하나 박으면
-- 실제로 작업한 팀이 아닌 쪽으로 비용이 잡힌다. usage_events 는 처음부터
-- team_id 와 project_id 를 따로 들고 있으므로 "어느 팀이 어느 건에 얼마" 는
-- 그대로 나온다 — 묶을 이유가 없었던 것.
CREATE TABLE projects_new (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  team_id    TEXT,                       -- 선택: 전담 팀이 있으면 표시용
  active     INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
INSERT INTO projects_new SELECT id, name, team_id, active, created_at FROM projects;
DROP TABLE projects;
ALTER TABLE projects_new RENAME TO projects;
CREATE INDEX IF NOT EXISTS idx_projects_active ON projects(active);
