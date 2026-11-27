-- 단가표 v3 (2026-11-27): 장당 과금의 두 번째 고화소 구간.
-- Seedream 5.5 가 4K 를 열면서 구간이 셋이 됐다(BytePlus 5.5 문서 4.2):
--   <= 1536²x1.05² (2.61MP) $0.020 · <= 2048²x1.05² (4.63MP) $0.035 · <= 2880² (8.30MP) $0.079
-- 출력 픽셀이 px_threshold2 를 넘으면 per_image_hi2, 아니면 예전처럼 px_threshold / per_image_hi.
-- 0 이면 그 구간 없음 — 다른 모델은 아무것도 바뀌지 않는다.
ALTER TABLE prices ADD COLUMN per_image_hi2 REAL NOT NULL DEFAULT 0;
ALTER TABLE prices ADD COLUMN px_threshold2 INTEGER NOT NULL DEFAULT 0;
