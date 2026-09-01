# NanoBanana — 개발/배포 가이드

멀티 프로바이더 AI 이미지 생성 데스크톱 앱 (Flask + pywebview + PyInstaller).
이 문서 하나로 **구조 · 원리 · 규칙 · 배포 · 관리**를 파악할 수 있게 씁니다. 낡은 내용을 발견하면 즉시 고칠 것.

> 최종 갱신: 2026-08-07 (v2026-08-0701 기준)

---

## 0. 빠른 요약 (TL;DR)

- **실행**: onefile PyInstaller EXE (`NanoBanana.exe`) + 키 설치 스크립트
- **프론트**: HTML/CSS/JS를 Flask가 서빙, pywebview(WebView2) 창에서 렌더
- **백엔드**: Flask 127.0.0.1:5656, `app.py` 단일 파일에 상태+라우트 전부
- **프로바이더 3사**: Google Gemini(Vertex/Studio) · OpenAI gpt-image-2 · BytePlus Seedream
  (Reve는 2026-09 API 서비스 종료로 제거 — 구 프로젝트의 reve-create는 로드 시 기본 모델로 자동 폴백)
- **멀티 프로젝트 탭**: 앱 상태는 `_Shared`(앱 전역) + 프로젝트별 `AppState` N개.
  `state`는 "이 요청이 다루는 프로젝트"를 가리키는 프록시라 기존 라우트는 그대로 동작
- **배포**: GitHub Release에 EXE 업로드 → 사용자는 **앱을 껐다 켜기만 하면 자동 설치** (클릭 불필요)
- **버전**: `vYYYY-MM-DDNN` — **오늘 날짜 기준**, 같은 날 재릴리스는 NN 증가, 날짜 바뀌면 01부터
- **맥**: EXE 없이 소스 실행 (`server_mac.py` + `keys.env`), git pull로 업데이트

---

## 1. 프로젝트 구조

```
나노바나나 api/                     # 프로젝트 루트 = 문서 저장 폴더
├── app.py                  # Flask 서버 + AppState(상태·로직 전부), ~4550줄, 라우트 64개
├── launcher.py             # pywebview 창 수명주기 + 단일인스턴스 + --updater 모드 + 부팅 업데이트 체크
├── updater.py              # 버전 체크 + 자산 다운로드/검증 + EXE 자기교체 + 자동설치 시도 예산
├── VERSION                 # 현재 버전 (커밋 대상, EXE에 번들됨)
├── NanoBanana.spec         # PyInstaller onefile 설정
├── static/app.js           # 프론트 로직 전부 (~3390줄)   static/style.css
├── templates/index.html    # 메인 UI
├── templates/viewer.html   # 프리뷰 창 (자체 완결 — 뷰어 로직 전부 이 파일)
├── templates/prompt_popup.html
├── server_mac.py           # 맥 전용 진입점 (Flask를 브라우저로 띄움)
├── requirements_mac.txt    NanoBanana.command    keys.env.example
├── dist/NanoBanana/        # 배포 산출물
├── _retired/               # 은퇴 소스 (빌드 제외, 절대 src로 복사 금지)
└── .claude/skills/나노바나나api배포/SKILL.md   # 배포 절차 (배포 시 반드시 따를 것)
```

### 빌드 보조 경로 (한글 경로 회피)
```
C:\NanoBanana_build\
├── src\    # 빌드 시 소스를 여기로 복사 (PyInstaller가 읽음)
└── venv\   # 빌드 전용 파이썬 ⚠ `venv` 가 맞음 (과거 표기 venv_build 는 폐기)
```

