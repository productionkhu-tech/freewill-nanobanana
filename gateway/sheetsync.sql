-- 구글 시트 동기화용 스키마.
--
-- source: 이 행을 누가 만들었나. 'sheet' 면 동기화가 관리하고, 그 외(손으로 추가)는
-- 동기화가 건드리지 않는다. 안 나누면 관리자 페이지에서 급하게 추가한 건이
-- 다음 날 크론에 조용히 보관으로 내려간다.
ALTER TABLE teams    ADD COLUMN source TEXT;
ALTER TABLE projects ADD COLUMN source TEXT;

-- 마지막 동기화 결과. 관리자 화면에 "언제, 잘 됐나" 를 보여주려면 필요하다 —
-- 조용히 실패하면 목록이 며칠씩 낡은 채로 남는다.
CREATE TABLE IF NOT EXISTS sync_state (
  id     TEXT PRIMARY KEY,   -- 'sheet'
  at     TEXT NOT NULL,
  ok     INTEGER NOT NULL,
  detail TEXT
);
