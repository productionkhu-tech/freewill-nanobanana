-- PC 마다 "평문 키가 남았는가" (v2026-10-0802). 관리자 페이지 PC 탭의 "평문 키" 칸.
--
-- 앱이 정리(scrub_plaintext)를 한 뒤 남은 것의 **이름만** 보낸다 — 값은 절대 오지 않는다.
-- 빈 배열 = 정리됨. 행이 없으면 아직 새 버전으로 켜지지 않은 PC(확인 전).
-- KV 가 아니라 D1: KV 는 하루 쓰기 1,000번 한도라 PC 마다 쓰는 기록엔 아껴 쓴다.
CREATE TABLE IF NOT EXISTS pc_plain (
  token_id    TEXT PRIMARY KEY,
  left_json   TEXT NOT NULL,   -- JSON 배열: ["OPENAI_API_KEY", "SYSTEM:ARK_API_KEY", "file:service_account.json", ...]
  checked_at  TEXT NOT NULL,
  app_version TEXT
);
