# NanoBanana — AI Image Studio

멀티 프로바이더 AI 이미지 생성 데스크톱 앱.
Flask 로컬 서버 + pywebview(WebView2) 네이티브 창, Windows는 단일 EXE로 배포됩니다.

| 프로바이더 | 모델 |
|---|---|
| Google Gemini | gemini-3-pro-image / 3.1-flash / 3.1-flash-lite / 2.5-flash (Vertex + AI Studio 이중 연동, 자동 폴백) |
| OpenAI | gpt-image-2 |
| BytePlus | Seedream 5.0 Pro / 4.5 |
| Reve | Reve 2.1 (reve-create) |

## 주요 기능
- 배치 생성 큐 (최대 100장 대기, 병렬 처리, Stop 즉시 반응)
- 레퍼런스 이미지 슬롯 — 드래그앤드롭 · 클립보드 · 웹 이미지, `[Image N]` 멘션, **호버 확대 미리보기**
- 종횡비/해상도/Custom 픽셀 (모델별 실제 API 한도에 맞춰 자동 보정)
- 갤러리 — 검색, 즐겨찾기, 1/2/4/8열, 마퀴 선택
- 프리뷰 창 — 줌/팬, 휠 이동, **좌우 와이프 비교**, 클립보드 복사
- 프로젝트 저장/불러오기(JSON) + 생성 중 15초 자동저장
- **자동 업데이트 — 앱을 껐다 켜기만 하면 최신 버전이 설치됩니다**

## 설치 (Windows 사용자)
1. `NanoBanana.exe` 와 키 설치 스크립트를 같은 폴더에 둡니다 (Program Files 아래는 피할 것)
2. 키 설치 스크립트를 1회 실행
3. `NanoBanana.exe` 더블클릭

## 설치 (맥 사용자)
`맥_실행_가이드.md` 참고. 요약: python.org Python 3.10+ 설치 →
`pip3 install -r requirements_mac.txt` → `keys.env` 배치 → `NanoBanana.command` 더블클릭.

## 개발자
- 개발 규칙·구조·배포 절차: **[CLAUDE.md](CLAUDE.md)**
- 배포 실행 절차: `.claude/skills/나노바나나api배포/SKILL.md`
- 현행 인수인계: `인수인계_2026-07-30_전체.md`

소스에서 실행:
```
python launcher.py      # Windows (pywebview 창)
python3 server_mac.py   # macOS (브라우저)
```

현재 버전은 `VERSION` 파일 참조. 릴리스: https://github.com/productionkhu-tech/freewill-nanobanana/releases