### 사용자 데이터
```
~/.nanobanana/prefs.json                 # skip_delete_confirm, prompt_history
~/.nanobanana/last_seen_version.txt      # "업데이트 완료" 팝업 판단
~/.nanobanana/auto_update_state.json     # 자동설치 시도 예산 (버전별)
~/Documents/NanoBanana JSON/             # 프로젝트 세션 JSON
~/Pictures/Screenshots/NanoBanana Clipboard/  # 클립보드·업로드 ref 캐시 (SHA1 이름)
~/Desktop/NanoBanana_Output/             # 기본 출력 폴더
%TEMP%/_MEI<random>/                     # PyInstaller 런타임 해제 폴더 (런처가 고아 청소)
%TEMP%/nanobanana_update.log             # 스왑 단계별 로그 (실패 진단용)
```

---

## 2. 동작 원리

### 부팅 흐름
```
NanoBanana.exe 실행
  ├── (모듈 로드) stdout UTF-8 재설정 + print 래퍼 + 고아 파일(.new.exe/.exe.old) 청소
  ├── sys.argv[1]=="--updater" 면 → _run_as_updater() 로 분기 (UI/Flask 없이 스왑만)
  ├── 단일 인스턴스 mutex → 이미 실행 중이면 기존 창 포커스 후 종료
  ├── API 환경변수 체크 / Program Files 경고 / WebView2 존재 확인
  ├── 포트 5656 점유 시 기존 창 포커스 후 종료 (mutex 놓친 경우 폴백)
  ├── 백그라운드 업데이트 체크 스레드 기동 (2초 지연)  ★ 절대 끄지 말 것 (§3-1)
  ├── Flask 스레드 기동 → 준비 대기(최대 15초)
  └── pywebview 창 생성 → WebView2가 http://127.0.0.1:5656 로드
```

### 프론트 ↔ 서버
- 상태는 `AppState` 싱글톤 하나에 전부 (설정·갤러리·ref 슬롯·큐·로그·이벤트)
- 통신은 **폴링**: `/api/status` 500ms, `/api/events` 800ms, `/api/logs` 2s (pagehide에서 clearInterval)
- **이벤트 큐는 pop 방식** — 소비자는 메인 창 하나뿐. 진단 목적으로도 외부에서 `/api/events`를
  폴링하면 **앱 화면의 팝업을 가로챈다.** 로그로 볼 것
- CSRF: GET 제외 모든 `/api/*` 에 `X-NB-Token` 필수 (HTML 메타에 주입, `api()` 헬퍼가 자동 첨부)
- 모든 `/api/*` 응답 no-store (WebView2 캐시 회피). `/api/*` 오류는 항상 JSON
  (404/405는 실제 상태코드로, 나머지는 500+트레이스)

### 생성 파이프라인 (큐)
```
Generate 클릭 → 프론트가 멘션 동기화 + 설정 저장 → POST /api/generate

/api/generate:
  1. 모델별 클라이언트 확인, compose_prompt() (fixed + sections)
  2. 락 밖에서 스냅샷: ref 이미지를 PNG bytes로(빈 슬롯은 None), 모델별 img_cfg 계산
  3. pending_jobs_lock 임계구역:
     - cancel_flag 중이면 거부 / 용량(100) 재확인
     - 새 배치면 카운터 리셋 + is_generating=True 선점, 아니면 기존 배치에 append
     - job N개 생성 — prompt·model·img_cfg·ref_payloads·output_dir·naming 전부 **스냅샷**
       (생성 중 사용자가 설정을 바꿔도 진행 중 job은 영향 없음)
  4. 새 배치면 gen_worker 스레드 1개 기동

gen_worker:
  - ThreadPoolExecutor(max_workers=100) 수동 관리 (with 블록 금지 → Stop 즉시 반응)
  - 완료건마다: 파일명 생성(락) → PNG 저장(메타데이터) → 갤러리 추가 → image_done 이벤트
  - 루프마다 _maybe_autosave() (15초 스로틀)
  - 종료 3분기: cancel이면 pending 폐기 / 레이스로 남은 pending 있으면 워커 재스폰 / 아니면 done

각 워커 generate_one_image:
  - 모델별 분기 → RateLimiter.acquire (Gemini 7.5s=8RPM, OpenAI 1.5s, Seedream 0.3s)
  - 재시도 최대 5회 (백오프 10→120s + 지터), Gemini는 vertex↔studio 폴백 + 무이미지 원인 로깅
```

