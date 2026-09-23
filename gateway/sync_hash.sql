-- sheetsync.sql 뒤에 붙은 두 칸. (운영 D1 에는 실시간 동기화 때 이미 적용됨 —
-- 파일로 안 남아 있어서, DB 를 새로 만들면 동기화가 INSERT 에서 깨졌을 것이다.)
--
-- hash: 시트 내용의 지문. 1분마다 도는 크론이 내용이 그대로면 D1 에 아무것도
--       쓰지 않게 한다 (무료 쓰기 한도 보호).
-- changed_at: 시트가 실제로 바뀌어 반영된 마지막 시각. "마지막 확인" 과 따로 보여준다.
ALTER TABLE sync_state ADD COLUMN hash TEXT;
ALTER TABLE sync_state ADD COLUMN changed_at TEXT;
