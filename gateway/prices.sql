-- 단가표. 금액을 이벤트에 박지 않고 여기서 조회 시점에 곱한다.
-- verified=0 인 행은 공식 문서로 확인 못 한 값이라 관리자 페이지에서 고쳐야 한다.
CREATE TABLE IF NOT EXISTS prices (
  model          TEXT PRIMARY KEY,
  provider       TEXT NOT NULL,
  mode           TEXT NOT NULL,              -- 'token' | 'image'
  in_text_per_m  REAL NOT NULL DEFAULT 0,    -- $ / 1M 텍스트 입력 토큰
  in_image_per_m REAL NOT NULL DEFAULT 0,    -- $ / 1M 이미지 입력 토큰
  out_per_m      REAL NOT NULL DEFAULT 0,    -- $ / 1M 이미지 출력 토큰
  per_image      REAL NOT NULL DEFAULT 0,    -- $ / 장  (mode='image')
  verified       INTEGER NOT NULL DEFAULT 0,
  note           TEXT,
  updated_at     TEXT NOT NULL
);

INSERT OR REPLACE INTO prices
  (model, provider, mode, in_text_per_m, in_image_per_m, out_per_m, per_image, verified, note, updated_at)
VALUES
  ('gpt-image-2',            'openai',   'token', 5.0, 8.0, 30.0,  0, 1, 'OpenAI pricing page 2026-09', datetime('now')),
  ('gpt-image-2.5-flare',    'openai',   'token', 5.0, 8.0, 30.0,  0, 1, 'Token rates match GPT Image 2', datetime('now')),
  ('gpt-image-2.5-sunburst', 'openai',   'token', 5.0, 8.0, 30.0,  0, 1, 'Token rates match GPT Image 2', datetime('now')),
  ('gemini-3-pro-image',         'gemini', 'token', 2.0,  2.0, 120.0, 0, 1, '1K/2K $0.134, 4K $0.24', datetime('now')),
  ('gemini-3.1-flash-image',     'gemini', 'token', 0.5,  0.5,  60.0, 0, 1, '1K $0.067 / 2K $0.101 / 4K $0.151', datetime('now')),
  ('gemini-3.1-flash-lite-image','gemini', 'token', 0.25, 0.25, 30.0, 0, 1, '1K $0.0336', datetime('now')),
  ('gemini-2.5-flash-image',     'gemini', 'token', 0.0,  0.0,  30.0, 0, 0, '확인 필요 — 구형 모델, 공식 표에서 못 찾음', datetime('now')),
  ('seedream-5-0-pro-260628','byteplus', 'image', 0, 0, 0, 0.03, 0, '확인 필요 — BytePlus 콘솔에서 실제 단가 입력', datetime('now')),
  ('seedream-4-5-251128',    'byteplus', 'image', 0, 0, 0, 0.03, 0, '확인 필요 — BytePlus 콘솔에서 실제 단가 입력', datetime('now'));