### 레퍼런스 슬롯 모델
- `ref_images/ref_path_list/ref_pinned` 병렬 리스트, **인덱스 = 슬롯 번호**
- 삭제하면 `None` 구멍이 남고 **재번호 매김 없음** — 프롬프트의 `[Image N]`이 앵커
- 슬롯이 비면 프론트가 `[Image N]` → `@imageN`(회색 텍스트)으로 형태만 전환, 채우면 복원
- 호버 확대는 `/api/refs/preview/<idx>` — **메모리의 PIL 사본에서 서빙**하므로 출처(클립보드/갤러리/
  업로드/외부파일) 무관하고 원본 파일이 사라져도 동작

### 자동 업데이트 (v2026-07-3002 현행)
```
1) 부팅 체크 (launcher._bg_update_check)
   Releases API(1순위) → raw VERSION(폴백) 으로 최신 태그 조회
   업데이트 있으면 시도 예산 확인 후 update_status 이벤트 push (auto 플래그 포함)

2) 프론트 (app.js)
   auto=true 이고 생성 중이 아니면 → 오버레이 띄우고 즉시 POST /api/apply-update (클릭 불필요)
   auto=false (예산 소진) 이거나 생성 중이면 → 기존 확인 다이얼로그

3) 설치 (app.py /api/apply-update → updater)
   자산 URL 확보(API 403이면 조립 URL+사이드카 폴백) → 다운로드(진행률 이벤트) → sha256 검증
   → NanoBanana.new.exe 를 `--updater <old_path>` 로 스폰 → os._exit(0)

4) 스왑 (launcher._run_as_updater)
   write-probe로 부모 종료 대기 → copy2 → os.replace(20회 retry, 5회차 taskkill) → 재실행
```

**시도 예산 (무한 재시작 방지)**: `~/.nanobanana/auto_update_state.json` 에 대상 버전별 시도 횟수.
`AUTO_UPDATE_MAX_ATTEMPTS`(2) 초과 시 자동 중단하고 수동 다이얼로그로 폴백. 목표 버전 도달 시 상태 삭제.
**자산 미업로드(404)는 잘못이 아니므로 예산을 환불**하고 "새 버전을 준비 중" 안내만 표시 (§4 참조).

---

## 3. 개발 규칙 — 절대 금지

1. **부팅 업데이트 체크 스레드를 끄지 말 것.** v2026-06-1201에서 주석 처리된 채 6주간 배포돼
   전 사용자가 업데이트 팝업을 못 봤다. 재발 시 사용자는 영원히 구버전에 갇힌다
2. **`setup_env.bat` / `*api key.bat` / `keys.env` / `*-key.json` / `service_account*.json` 커밋 금지.**
   `.gitignore`에 있지만 `git add .` 금지 — **변경 파일을 이름으로만 add**
3. **PyInstaller 캐시 재사용 금지.** 매 빌드 전 `build/` `dist/` 폴더를 지울 것 (`--clean` 만으로 부족)
4. **`os.execv()` / 데몬 스레드에서 `sys.exit(0)` 로 재시작 금지.** 후자는 스레드만 죽고 프로세스가
   살아남아 파일 핸들을 잡아 스왑이 실패한다. 업데이트 경로는 무조건 `os._exit(0)`
5. **swap.bat 부활 금지.** cp949·`_MEIPASS2` 상속·창 노출 등 버그의 온상이었다. `--updater` 서브커맨드로 통일
6. **Win32 MessageBox를 업데이트 플로우에 쓰지 말 것.** frozen windowed EXE에서 안 뜨는 경우가 있다.
   업데이트 UI는 **프론트 DOM만**
