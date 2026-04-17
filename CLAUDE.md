# NanoBanana — 개발/배포 가이드

Google Gemini API 기반 AI 이미지 생성 데스크톱 앱 (Flask + pywebview + PyInstaller).
이 문서는 코드베이스를 다루는 사람(그리고 AI 에이전트)이 **프로젝트 규칙, 구조, 원리, 배포 흐름**을 한 번에 이해하도록 쓰여있습니다.

---

## 0. 빠른 요약 (TL;DR)

- **실행 방식**: onefile PyInstaller EXE (`NanoBanana.exe`) + `setup_env.bat` (환경변수 심기)
- **프론트**: HTML/CSS/JS (Flask가 서빙, pywebview WebView2 창에서 렌더)
- **백엔드**: Flask 127.0.0.1:5656, `app.py` 단일 파일에 상태+라우트
- **배포**: GitHub Release에 EXE asset 업로드 → 기존 사용자는 실행 시 자동 업데이트
- **버전**: `vYYYY-MM-DDNN` — 같은 날 `NN`은 증가, 날짜 바뀌면 `01`부터 재시작
- **자동 업데이트**: `main` 브랜치 `VERSION` vs 로컬 tuple 비교 → 전체 EXE 교체 + `os._exit` + swap.bat

---

## 1. 프로젝트 구조

```
나노바나나 api/
├── app.py                  # Flask 서버 + AppState(비즈니스 로직 전부), ~2400줄
├── launcher.py             # pywebview 창 + 앱 수명주기 + 자동업데이트 진입점
├── updater.py              # GitHub Release에서 새 EXE 받아와서 스왑 (chcp 65001 bat)
├── VERSION                 # v2026-04-1730 같은 현재 버전 (커밋 대상)
├── setup_env.bat           # ⚠️ GIT에 NEVER — API 키 하드코딩된 사용자용 설치 스크립트
├── setup_env.bat.example   # Git에 공개되는 템플릿 (placeholder 값)
├── app.ico                 # Windows 아이콘 (spec에 icon 명시)
├── images.png              # 아이콘 원본 PNG
├── NanoBanana.spec         # PyInstaller 설정 (onefile, hidden imports)
├── requirements.txt        # 개발용 의존성
├── .gitignore              # setup_env.bat + dist/ + build/ 등 제외
├── static/
│   ├── app.js              # 프론트엔드 로직 전부 (~1800줄)
│   └── style.css           # 다크 테마 + 반응형
├── templates/
│   ├── index.html          # 메인 UI (CSRF 메타 태그 포함)
│   ├── viewer.html         # 더블클릭 시 뜨는 별도 창
│   └── prompt_popup.html   # Prompt 버튼 클릭 시 뜨는 별도 창
└── dist/NanoBanana/        # 빌드 결과물 (NanoBanana.exe 33MB + setup_env.bat)
```

### 빌드 보조 경로
```
C:\NanoBanana_build\
├── src\                    # 소스 파일이 복사되는 곳 (PyInstaller가 읽음)
└── venv_build\             # PyInstaller 빌드 전용 가상환경 (Python 3.12)
```

**이유:** 원본 소스 경로(`기획 파일/TA/나노바나나 api`)는 한글/공백 포함 → PyInstaller가 가끔 경로 인식 못 함. ASCII 경로에서 빌드 후 dist/를 원본으로 복사하는 파이프라인.

### 사용자 데이터 경로
```
~/.nanobanana/
├── prefs.json                   # skip_delete_confirm, prompt_history
└── last_seen_version.txt        # "업데이트 완료" 팝업 표시 여부 판단

~/Documents/NanoBanana JSON/     # 프로젝트 세션 JSON (Save/Load 대상)

~/Pictures/Screenshots/NanoBanana Clipboard/  # 클립보드/업로드된 ref 이미지 캐시

~/Desktop/NanoBanana_Output/     # 기본 생성 이미지 출력 폴더 (사용자가 변경 가능)

%TEMP%/_MEI<random>/             # PyInstaller 런타임 압축해제 폴더 (런처가 고아 청소)
%TEMP%/nanobanana_update_*.bat   # swap 스크립트 (실행 후 자기 자신 삭제)
%TEMP%/nanobanana_update.log     # swap 단계별 로그 (실패 진단용)
```

---

## 2. 동작 원리

