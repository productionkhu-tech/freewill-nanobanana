-- 실제로 청구되는 만큼 기록하기 위한 칸. (운영 D1 에는 2026-10-07 적용)
--
-- out_text_tokens: 텍스트·생각(thinking) 출력 토큰. Gemini 이미지 모델은 생각 토큰을 따로 청구하고
--   (예: Nano Banana 2.1 생각 1,000토큰 안팎), 응답 텍스트도 이미지보다 싼 텍스트 단가로 청구한다.
--   예전엔 생각 토큰은 아예 안 적었고, 텍스트는 이미지 단가로 계산했다.
-- out_text_per_m: 그 토큰의 단가 (1M 당).
-- images = 0 인 행: 이미지는 안 나왔지만 청구된 시도(Gemini 무이미지 재시도 등).
ALTER TABLE usage_events ADD COLUMN out_text_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE prices ADD COLUMN out_text_per_m REAL NOT NULL DEFAULT 0;