7. **업데이트 체크에 raw.githubusercontent 단독 사용 금지.** CDN이 수 분간 스테일. Releases API가 1순위
8. **`os.replace` 락 probe를 rename으로 하지 말 것.** Windows는 running EXE의 rename을 허용해
   거짓 성공한다. 반드시 `open(path, "ab")` write-probe
9. **병렬 워커 공유 상태는 반드시 락.** `file_counter_lock`, `ref_lock`, `gallery_lock`,
   `pending_jobs_lock`, `log_lock`, `progress_lock`
10. **`send_file(user_path)` 금지.** `_is_path_allowed(fp)` allowlist 통과 후에만
11. **실행 문자열(print/로그)에 non-ASCII 금지.** 한국어 Windows 콘솔이 cp949라 크래시한다. 주석은 OK
12. **`Image.convert("RGB")` 를 ref/생성 이미지에 직접 쓰지 말 것.** PNG 투명 픽셀이 검게 된다.
    `_to_display_image()`(알파 보존) 또는 `_to_rgb_flatten()`(JPEG/BMP 전용)
13. **자식 프로세스에 `_MEIPASS*` 환경변수 상속 주의.** onefile EXE 자식이 옛 _MEI를 재사용하려다
    "Failed to load Python DLL"로 죽는다. `env=`에서 `_MEI*` 제거
14. **`.meta.json` 사이드카 부활 금지.** 읽는 코드가 없었고 고아만 쌓였다. `_maybe_autosave()`가 대체
15. **이론만 믿고 배포 종료 금지.** 업데이트 관련 변경은 **실제 구버전 EXE로 E2E 검증**이 의무.
    v1733~v1738은 이 검증 없이 릴리스해 매번 실패했다
16. **"생성 중" 판단에 `state.is_generating` 단독 사용 금지.** 그건 *보이는 탭* 하나뿐이다.
    프로세스를 죽이거나 작업을 잃는 경로(자동 업데이트·종료)는 반드시 `any_project_generating()`.
    v2026-08-0501까지 백그라운드 탭 배치가 자동 업데이트에 통째로 날아갔다
17. **테스트 스크립트로 app.py를 import 할 땐 `NANOBANANA_DATA_DIR` + 임시 프로젝트 폴더 필수.**
    안 하면 `_save_session()`이 사용자의 실제 `~/.nanobanana/session.json`을 덮어써
    다음 실행 때 열려 있던 탭이 전부 바뀐다 (2026-08-07 실제로 발생)
18. **UI 목록을 `innerHTML = ""`로 통째로 다시 그리지 말 것.** 갤러리·탭 스트립은 이미지가
    완성될 때마다 갱신되므로 전체 재생성은 화면 깜빡임 + 썸네일 재요청 + 드래그 취소를 부른다.
    키(파일경로 / pid) 기반으로 DOM을 재사용할 것
19. **app.py에서 `import launcher` 금지.** launcher는 frozen 진입 모듈이라 재import 시 모듈 레벨
    부작용이 재실행된다. 공유가 필요하면 `updater.py`에 둘 것

---

## 4. 배포 — 업데이트를 절대 놓치지 않는 업로드 방법

> 전체 절차·스크립트는 `.claude/skills/나노바나나api배포/SKILL.md`. 여기엔 **원리와 필수 규칙**만.

### 4.1 순서
```
VERSION bump(오늘 날짜) → 문법 체크 → src 동기화 → build/dist 삭제 → 클린 빌드 → sha256
→ 시크릿 스캔 → 이름 지정 add → commit -F 파일 → push → tag → push
→ ★ draft 릴리스 생성 → 자산 2종 업로드 → draft:false 로 공개
→ E2E 검증 → 로컬 재기동 → 인수인계 기록
```

