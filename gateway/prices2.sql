-- 단가표 v2: 시점별 단가 + 픽셀 구간 + 입력이미지 과금 + 환율
--
-- 왜 시점(effective_from)이 필요한가: 단가는 바뀐다(BytePlus 는 실제로 8월에
-- 바꿨다). 행을 덮어쓰면 지난달 집계가 조용히 달라진다. 새 단가를 새 행으로
-- 넣고, 조회할 때 "그 날짜에 유효했던 단가" 를 고른다.

DROP TABLE IF EXISTS prices;
CREATE TABLE prices (
  model           TEXT NOT NULL,
  effective_from  TEXT NOT NULL,              -- 'YYYY-MM-DD' 부터 유효
  provider        TEXT NOT NULL,
  mode            TEXT NOT NULL,              -- 'token' | 'image'
  -- mode='token'
  in_text_per_m   REAL NOT NULL DEFAULT 0,
  in_image_per_m  REAL NOT NULL DEFAULT 0,
  out_per_m       REAL NOT NULL DEFAULT 0,
  -- mode='image' : 출력 픽셀이 px_threshold 를 넘으면 per_image_hi 를 쓴다
  per_image       REAL NOT NULL DEFAULT 0,
  per_image_hi    REAL NOT NULL DEFAULT 0,
  px_threshold    INTEGER NOT NULL DEFAULT 0, -- 0 이면 구간 없음
  -- 레퍼런스 이미지 과금 (Seedream 5 Pro: 첫 장 무료, 2장째부터 $0.003)
  in_per_image    REAL NOT NULL DEFAULT 0,
  in_free_count   INTEGER NOT NULL DEFAULT 0,
  verified        INTEGER NOT NULL DEFAULT 0,
  note            TEXT,
  updated_at      TEXT NOT NULL,
  PRIMARY KEY (model, effective_from)
);

INSERT INTO prices (model, effective_from, provider, mode,
  in_text_per_m, in_image_per_m, out_per_m,
  per_image, per_image_hi, px_threshold, in_per_image, in_free_count,
  verified, note, updated_at) VALUES
 ('gpt-image-2',            '2000-01-01','openai','token', 5.0,8.0,30.0, 0,0,0,0,0, 1,'OpenAI pricing 2026-09',datetime('now')),
 ('gpt-image-2.5-flare',    '2000-01-01','openai','token', 5.0,8.0,30.0, 0,0,0,0,0, 1,'Token rates match GPT Image 2',datetime('now')),
 ('gpt-image-2.5-sunburst', '2000-01-01','openai','token', 5.0,8.0,30.0, 0,0,0,0,0, 1,'Token rates match GPT Image 2',datetime('now')),
 ('gemini-3-pro-image',          '2000-01-01','gemini','token', 2.0,2.0,120.0, 0,0,0,0,0, 1,'1K/2K $0.134, 4K $0.24',datetime('now')),
 ('gemini-3.1-flash-image',      '2000-01-01','gemini','token', 0.5,0.5,60.0,  0,0,0,0,0, 1,'1K $0.067 / 2K $0.101 / 4K $0.151',datetime('now')),
 ('gemini-3.1-flash-lite-image', '2000-01-01','gemini','token', 0.25,0.25,30.0,0,0,0,0,0, 1,'1K $0.0336',datetime('now')),
 ('gemini-2.5-flash-image',      '2000-01-01','gemini','token', 0.0,0.0,30.0,  0,0,0,0,0, 0,'확인 필요 - 공식 표에 없음',datetime('now')),
 -- BytePlus 공식 가격표 (docs ModelArk/1099320) 확인분
 ('seedream-5-0-pro-260628','2000-01-01','byteplus','image', 0,0,0,
   0.045, 0.09, 2610000, 0.003, 1, 1,
   '출력 2.61M px(1.5K) 이하 $0.045 / 초과 $0.09. 입력이미지 2장째부터 $0.003',datetime('now')),
 ('seedream-4-5-251128',    '2000-01-01','byteplus','image', 0,0,0,
   0.04, 0, 0, 0, 0, 1, '출력 장당 $0.04, 입력이미지 무료',datetime('now'));

-- 환율: 매일 한 번 워커가 채운다. 이벤트 날짜의 환율로 환산하고, 없으면 직전 값.
CREATE TABLE IF NOT EXISTS fx_rates (
  day        TEXT PRIMARY KEY,   -- 'YYYY-MM-DD'
  usd_krw    REAL NOT NULL,
  source     TEXT,
  created_at TEXT NOT NULL
);