### 런타임 흐름
```
사용자가 NanoBanana.exe 더블클릭
  ↓
launcher.py 모듈 로드 시점
  ├── sys.stdout/stderr reconfigure → UTF-8 (cp949 크래시 방지)
  ├── builtins.print 안전 래퍼 장착 (UnicodeEncodeError 조용히 swallow)
  ├── NanoBanana.new.exe / NanoBanana.exe.old 고아 파일 청소
  └── %TEMP%\_MEI* 고아 폴더 청소 (자신 것 제외)
  ↓
main()
  ├── 단일 인스턴스 mutex (ctypes get_last_error 기반) → 이미 실행 중이면
  │    EnumWindows로 기존 창 찾아 SetForegroundWindow 후 sys.exit(0)
  ├── API 환경변수 체크 → 없으면 MessageBox 후 종료
  ├── Program Files 설치 감지 → 경고 메시지
  ├── WebView2 런타임 감지 → 없으면 설치 안내
  ├── 포트 충돌 체크 → 사용 중이면 기존 창 포커스 + sys.exit(0) (mutex 놓친 경우 fallback)
  ├── 백그라운드 자동업데이트 체크 스레드 기동 (2초 지연)
  ├── Flask 서버 스레드 기동 (127.0.0.1:5656)
  ├── Flask 준비될 때까지 대기 (최대 15초)
  └── pywebview.create_window + webview.start()
       ↓
       WebView2가 http://127.0.0.1:5656 로드
       ↓
       index.html + app.js 가 서버와 JSON API로 통신
```

### Flask ↔ 프론트 통신
- 서버는 `state = AppState()` 싱글톤이 모든 상태 보유 (갤러리 아이템, ref 이미지, 설정, 프롬프트 히스토리, 큐)
- 프론트는 JSON API로 폴링 + 명령 전송
- 폴링: `/api/status` (500ms), `/api/events` (800ms), `/api/logs` (2s) — 모두 `pagehide/beforeunload`에서 `clearInterval`
- CSRF: `X-NB-Token` 헤더 필수 (GET 제외). 토큰은 HTML 템플릿의 `<meta name="nb-csrf">`에서 주입
- 모든 `/api/*` 응답은 `no-store, no-cache` 헤더 (WebView2 캐시 회피)

### 이미지 생성 파이프라인
```
사용자가 Generate 클릭 (또는 Enter)
  → POST /api/generate
  → state.pending_jobs 에 N개 job 추가 (pending_jobs_lock)
  → queue_count += N (락 안에서 원자 증가)
  → is_generating = True
  → 백그라운드 스레드 gen_worker 시작 (이미 돌고 있으면 skip)

gen_worker 루프:
  ├── pending_jobs에서 최대 max_parallel_requests(100) 만큼 pop
  ├── ThreadPoolExecutor(max_workers=100) 로 동시 실행
  ├── generate_one_image 각 워커:
  │    ├── RateLimiter.acquire() (per-provider, 7.5s interval = 8 RPM)
  │    ├── genai.Client.generate_content(model, contents, config)
  │    ├── 실패 시 재시도 최대 5회 (exponential backoff, 10s→20s→40s→80s→120s)
  │    ├── "이미지 없음" 응답이면 다른 provider로 fallback
  │    ├── 응답에서 이미지 추출 → _to_display_image (알파 보존) → PNG로 저장
  │    ├── gallery_items 에 추가 (gallery_lock)
  │    └── progress_events 에 이벤트 push
  ├── 루프마다 _maybe_autosave() → 15초에 한 번씩 프로젝트 JSON 자동 저장
  ├── Stop 버튼(cancel_flag) 감지 시 루프 종료
  ├── 종료 시 executor.shutdown(wait=False, cancel_futures=True) → 즉시 반응
  └── 배치 끝나면 "done" 이벤트
```

