-- 시트의 프로젝트 ID(H열, PJ-…)를 프로젝트 키로 쓰면서 생긴 별칭표.
-- (운영 D1 에는 2026-09-23 에 적용됨)
--
-- 예전 id(건 번호 26p50, 이름 슬러그 26tf04-afx-unt-test …)로 쌓인 사용 기록은
-- 동기화가 PJ id 로 옮기고, 옮긴 옛 id 를 여기 남긴다. 이게 있어야
--   - 아직 옛 id 를 들고 있는 앱이 보낸 사용량도 새 id 로 들어오고 (/usage)
--   - 앱이 탭에 저장된 옛 id 를 같은 프로젝트로 알아보고 (/catalog 의 aliases)
--   - 시트의 H열이 실수로 비어도 그 건이 옛 id 로 되돌아가 갈라지지 않는다.
CREATE TABLE IF NOT EXISTS project_aliases (
  alias      TEXT PRIMARY KEY,   -- 옛 id
  project_id TEXT NOT NULL,      -- PJ id
  created_at TEXT NOT NULL
);