### 4.2 ★ 왜 draft로 올려야 하는가 (2026-07-30 실제 사고)
GitHub은 릴리스를 만드는 **즉시** `/releases/latest`에 새 태그를 노출하는데,
36MB EXE 업로드에는 **4~5초**가 걸린다(전 릴리스 실측). 그 사이에 업데이트를 시도한 앱은
아직 없는 파일을 받으려다 **HTTP 404**를 맞는다. 자동 업데이트가 켜진 지금은 그 창에 걸린
사용자에게 "업데이트 실패"로 보이고, 자동설치 시도 예산까지 잘못 소모된다.

**draft로 만들면 자산이 다 붙기 전엔 누구에게도 안 보이므로 무방비 창이 0초가 된다.**
```
1) POST /releases  {"draft": true, ...}      → latest 는 아직 이전 버전
2) 자산 업로드: NanoBanana.exe + NanoBanana.exe.sha256
3) PATCH /releases/<id>  {"draft": false}    → 이 순간부터 노출 (자산은 이미 준비됨)
```
클라이언트 쪽 안전망도 있다: 자산 404는 `UpdateNotReady`로 구분해 조용히 넘어가고
시도 예산을 환불한다. 하지만 **절차로 막는 게 먼저**다.

### 4.3 자산 2종은 필수
- `NanoBanana.exe`
- `NanoBanana.exe.sha256` (내용: `<hex>  NanoBanana.exe`)
  api.github.com이 403(무인증 IP당 60회/h — 공유망에서 잘 터짐)일 때 업데이터가 조립 URL로
  폴백하는데, 그때 해시를 이 사이드카에서 읽는다. **빼먹으면 무결성 검증이 사라진다**
- 릴리스 body에도 `sha256: <hex>` 한 줄 포함 (프론트가 자동 필터링해 사용자에겐 안 보임)

### 4.4 한국어 릴리스 노트
- **반드시 Python urllib으로 JSON 바이트 POST.** bash/PS 인라인 curl은 cp949로 깨진다
- 톤: 평문 한국어 2~5줄, "뭐가 좋아졌는지"만. 마크다운·백틱·개발 용어 금지
  (`sha256:` 줄만 예외 — 프론트가 필터)
- 기술 상세는 **커밋 메시지**에 (개발자용)

### 4.5 E2E 검증 (업데이트 경로 변경 시 의무)
- **사전조건**: `Get-Process NanoBanana` 0건 + 포트 5656 미점유.
  안 지키면 새로 띄운 테스트 바이너리가 포트 폴백으로 조용히 종료되고
  **남의 앱을 측정**해 가짜 결과가 나온다 (2026-07-30 실제로 당함)
- **판정은 버전 문자열이 아니라 측정 대상 파일의 sha256 우선**
- 핸즈프리 확인: 구버전 EXE를 켜고 **아무 조작 없이** 새 버전이 되는지

---

## 5. Git 관리

- 저장소: https://github.com/productionkhu-tech/freewill-nanobanana — 브랜치 `main` 단일
- **`git add .` 금지.** 변경 파일을 이름으로 나열
- 커밋 전 시크릿 스캔:
  ```powershell
  git diff --cached | Select-String -Pattern "AIzaSy|sk-|ark-|papi\.|BEGIN PRIVATE KEY"
  ```
  (이 패턴 문자열 자체가 문서에 있으면 오탐 — 실제 **키 값**이 있는지로 판단)
- **커밋 메시지는 `git commit -F <파일>`.** PS 5.1에서 따옴표/한글 인라인은 깨진다.
  메시지 파일은 UTF-8(no BOM)로 작성
- 태그: `git tag -a vYYYY-MM-DDNN -m vYYYY-MM-DDNN` → `git push origin <tag>`
- **이미 공개된 태그/릴리스는 지우지 말 것.** 그 버전을 받은 사용자의 업데이터가 자산을 못 찾는다
- 버전 비교는 숫자 튜플(`_version_tuple`)이라 `(2026,7,3001) > (2026,7,2303)`.
  날짜를 잘못 찍어도 다음에 바로잡으면 정상 인식된다 (문자열 비교 금지)