### 자동 업데이트 흐름 (현재 모델)
```
런처 백그라운드:
  ├── 2초 sleep (UI 안정화 대기)
  ├── raw.githubusercontent.com/.../main/VERSION fetch (15s timeout, 3회 재시도, no-cache 헤더)
  ├── tuple 변환 후 비교 (v2026-04-1730 → (2026,4,17,30))
  ├── 결과를 state.log()로 앱 Log 창에 기록 ("Update available: ..." / "up to date" / "unavailable")
  └── has_update=True면 MessageBox "업데이트하시겠습니까?" 표시

사용자가 "예" 클릭 시:
  ├── pywebview 창에 "업데이트 설치 중…" 전체 오버레이 띄움 (사용자 피드백)
  ├── apply_update_and_relaunch(remote)
  │    ├── Program Files 설치면 거부 (UAC 필요)
  │    ├── Release API로 NanoBanana.exe asset URL + expected sha256 수집
  │    ├── NanoBanana.new.exe 로 다운로드 (Content-Length 검증, 3회 재시도)
  │    ├── sha256 미스매치 시 파일 삭제하고 중단
  │    ├── %TEMP%\nanobanana_update_*.bat 작성
  │    │    - 헤더: chcp 65001 (한글 경로 지원)
  │    │    - 단계별 log 기록
  │    │    - set "_MEIPASS="; set "_MEIPASS2="  (새 EXE가 옛 _MEI 폴더 참조 방지)
  │    │    - 6회 polite rename 시도 → 실패 시 taskkill /PID → 마지막 수단 taskkill /IM
  │    │    - move /y NEW OLD → del /F /Q BACKUP (재시도 1회)
  │    │    - ping 2초 대기 (AV 스캔 시간)
  │    │    - start "NanoBanana" "%OLD%"
  │    │    - tasklist로 런칭 확인 → 안 떠 있으면 start 한 번 더
  │    └── subprocess.Popen(bat, env=_MEI* 제거한 복사본, DETACHED)
  └── os._exit(0) — 데몬 스레드에서 sys.exit(0)은 프로세스 안 죽이니 반드시 os._exit

새 EXE 기동:
  ├── stdout/stderr UTF-8 재설정 + print 래퍼
  ├── 고아 청소 (new.exe, exe.old, 오래된 _MEI*)
  ├── 정상 기동
  └── JS가 /api/release-notes-check 호출 → last_seen_version.txt vs 현재 VERSION 비교
       → 다르면 "업데이트 완료" 팝업 (sha256/hash 라인은 필터링)
```

---

## 3. 개발 규칙

### 절대 금지
1. **`setup_env.bat`을 Git에 커밋하지 말 것.**
   Vertex 서비스계정 private key가 하드코딩돼 있음. `.gitignore`에 이미 등록돼 있지만 `git add .` 시 주의.
   - 공개용 템플릿은 `setup_env.bat.example` (placeholder 값만)
2. **PyInstaller 캐시 재사용 금지.** 매 빌드 전 `build/` `dist/` 삭제.
3. **`os.execv()` / `sys.exit(0)` (데몬 스레드에서) 재시작 금지.** 전자는 `_MEI` 재사용 문제, 후자는 **스레드만 죽이고 프로세스는 계속 실행**되어 파일 핸들 잡고 swap 실패. 업데이트 경로는 무조건 `os._exit(0)`.
4. **Overlay/user_updates 폴더 접근 금지.** 구버전 잔재. 현재 업데이트는 EXE 통째 교체 방식.
5. **병렬 워커 공유 상태 락 없이 접근 금지.** `file_counter_lock`, `ref_lock`, `gallery_lock`, `pending_jobs_lock`, `log_lock`, `progress_lock` 반드시 사용.
6. **`send_file(user_path)` 금지.** 반드시 `_is_path_allowed(fp)`로 allowlist 체크 후 전달.
7. **`print()` / 로그 문자열에 em-dash(`—`), curly quote, emoji 등 non-ASCII 금지.** 한국어 Windows 콘솔이 cp949라 `UnicodeEncodeError`로 앱 크래시. 주석은 OK, 실행 문자열은 ASCII. (안전망으로 `print` 래퍼 + stdout reconfigure 있지만 이중 방어용)
8. **`Image.convert("RGB")` 를 ref/생성 이미지 경로에 직접 사용 금지.** PNG 투명 픽셀이 검정으로 변함. `_to_display_image()`(알파 보존) 또는 `_to_rgb_flatten()`(JPEG/BMP 인코더 전용) 사용.
9. **swap.bat에 `chcp 65001` 제거 금지.** 한글 경로가 cp949에서 깨져 swap 전부 실패.
10. **subprocess.Popen으로 자식 프로세스 띄울 때 `_MEIPASS*` 환경변수 상속 주의.** 자식이 onefile EXE면 옛 _MEI 폴더 재사용하려다 LoadLibrary 실패. `env=` 파라미터로 `_MEI*` 제거한 복사본 전달.
11. **`.meta.json` 사이드카 다시 만들지 말 것.** 과거에 크래시 복구용으로 만들었지만 읽는 코드가 한 번도 없었음 + 삭제 로직 없어서 고아 파일 누적. `_maybe_autosave()`로 대체됨.

