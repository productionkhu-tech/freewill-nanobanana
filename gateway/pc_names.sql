-- 등록된 PC 의 "지금" 컴퓨터 이름. (운영 D1 에는 2026-09-30 에 적용)
--
-- 사용 행과 토큰에 찍히는 이름은 처음 등록할 때 것이라, PC 이름을 바꿔도 관리자 페이지엔
-- 옛 이름만 남았다. 앱은 켤 때마다 /key 요청에 지금 이름(platform.node())을 싣는데,
-- 워커가 그걸 여기 적어 두고 리포트는 "지금 이름 (처음 이름)" 으로 보여준다.
-- 표시용이다 — 비용 귀속과는 무관하다.
CREATE TABLE IF NOT EXISTS pc_names (
  token_id   TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
