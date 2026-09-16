-- 게이트웨이 테스트가 남긴 흔적 정리.
--
-- test_usage / test_cost 는 운영 워커와 D1 을 실제로 때린다(그게 목적이다).
-- 대신 모든 흔적에 zzTEST 라는 이름과 1999~2001 이라는 날짜를 붙여 실제 집계
-- 기간과 절대 겹치지 않게 해 두었다. 테스트를 돌린 뒤 이 파일로 치운다.
--
--   npx wrangler d1 execute nanobanana-usage --remote --file=./purge_test_rows.sql
--
-- 순서가 중요하다: 사용 행을 먼저 지워야 팀/프로젝트 행이 지워진다
-- (사용 기록이 붙어 있으면 지우지 않는 게 기본 정책이라 여기서도 같은 조건을 쓴다).

DELETE FROM usage_events WHERE model = 'zztest-model';
DELETE FROM usage_events WHERE team_id LIKE 'zztest%' OR project_id LIKE 'zztest%';
DELETE FROM prices WHERE model = 'zztest-model';

-- id 는 이름을 슬러그로 바꾼 값이라, 이름을 바꿔 가며 돌린 옛 행은 id 가 zztest 로
-- 시작하지 않을 수 있다(예: 이름 zzTEST건A, id fiji-캠페인). 이름으로도 걸러낸다.
DELETE FROM projects
 WHERE (id LIKE 'zztest%' OR name LIKE 'zzTEST%' OR id LIKE '회수테스트%' OR id LIKE '임시테스트%')
   AND id NOT IN (SELECT DISTINCT project_id FROM usage_events);
DELETE FROM teams
 WHERE (id LIKE 'zztest%' OR name LIKE 'zzTEST%' OR id LIKE '회수테스트%' OR id LIKE '임시테스트%')
   AND id NOT IN (SELECT DISTINCT team_id FROM usage_events);