### 버전 규칙
- 포맷: `vYYYY-MM-DDNN`
- `NN`은 **해당 날짜 기준** 2자리 release 번호
- **날짜가 바뀌면 `01`부터 재시작** (예: `v2026-04-1730` → 다음날 `v2026-04-1801`)
- 같은 날 여러 번 릴리스 시 `01, 02, 03...` 증가
- 비교는 **숫자 튜플** 변환 후 비교 (`_version_tuple()` — 문자열 비교 금지)

### 코드 수정 시 매 번 해야 할 일
유저가 코드 수정을 **승인**하면 다음 사이클을 **전부** 돌 것:

1. `VERSION` 파일 bump
2. `C:\NanoBanana_build\src\`로 소스 동기화
3. 기존 NanoBanana.exe 프로세스 종료 (`taskkill /F /IM NanoBanana.exe`)
4. `build/` `dist/` 삭제
5. `PyInstaller --clean --noconfirm NanoBanana.spec`
6. dist/NanoBanana.exe를 원본 프로젝트 `dist/NanoBanana/`로 복사
7. `setup_env.bat`도 복사
8. `git add VERSION + 변경 파일` + 커밋 (Co-Authored-By 포함)
9. `git push origin main`
10. `git tag -a vXXX` + `git push origin vXXX`
11. GitHub Release 생성 + EXE asset 업로드 (릴리스 body에 `sha256: <hex>` 포함 → 업데이터가 검증)

### 릴리스 노트 톤
앱 내 "업데이트 완료" 팝업에 사용자가 봄. **개발자 용어 금지**:
- ❌ `**bold**`, `##`, backtick, `_MEIPASS`, `overlay`, `subprocess`, `onefile`, `sha256`, `mutex`
- ✅ 평문 한국어, 3-6줄 bullet, "뭐가 좋아졌는지" 중심
- 예시: `- @ 눌러도 언급 메뉴가 바로 뜹니다`
- 반례: `- Fixed IME composition state tracking in keydown handler`
- 기술 상세는 **git commit 메시지**에 쓸 것 (future-me/개발자용)
- `sha256:` / `size:` / `commit:` / `build:` / `hash:` prefix + 순수 64자 hex는 프론트엔드 렌더러가 자동 필터링

---

## 4. 빌드 & 배포 명령어

### 로컬 빌드 (개발자 테스트용)
```bash
# 1. 소스 동기화
cp -f app.py launcher.py updater.py VERSION app.ico images.png NanoBanana.spec /c/NanoBanana_build/src/
cp -f static/app.js static/style.css /c/NanoBanana_build/src/static/
cp -f templates/*.html /c/NanoBanana_build/src/templates/

# 2. 빌드
taskkill //F //IM NanoBanana.exe 2>/dev/null
rm -rf /c/NanoBanana_build/src/build /c/NanoBanana_build/src/dist
cd /c/NanoBanana_build/src
/c/NanoBanana_build/venv_build/Scripts/python.exe -m PyInstaller --clean --noconfirm NanoBanana.spec

# 3. dist/ 로 배포
rm -rf dist/NanoBanana
mkdir -p dist/NanoBanana
cp /c/NanoBanana_build/src/dist/NanoBanana.exe dist/NanoBanana/
cp setup_env.bat dist/NanoBanana/
sha256sum dist/NanoBanana/NanoBanana.exe   # 릴리스 body에 넣을 값
```

### 정식 릴리스 (UTF-8 안전 Python 경유)
```bash
# 1. VERSION bump
# 2-7: 위 로컬 빌드 절차
# 8. 커밋 + 푸시
git add VERSION + 변경 파일들
git commit -m "vXXXX-XX-XXNN — 변경 요약"
git push origin main

# 9. 태그
git tag -a vXXXX-XX-XXNN -m vXXXX-XX-XXNN
git push origin vXXXX-XX-XXNN

# 10~11. 릴리스 생성 + EXE 업로드 (UTF-8 안전)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python <<'PYEOF'
import os, json, urllib.request, subprocess
token = os.environ["GH_TOKEN"]
body = """- 사용자용 한국어 릴리스 노트 bullet 1
- bullet 2

sha256: <hex>"""
payload = json.dumps({"tag_name":"vX","name":"vX","body":body}, ensure_ascii=False).encode("utf-8")
req = urllib.request.Request(
    "https://api.github.com/repos/productionkhu-tech/freewill-nanobanana/releases",
    method="POST", data=payload,
    headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
             "Content-Type": "application/json; charset=utf-8", "User-Agent": "NB"})
rid = json.loads(urllib.request.urlopen(req, timeout=30).read())["id"]
subprocess.run(["curl","-sS","-X","POST",
    "-H", f"Authorization: Bearer {token}",
    "-H", "Content-Type: application/octet-stream",
    "--data-binary", "@dist/NanoBanana/NanoBanana.exe",
    f"https://uploads.github.com/repos/productionkhu-tech/freewill-nanobanana/releases/{rid}/assets?name=NanoBanana.exe"])
PYEOF
```