---

## 6. 관리 (키 · 맥 · 문서)

### 6.1 프로바이더/키
| 프로바이더 | 모델 | 환경변수 |
|---|---|---|
| Google Vertex | gemini-3-pro-image 등 | `GOOGLE_APPLICATION_CREDENTIALS` + `NANOBANANA_PROJECT_ID` |
| Google AI Studio | 동일 모델군 | `NANOBANANA_STUDIO_KEY` |
| OpenAI | gpt-image-2 | `OPENAI_API_KEY` |
| BytePlus | seedream-5-0-pro / 4-5 | `ARK_API_KEY` |

- Gemini는 10 RPM → 앱이 8 RPM으로 스로틀. **결제 미연결 시 Studio 429(limit:0) + Vertex 403(BILLING_DISABLED)**
- Reve는 2026-09 API 서비스 종료로 v2026-09-0101에서 완전 제거. `_RETIRED_MODEL_FALLBACK`이
  저장물의 reve-create를 기본 모델로 폴백 (개명 맵 `_MODEL_RENAMES`와 다름 — prefs 키는 건드리지 않음).
  과거 Reve 생성물의 갤러리 배지/라벨은 그대로 표시
- Seedream 5.0 Pro 커스텀 크기 상한은 `2048²×1.1025 = 4,624,220px` (그냥 2048²로 두면 통과할 크기가 잘림)

### 6.2 맥 (소스 실행)
- `NanoBanana.command` 더블클릭 → `server_mac.py` → Flask를 기본 브라우저로
- 키는 `keys.env` (앱 폴더 → `~/.nanobanana/` 순으로 탐색). 상대경로 서비스계정 JSON 자동 해석
- **python.org Python 3.10+ 필수.** Xcode 내장 3.9는 google-genai 1.47까지만 설치돼
  `image_size`(2K/4K)가 없어 Gemini가 즉사한다 → 앱이 시작 시 안내 후 종료
- **SSL**: python.org 파이썬은 `Install Certificates.command` 를 안 돌리면 CA 번들이 비어
  표준 urllib 경로(업데이트 체크 등)가 전부 실패 → `server_mac.py`가 certifi 번들로 자동 보정
- 업데이트: 실행 시 `git pull --ff-only` (clone 설치 한정). **진입 스크립트 특성상 pull 받은 다음 실행부터 적용**

### 6.3 문서 지도
| 문서 | 역할 |
|---|---|
| **CLAUDE.md** (이 문서) | 구조·원리·규칙·배포·관리 — 상시 최신 유지 |
| `.claude/skills/나노바나나api배포/SKILL.md` | 배포 실행 절차 + 스크립트 전문 |
| `인수인계_2026-07-30_전체.md` | 현행 인수인계 (제품 현황·최근 이력·다음 할 일) |
| `맥_실행_가이드.md` | 맥 고객 배포용 안내 |
| `seedream *.md` / `API_레퍼런스_종횡비.md` | 외부 API 스펙 + 실측 주석 (`Reve API 문서.md`는 서비스 종료로 이력용) |
| `인수인계_2026-07-15/20_*.md`(구) | 과거 스냅샷 — 이력 참고용 |

---

## 7. 자주 만나는 함정

