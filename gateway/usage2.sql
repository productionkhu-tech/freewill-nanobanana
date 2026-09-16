-- 사용량 v2: 앱이 이미 보내고 있던 값을 실제로 저장한다.
--
-- 앱은 처음부터 out_px(출력 픽셀)와 ref_images(레퍼런스 장수)를 보내고 있었는데
-- 워커의 INSERT 에 칸이 없어 그대로 버려졌다. 그래서
--   * seedream-5-0-pro 의 픽셀 구간제(2.61M px 초과 $0.09)가 한 번도 적용된 적이 없고
--     4K 이미지가 전부 절반 값($0.045)으로 잡혔다
--   * 레퍼런스 2장째부터 붙는 $0.003 도 계산에 들어간 적이 없다
-- 지난 행은 0 으로 남는다(그 값을 지금 되살릴 방법이 없다). 이 마이그레이션
-- 이후 들어오는 행부터 정확해진다.

ALTER TABLE usage_events ADD COLUMN out_px     INTEGER NOT NULL DEFAULT 0;
ALTER TABLE usage_events ADD COLUMN ref_images INTEGER NOT NULL DEFAULT 0;