**주의:** 한국어 body를 bash `curl -d` 인라인으로 넣으면 cp949 쉘에서 인코딩 깨짐. 반드시 Python urllib으로 JSON 바이트 직접 POST.

---

## 5. 아키텍처 결정사항 (왜 이렇게 했는가)

### Flask + pywebview 를 왜?
- **CustomTkinter 원본**이 있었지만 UI 업데이트/반응형/아이콘 교체가 번거로움
- HTML/CSS는 디자인 반복이 빠름
- WebView2 (Chromium 기반)는 Windows 10+에 내장 → 별도 런타임 필요 없음
- pywebview로 "브라우저 아닌 네이티브 창"으로 포장

### onefile vs onedir
- **onefile 선택**: 사용자가 `NanoBanana.exe` 하나만 받으면 됨. `_internal/` 폴더 없음
- 대가: 첫 실행 2~3초 느림 (자가 압축해제 to `%TEMP%\_MEI<random>`), EXE 33MB
- 업데이터가 "EXE 하나만 교체"하는 방식과도 맞음
- ⚠️ onefile은 `os._exit`로 죽으면 `_MEI` 폴더가 atexit cleanup 건너뛰어 남음 → 런처 시작 시 고아 `_MEI*` 청소 로직 필수

### 환경변수 vs 하드코딩 자격증명
- 원본 데스크톱 앱은 Python 소스에 service account JSON 박아놨음
- 웹 버전은 **환경변수**로 분리: 보안 + 소스코드 공개 가능
- `setup_env.bat`이 사용자 PC에 환경변수 심어주는 역할 (배포할 때만 동봉)

### 자동 업데이트: overlay → EXE 스왑 전환
- 초기: `user_updates/` 폴더에 새 파일들 덮어씀 (overlay 방식)
- 문제: PyInstaller `_MEIxxx` 임시폴더와 overlay 경로 불일치 → 버전 체크 꼬임 → 무한 업데이트 루프
- 현재: **NanoBanana.exe 통째로 교체** + `.bat` 헬퍼가 swap 수행
- VERSION은 단 하나 (EXE bundle 내부)만 존재 → 꼬임 불가

### swap.bat이 Python이 아닌 bat인 이유 + 한글 경로 대응
- 업데이트 시점에 Python 프로세스가 죽어야 EXE 핸들이 풀림 → 자기 자신을 교체 못 함
- 분리된 스크립트(.bat)가 부모 죽은 뒤 rename/move/launch 수행
- 한국어 Windows의 cmd는 기본 cp949 codepage → UTF-8로 저장한 한글 경로 bat이 깨짐
- **`chcp 65001` 를 bat 최상단**에 넣어 UTF-8로 전환 후 경로 변수 파싱

### _MEIPASS2 환경변수 차단
- PyInstaller 부트로더가 자기 추출 경로를 `_MEIPASS2`에 기록 (multiprocessing 자식용)
- `subprocess.Popen(swap.bat)` 이 env 상속 → 자식 bat의 `start` 로 띄운 새 EXE도 상속 → 새 부트로더가 `_MEIPASS2` 보고 옛 _MEI 폴더 재사용하려다 "Failed to load Python DLL" 크래시
- 수정: Popen `env=` 에 `_MEI*` 제거한 복사본 + bat 내부에서 `set "_MEIPASS2="` 다시 한 번

### PNG 투명도 보존
- `Image.convert("RGB")` 는 alpha=0 픽셀을 `(0,0,0)` 검정으로 매핑 → PNG 로고/아이콘이 검은 블럭이 됨
- `_to_display_image()`: RGBA/LA/P 모드 보존 (ref 이미지, 생성 이미지, PNG 썸네일)
- `_to_rgb_flatten()`: 흰 배경에 합성 후 RGB (JPEG 갤러리 썸네일, BMP 클립보드 복사 전용)
- ref 썸네일은 PNG로 서빙 → 다크 배경(`#151518`)이 투명 픽셀 뚫고 보임