| 증상 | 원인 | 해결 |
|---|---|---|
| 빌드 후 새 코드 반영 안 됨 | WebView2 캐시 | no-cache 헤더 + `?v=` 쿼리 (적용됨) |
| "업데이트 실패: HTTP 404" | 릴리스 공개~자산 업로드 4~5초 창 | **draft 후 공개** (§4.2). 앱은 조용히 넘어가고 예산 환불 |
| "이미 최신"인데 아님 | raw CDN 스테일 | Releases API 1순위 (적용됨) |
| os.replace AccessDenied | rename probe의 거짓 성공 | write-probe + 20회 retry |
| 업데이트 후 "Failed to load Python DLL" | `_MEIPASS2` 상속 | Popen `env=`에서 `_MEI*` 제거 |
| E2E가 이상한 결과 | 다른 NanoBanana가 포트 점유 | 사전조건 확인 + 해시로 판정 (§4.5) |
| 두 번째 실행 시 창 2개 | mutex/포트 체크 레이스(1초 내 동시 기동) | 재현되면 mutex 획득~포트 바인딩 원자화 필요 |
| cp949 UnicodeEncodeError 크래시 | 실행 문자열에 non-ASCII | 규칙 11 |
| 프리뷰 비율이 늘어남 | 푸터 성장으로 캔버스 백킹스토어 불일치 | `#stage` ResizeObserver로 재fit (적용됨) |
| PNG 투명 배경이 검정 | `convert("RGB")` | `_to_display_image` / `_to_rgb_flatten` |
| Stop 눌러도 UI 멈춤 | `with ThreadPoolExecutor`의 wait=True | `shutdown(wait=False, cancel_futures=True)` |
| 병렬 생성 시 파일 덮어쓰기 | file_counter race | `file_counter_lock` + `reserve_filepath()` (탭이 같은 출력 폴더를 써도 안전) |
| 같은 모델인데 장마다 시간이 다름 | 프로바이더별 공유 리미터(제미나이 8 RPM) 대기 시간이 카드의 초에 포함됨 | 정상. 로그의 `rate-limit wait Ns` 로 확인 |
| `finish_reason: NO_IMAGE` 반복 | 모델(특히 Lite)이 이미지를 안 내놓음 | 재시도 때 시드 재발급 + 다른 프로바이더 폴백. 잦으면 Flash/Pro로 |
| 다른 탭 갔다 오니 스켈레톤이 사라짐 | 스켈레톤이 프로젝트 소유가 아니었음 | `/api/gallery`의 `outstanding`에서 개수를 유도 (적용됨) |
| `/api/*` 403 | CSRF 토큰 없음 | `api()` 헬퍼 사용 |
| Program Files 설치 사용자 | UAC로 자기교체 불가 | 일반 폴더로 이동 안내 |

---

## 8. 사용자 배포 (재판매)

**Windows**: `NanoBanana.exe` + 키 설치 bat 2개 파일. 같은 폴더에 두고 bat 1회 실행 후 EXE 더블클릭.
Program Files 아래는 피할 것. 이후 업데이트는 **껐다 켜기만 하면 자동**.

**맥**: 저장소 clone + `pip3 install -r requirements_mac.txt` + `keys.env` 배치 →
`NanoBanana.command` 더블클릭. 상세는 `맥_실행_가이드.md`.

---

## 9. 업데이트가 "절대" 실패하지 않는가 — 솔직한 답

자동 복구 경로가 다층으로 있고(재시도·폴백·에스컬레이션·시도 예산), **원본 EXE는 `os.replace`가
원자적이라 실패해도 손상되지 않는다.** 하지만 우리 코드 밖의 변수는 통제 불가:

| 상황 | 결과 |
|---|---|
| 네트워크 blip / GitHub 403 | 재시도 + 조립 URL·사이드카 폴백 |
| 자산 업로드 중 접속 | `UpdateNotReady` → 조용히 넘어가고 다음 실행에 설치 |
| AV가 새 EXE 격리 | 시도 2회 후 수동 다이얼로그로 폴백 |
| OneDrive/Dropbox 동기화 락 | os.replace 재시도, 실패 시 수동 안내 |
| Program Files 설치 | UAC로 불가 — 폴더 이동 안내 |

탈출구는 항상 열려 있다: **푸터 버전 클릭(수동 체크)** 또는
[릴리스 페이지](https://github.com/productionkhu-tech/freewill-nanobanana/releases/latest)에서
EXE 수동 다운로드 후 덮어쓰기. 실패 진단은 `%TEMP%\nanobanana_update.log`.
