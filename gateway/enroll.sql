-- 새 PC 승인 요청 (v2026-10 키 보안 개편).
--
-- 앱이 요청을 올리면 그 PC 화면에 4자리 번호(code)가 뜨고, 관리자가 관리자 페이지에
-- 그 번호를 넣어야 출입증(token)이 나간다. req_id 는 요청한 앱만 아는 128비트 값이라
-- 승인된 출입증은 그 앱만 받아간다. token 칸은 앱이 받아가는 순간 비운다.
--
-- KV 가 아니라 D1 인 이유: KV 는 다른 지역에서 쓴 값이 1분까지 늦게 보일 수 있어서,
-- 방금 올라온 요청을 관리자가 못 찾거나 번호가 겹칠 수 있다.
CREATE TABLE IF NOT EXISTS enroll_requests (
  req_id       TEXT PRIMARY KEY,
  code         TEXT NOT NULL,
  user         TEXT,
  machine      TEXT,
  app_version  TEXT,
  ip           TEXT,
  created_at   TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | denied
  token        TEXT,
  token_id     TEXT,
  decided_at   TEXT,
  picked_up_at TEXT
);
CREATE INDEX IF NOT EXISTS enroll_requests_code ON enroll_requests (code, status);
CREATE INDEX IF NOT EXISTS enroll_requests_ip ON enroll_requests (ip, created_at);