### cp949 stdout 크래시 2중 방어
1. 모듈 로드 시점에 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` 시도. 실패 시 `io.TextIOWrapper(s.buffer, utf-8)`로 재감싸기. 그것도 실패하면 `os.devnull` 로.
2. `builtins.print` 를 try/except 래퍼로 교체 → `UnicodeEncodeError` 잡으면 ASCII-replace 후 재시도, 그래도 실패하면 조용히 drop.

이 두 레이어가 다 작동하더라도 **실행 문자열에 em-dash 넣지 않는 게 정석**.

### Rate limiter 7.5초 간격
- Google Gemini image API는 **10 RPM** 제한
- `7.5s interval` = 8 RPM (안전 마진)
- 이전 0.5초(120 RPM)는 429 폭주 원인

### 사이드카 `.meta.json` 제거
- 원래 크래시 복구용 개별 파일 백업이었음 — 하지만 읽는 코드가 구현된 적 없음
- `_maybe_autosave()` (생성 중 15초마다 프로젝트 JSON 자동 저장) 가 같은 역할 수행
- 사이드카는 삭제 로직도 없어서 고아 파일 누적 → 완전 제거 + 기존 사이드카 startup 때 정리

### 8-column 갤러리만 정사각형 타일
- 1/2/4 칸: 개별 이미지 aspect 유지 (`.media-frame { padding-bottom: calc(100% / var(--card-ar)) }`)
- 8칸: contact sheet 용도 → 모든 타일 정사각형 + `object-fit: cover`
- 혼합 aspect 이미지가 행마다 높이 튕기는 문제 해결

### 프로젝트 저장 이름 충돌 처리
- 같은 이름 파일 이미 있고, 현재 로드된 프로젝트가 아닌 경우: 서버가 `{ok:false, conflict:true, suggested:"name_2"}` 반환 → 프론트가 모달로 [취소/덮어쓰기/"name_2"로 저장] 제시
- 같은 이름이지만 **현재 로드된 프로젝트 자체**를 다시 저장하는 경우는 확인창 없이 덮어쓰기 (Save = update 의미)

---

## 6. 주요 파일 책임

### `app.py` (Flask 서버, ~2400줄)
- `class AppState`: 싱글톤 상태. 설정, 갤러리, ref, 큐, 잠금 모두 여기
- `class RateLimiter`: 토큰 버킷. `acquire()`가 `cancel_flag` 감지해 중단
- `_to_display_image(img)`: 알파 보존하며 모드 정규화 (ref/생성 이미지용)
- `_to_rgb_flatten(img, bg)`: 알파를 bg색에 합성 (JPEG/BMP용)
- `init_api()`: Gemini client 2종 생성 (vertex + studio) + 고아 `.meta.json` 정리
- `gen_worker()` / `_gen_worker_body()`: 백그라운드 이미지 생성 루프
  - ThreadPoolExecutor 직접 관리 (`with` 블록 미사용 → shutdown(wait=False, cancel_futures=True))
  - 루프마다 `_maybe_autosave()` 호출
- `_maybe_autosave()`: 15초 throttle 로 프로젝트 JSON 기록
- `collect_project_state()`: 프로젝트 JSON 직렬화 (모든 lock 아래 스냅샷)
- `save_project()`: **atomic write** (`.tmp` → `os.replace`)
- `load_project()`: tolerant parse (count="" 등 깨진 값도 OK)
- `_is_path_allowed(fp)`: path traversal 방어 (dir prefix 우선 체크, exact match는 normcase)
- `_sanitize_project_name` + `_suggest_unique_name`: 저장 이름 충돌 대응
- 모든 `/api/*` 라우트 (약 45개)
- CSRF guard (`X-NB-Token` 필수, GET/release-notes-check/version 제외)
- `MAX_CONTENT_LENGTH=40MB` (업로드 OOM 방지)

### `launcher.py` (pywebview 래퍼, ~580줄)
- **모듈 로드 시점**: stdout UTF-8 재설정, print 래퍼, 고아 파일/폴더 청소
- `_focus_existing_nanobanana_window()`: `EnumWindows` + title prefix 매칭으로 기존 창 찾아서 foreground
- `acquire_single_instance()`: `WinDLL(use_last_error=True)` + `ctypes.get_last_error()` 로 mutex 안정 감지
- `check_api_env()`: 환경변수 없으면 MessageBox 후 exit
- `check_webview2_installed()`: 레지스트리로 WebView2 존재 검증
- Program Files 설치 감지 → 경고 (UAC 이슈)
- 포트 충돌 fallback → 기존 창 포커스 후 silent exit
- `class JsApi`: JS에서 호출되는 Python 함수 (창 제어, 뷰어 팝업 등)
- `_bg_update_check()`: 백그라운드 업데이트 체크 스레드 (2초 지연, 결과 state.log로 노출)
- 업데이트 승낙 시: pywebview에 "업데이트 설치 중…" overlay 주입 → `apply_update_and_relaunch()` → `os._exit(0)`
- Win32 아이콘 주입 (`WM_SETICON`)

### `updater.py` (자동 업데이트)
- `_version_tuple(v)`: 버전 문자열 → 숫자 튜플
- `get_current_version()`: bundle의 VERSION
- `get_remote_version()`: GitHub raw, **3회 재시도 + 15s timeout + Cache-Control: no-cache**
- `check_for_update()`: tuple 비교, fetch 실패 시 remote="" 로 구분
- `_find_release_assets()`: release body `sha256:` 파싱 + `.sha256` sidecar asset fallback
- `_download_with_retry()`: 3회 재시도 + Content-Length 검증
- `_sha256_of()`: 파일 해시 계산
- `apply_update_and_relaunch()`:
  - Program Files 거부
  - 다운로드 + sha256 검증
  - swap.bat 작성 (UTF-8, `chcp 65001` 헤더)
  - `subprocess.Popen(env=_MEI* 제거한 복사본)` DETACHED
- `cleanup_legacy_overlay()`: 구버전 `user_updates/` 폴더 정리

### `static/app.js` (프론트엔드, ~1800줄)
- 모든 UI 로직: 갤러리 렌더링, 드래그 앤 드롭, 프롬프트 멘션, 모달, 폴링
- `api()` helper: CSRF 토큰 자동 첨부 (`X-NB-Token`)
- IME 처리: `_tryShowMention`, compositionstart/end, blur/visibilitychange 리셋
- `_atomicMentionEdit`: `[Image N]` 을 backspace/delete/arrow 에서 원자 단위로 처리
- `insertMention()`: 실시간 커서 위치 재확인 + IME composition 중이면 compositionend 까지 defer
- `showMentionMenu()`: idempotent — 같은 위치면 재생성 안 함 (Arrow 네비 상태 보존)
- `scheduleGalleryRefresh()`: image_done 이벤트 rAF coalesce (100장 batch 시 100번 rebuild 방지)
- `stopPolling()`: pagehide/beforeunload 에서 clearInterval

### `static/style.css`
- 다크 테마 색상 팔레트
- `.prompt-highlight` 오버레이 (transparent textarea 위에 컬러 레이어). **`.mention` 폰트 weight textarea와 일치**해야 caret 정렬 맞음
- 1/2/4 칸: `.media-frame { padding-bottom: calc(100% / var(--card-ar)) }` — aspect-ratio 기반 높이 예약 (이미지 로드 전 카드 찌그러짐 방지)
- 8칸: 정사각형 + `object-fit: cover`
- 스켈레톤: 컬럼별 `min-height` 설정

### `NanoBanana.spec` (PyInstaller)
- `SPECPATH` 기반 base_dir (dirname 호출 금지)
- ONEFILE 빌드 (COLLECT 미사용)
- `icon=app.ico` 명시 (EXE Windows 아이콘)
- hiddenimports: `google.auth.crypt.*`, `tkinter.filedialog`, webview edgechromium, clr_loader/pythonnet 등

---

## 7. 자주 만나는 함정

| 증상 | 원인 | 해결 |
|---|---|---|
| 빌드 후 새 코드 반영 안 됨 | WebView2 캐시 | `@app.after_request no-cache` + `?v=_BUILD_ID` 쿼리스트링 (이미 적용됨) |
| 한글 입력 시 @ 멘션 메뉴 안 뜸 | IME composition 중 input 이벤트 안 발생 | `keydown`에서 Shift+2 감지 후 폴링, `keyup`에서 idempotent 메뉴 표시 |
| @ 멘션 방향키로 Image 2 선택 안 됨 (Image 1로 돌아감) | 매 keyup 마다 `showMentionMenu` 가 메뉴 rebuild → selected=0 리셋 | `showMentionMenu` idempotent 체크 추가 |
| 커서가 `[Image N]` 태그 끝에서 약간 떠보임 | overlay의 `.mention` span에 font-weight 600 → textarea(400)와 글자 폭 어긋남 | span에 font-weight 지정 금지, 색상만 |
| "업데이트 있습니다" 무한 팝업 | swap.bat 실패 (한글 경로 cp949 mangling / `os._exit` 놓침 / AV 간섭) | `chcp 65001`, `os._exit(0)`, PID taskkill, start 재시도, `_MEIPASS2` 차단 |
| 업데이트 후 "Failed to load Python DLL _MEI*\\python312.dll" | 새 EXE가 부모의 `_MEIPASS2` 상속 → 옛 _MEI 폴더 로드 시도 | subprocess Popen `env=` 에서 `_MEI*` 제거 + bat에서도 `set _MEIPASS=` |
| 업데이트 후 NanoBanana.exe.old 또는 .new.exe 남음 | bat del 실패 (AV hold) / swap 실패 | del /F /Q 재시도 + launcher 시작 시 orphan 청소 |
| "UnicodeEncodeError: cp949 codec..." 앱 크래시 | print 문자열에 em-dash/한글/이모지 + stdout이 cp949 | 실행 문자열 ASCII화, stdout reconfigure, print 래퍼 |
| 두 번째 double-click 시 새 창 + 포트 에러 | ctypes GetLastError 불안정 (mutex 감지 놓침) | `WinDLL(use_last_error=True)` + `ctypes.get_last_error()`, 포트 fallback |
| 기존 창이 제목 바뀌어서 FindWindowW 실패 | JS가 타이틀을 `NanoBanana - name *` 로 변경 | `EnumWindows` + `startswith("NanoBanana")` 매칭 |
| 다른 PC에서 EXE 크래시 | VC++ redist 없음 / WebView2 없음 | WebView2 체크 로직 (설치 안내 MessageBox) |
| 1/2칸 갤러리에서 카드가 얇은 선으로 | `.media-frame` 높이 예약 없음 → 이미지 로드 전 0 | `padding-bottom: calc(100% / var(--card-ar))` |
| 병렬 생성 시 파일 덮어쓰기 | file_counter race | `file_counter_lock` |
| `/api/...` 응답이 403 | CSRF 토큰 없음 | `api()` helper가 `X-NB-Token` 자동 첨부 |
| PNG 투명 배경이 검정 | `Image.convert("RGB")` 기본 동작 | `_to_display_image` (알파 보존) or `_to_rgb_flatten`(흰 배경 합성) |
| Stop 버튼 눌러도 UI 60초 멈춤 | `with ThreadPoolExecutor` __exit__이 `shutdown(wait=True)` | 수동 `shutdown(wait=False, cancel_futures=True)` |
| 재실행 시 진행바 100% 고정 | `done` 이벤트가 width=100%로 설정 후 reset 없음 | `generate()`에서 새 배치 시작 시 width=0% 로 리셋 |
| 프로젝트 저장 시 같은 이름 조용히 덮어쓰기 | 서버가 파일 존재 무시하고 저장 | conflict 감지 → 프론트 모달로 [덮어쓰기/다른 이름] 선택 |
| `.meta.json` 고아 파일 누적 | 과거 사이드카 생성 로직 + 삭제 로직 없었음 | 생성 중단 + 삭제 시 같이 제거 + startup 청소 |

---

## 8. GitHub 저장소

- URL: https://github.com/productionkhu-tech/freewill-nanobanana
- 브랜치: `main` (유일)
- 배포 채널: GitHub Releases
- 업데이터가 읽는 파일: `raw.githubusercontent.com/.../main/VERSION`
- 업데이터가 받는 파일: Release asset `NanoBanana.exe`
- Release body에 `sha256: <hex>` 라인을 반드시 포함 (업데이터가 무결성 검증)
- 업로드 시 한국어 body는 반드시 Python urllib으로 JSON 바이트 POST (bash curl -d 는 cp949 쉘에서 깨짐)

---

## 9. 사용자 배포 안내 (재판매용 가이드)

사용자에게 전달하는 파일 2개:
1. `NanoBanana.exe` (33MB)
2. `setup_env.bat` (API 키가 그 사용자 용으로 채워진 것)

사용 방법:
1. 두 파일을 같은 폴더에 둠 (예: `C:\Users\유저\Desktop\NanoBanana\`)
   - ⚠️ **Program Files 아래는 피할 것** — UAC 때문에 자동 업데이트 실패
2. `setup_env.bat` 더블클릭 → API 키가 환경변수로 설치됨 (1회만)
3. `NanoBanana.exe` 더블클릭 → 실행 (작업표시줄에 고정해도 업데이트 후 유지됨 — 같은 경로·같은 파일명으로 덮어쓰기)
4. 업데이트가 있으면 시작 시 팝업으로 안내, "예" 누르면 자동 다운로드 + 재시작
