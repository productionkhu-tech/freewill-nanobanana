# NanoBanana 개인판 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 같은 소스에서 회사판 `NanoBanana.exe` 와 별개로 `NanoBanana-Personal.exe` 를 만든다 — 본인 키·회사 게이트웨이/팀·프로젝트 없음·바깥 프로그램 차단·자기 공개 저장소에서 자동 업데이트.

**Architecture:** 판은 번들 안의 `EDITION` 파일 하나가 정하고(`edition.py`), 이름·포트·폴더·저장소는 전부 `edition` 상수에서 읽는다. 개인판 키는 `personal_keys.py`(DPAPI 금고·무료 확인·회사 키 조회)가 맡고, app.py 는 `edition.PERSONAL` 분기에서만 그것을 쓴다. 창 토큰은 HTML 대신 launcher 가 창 주소 조각(`#nbt=`)으로만 건넨다.

**Tech Stack:** Python 3 (Flask, pywebview 6.2.1/WebView2, PyInstaller onefile), 바닐라 JS, Cloudflare Worker(JS), Windows DPAPI.

**Spec:** `docs/superpowers/specs/2026-11-23-nanobanana-personal-design.md` (승인됨 2026-11-23). 실행자는 이 계획과 함께 반드시 읽는다. 프로젝트 규칙은 `CLAUDE.md`(특히 §3 규칙 2·11·15·16·17·24·26·27).

## Global Constraints

- 판 결정: frozen 이면 `sys._MEIPASS\EDITION` 내용이 정확히 `personal` 일 때만 개인판, **환경변수 무시**. 소스 실행만 `NANOBANANA_EDITION=personal`.
- 개인판 분기는 `edition.PERSONAL` 하나로만. 회사판 동작·화면·커넥터·업데이트는 바뀌면 실패.
- 이름표 (회사 → 개인): EXE `NanoBanana` → `NanoBanana-Personal` · 포트 5656 → **5657** · 잠금 `NanoBanana-AIImageStudio-SingleInstance-v1` → `NanoBanana-Personal-SingleInstance-v1` ·
  창 제목 `NanoBanana` → `NanoBanana Personal` · 미니창 `NanoBanana mini` → `NanoBanana Personal mini` · 데이터 `~/.nanobanana` → `~/.nanobanana-personal` ·
  프로젝트 `문서\NanoBanana JSON` → `문서\NanoBanana Personal JSON` · 캐시 `NanoBanana Clipboard` → `NanoBanana Personal Clipboard` · 출력 `NanoBanana_Output` → `NanoBanana Personal_Output` ·
  미니 자리 `nanobanana` rank 1 → `nanobanana-personal` rank 1.5 · 저장소 `productionkhu-tech/freewill-nanobanana` → `productionkhu-tech/freewill-nanobanana-personal` ·
  교체 로그 `nanobanana_update.log` → `nanobanana_personal_update.log` · AppUserModelID `NanoBanana.AIImageStudio` → `NanoBanana.Personal`.
- 개인판은 `nb_gateway` 중 `gateway_url` 과 DPAPI 도우미(`vault_put`/`vault_get`) 말고는 부르지 않는다. 환경변수 키·`~/.nanobanana`·Vertex 를 읽지 않는다.
- 키: DPAPI 로만 저장(실패하면 저장 안 함, 평문 대체 없음). 화면엔 끝 네 글자만. 키 원문은 로그·이벤트·프로젝트 파일·게이트웨이로 가지 않는다. 게이트웨이엔 `sha256(key)` 앞 **8글자(hex)** 만.
- 회사 키 비교 대상: 워커의 `knownKeyHashes(env)` 집합(현재 OPENAI/STUDIO/ARK + RETIRED + TICKET). 시댄스 키는 범위 밖(사용자 결정).
- 실행 문자열(print/로그)은 ASCII 만(규칙 11). 화면 문구는 한국어 평문.
- 시험은 격리(규칙 17): `NANOBANANA_DATA_DIR`·`USERPROFILE`·`HOME`·`NANOBANANA_MINI_DOCK_DIR` 를 임시 폴더로. 시험 러너는 빌드 venv `C:\NanoBanana_build\venv\Scripts\python.exe` (pytest 없음 — 스크립트가 `checks N, failed M` 을 찍고 실패 시 종료코드 1).
- EXE 시험 직전 5656·5657 과 NanoBanana*.exe 가 비어 있는지 확인, 끌 땐 시험 폴더 경로로만(메모리 `exe-test-never-touch-real-app`).
- git: 작업 브랜치 `personal`(main 에서 분기, 끝나면 main 으로 fast-forward). `git add .` 금지 — 이름으로만. 커밋은 `git commit -F <스크래치 파일>`, 끝 줄 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. 커밋 전 시크릿 스캔(CLAUDE.md §5).
- 바깥에 나가는 일(공개 저장소 만들기·push·릴리스·워커 배포)은 그 단계에서 사용자에게 먼저 묻는다. 돈 드는 생성은 사용자 확인 뒤.

## Review Focus

1. **붙여 넣은 키의 군더더기** — 앞뒤 공백·줄바꿈·따옴표, `Bearer ` 접두어가 붙은 키는 다듬어서 받아야 하고, 가운데 공백이 있으면 `bad_key` 로 거절해야 한다. → Task 4 `test_clean_key`.
2. **인터넷 없이 넣은 키** — 저장되고 "확인 전" 으로 보이다가, 다음에 켤 때 확인돼 "확인됨" 이 되어야 한다(회사 키면 그때 지워짐). → Task 5 `test_offline_then_recheck`.
3. **다른 윈도우 계정/PC 에서 옮겨 온 금고** — 30초 다시 시도 뒤 "다시 넣어 주세요" 띠, 새 키 저장은 되어야 하고 앱은 멀쩡해야 한다. → Task 5 `test_unreadable_vault`.
4. **생성 중 키 지우기** — 남은 장은 "키 필요" 로 깔끔히 실패하고 예외·멈춤이 없어야 한다. → Task 5 `test_delete_key_mid_batch`.
5. **회사판·개인판·시댄스 미니창이 한 구석에** — 셋이 겹치지 않고 쌓여야 한다. → Task 10 `exe_both.py` 의 미니창 단계.

---

## File Structure

| 파일 | 책임 |
|---|---|
| `edition.py` (새) | 판 결정 + 판별 이름표 상수 + 창 제목 판별. 다른 모듈 import 금지 |
| `personal_keys.py` (새) | 개인 키 금고(DPAPI)·키 다듬기·무료 확인·회사 키 조회·화면용 상태. Flask/app 모름 |
| `edition_personal/EDITION` (새) | 내용 `personal` 한 줄 — 개인판 spec 만 번들 |
| `NanoBananaPersonal.spec`, `app-personal.ico` (새) | 개인판 빌드 |
| `static/nb_token.js` (새) | 창 토큰 받기(조각 → 메모리+쿠키, 없으면 쿠키, 없으면 메타) |
| `app.py` | 이름표 연결 · 개인판 키/상태/생성 오류 안내 · 팀·프로젝트·에이전트 분기 · 토큰 검사 · `/api/personal/keys` · `/api/back-to-main` |
| `launcher.py` | 이름표 · 창 주소 조각 · 두 번째 실행 · `_MEI` 청소 |
| `updater.py` | 저장소·자산·임시 파일·상태 폴더 이름 |
| `mini_window.py` | 창 제목·자리 파일·rank |
| `templates/index.html`·`viewer.html`·`prompt_popup.html`·`mini.html` | `nb_token.js` 먼저 · 판/제목 치환 · 내 키 단추/창 |
| `static/app.js`, `static/style.css` | 개인판 화면(내 키, 띠, 잠긴 모델, 숨김) |
| `gateway/worker.js` | `POST /personal/check` |
| `tests/personal/*` (새) | 시험 (공개 저장소에 커밋해도 되는 것만 — 키 없음) |

---

### Task 1: `edition.py` — 판 결정과 이름표

**Files:** Create `edition.py`, `edition_personal/EDITION`, `tests/personal/_h.py`, `tests/personal/t_edition.py`

**Interfaces — Produces:**
- `detect(frozen: bool, meipass: str | None, env: Mapping[str, str]) -> str` — `"personal"|"company"`
- 모듈 상수: `EDITION`, `PERSONAL`, `APP_TITLE`, `MINI_TITLE`, `PORT`, `MUTEX_NAME`, `APP_USER_MODEL_ID`, `DATA_DIRNAME`, `PROJECTS_DIRNAME`, `CLIPBOARD_DIRNAME`, `OUTPUT_DIRNAME`, `DOCK_NAME`, `DOCK_RANK`, `REPO`, `EXE_STEM`, `UPDATE_LOG` (값은 Global Constraints 표 그대로)
- `is_own_main_title(title: str) -> bool` — 회사: `startswith("NanoBanana")` 이고 `startswith("NanoBanana Personal")` 아니고 `!= "NanoBanana mini"`. 개인: `startswith("NanoBanana Personal")` 이고 `!= MINI_TITLE`
- `tests/personal/_h.py`: `isolate(edition: str) -> dict`(임시 폴더 만들고 위 환경변수 4개 + `NANOBANANA_EDITION` 설정, 경로 dict 반환), `check(name, cond, extra="")`, `done() -> None`(요약 출력, 실패면 `sys.exit(1)`), `FakeHTTP(routes: dict[(method, path)] -> callable(req) -> (status, dict))` 와 `.base`, `.calls`(method, path, headers, body 기록)

- [ ] **Step 0:** `git switch -c personal`
- [ ] **Step 1: 실패하는 시험** `t_edition.py`
```python
check("frozen + EDITION=personal", detect(True, d_personal, {}) == "personal")
check("frozen, no file -> company", detect(True, d_empty, {}) == "company")
check("frozen ignores env", detect(True, d_empty, {"NANOBANANA_EDITION": "personal"}) == "company")
check("frozen, file says other", detect(True, d_other, {}) == "company")          # 내용 "company\n" / "Personal" / ""
check("source + env", detect(False, None, {"NANOBANANA_EDITION": "personal"}) == "personal")
check("source default", detect(False, None, {}) == "company")
# 이름표: 서브프로세스로 두 판을 각각 import 해 상수 dict 를 받아 Global Constraints 표와 비교
check("company names unchanged", names("company") == COMPANY_EXPECTED)
check("personal names", names("personal") == PERSONAL_EXPECTED)
for t, comp, pers in [("NanoBanana", True, False), ("NanoBanana — a *", True, False), ("NanoBanana mini", False, False),
                      ("NanoBanana Personal", False, True), ("NanoBanana Personal — a", False, True),
                      ("NanoBanana Personal mini", False, False), ("Image Viewer", False, False)]:
    check("title " + t, own_title("company", t) == comp and own_title("personal", t) == pers)
```
- [ ] **Step 2:** 실행 → 실패 확인: `& $PY tests\personal\t_edition.py` → `ModuleNotFoundError: edition`
- [ ] **Step 3:** `edition.py` 구현(파일 읽기 실패는 company), `edition_personal/EDITION` = `personal\n`
- [ ] **Step 4:** 실행 → `checks 15, failed 0`
- [ ] **Step 5:** 커밋 (`edition.py edition_personal/EDITION tests/personal/_h.py tests/personal/t_edition.py`)

### Task 2: 이름표 연결 (launcher · updater · app · mini_window)

**Files:** Modify `launcher.py` (PORT 115, 고아 파일 146, mutex 468, 창 찾기 289-345, 교체 로그 749, 창 제목 1036, AppUserModelID 1081, FindWindowW 1105), `updater.py` (REPO 34, 상태 폴더 65, 자산 이름 243-260·387-389), `app.py` (`_user_data_dir` 3817, 캐시 1018, 프로젝트 1021, 출력 1343, 2712, 4779, `__main__` 포트), `mini_window.py` (DOCK 226, 창 제목 1017), `templates/index.html`·`mini.html` (`<title>`), `static/app.js` (`updateTitleDirty` 4971), `NanoBanana.spec` (hiddenimports), Test `tests/personal/t_names_wired.py`

**Interfaces — Consumes:** Task 1 상수 전부. **Produces:** `app._render_html` 이 `__EDITION__`·`__APP_TITLE__`·`__MINI_TITLE__` 를 치환; 화면은 `<meta name="nb-edition" content="__EDITION__">` 와 `<title>__APP_TITLE__</title>` 을 읽는다. `updater.asset_names() -> tuple[str, str]` (`EXE_STEM + ".exe"`, `+ ".exe.sha256"`), `updater.temp_names(exe_dir) -> tuple[str, str]` (`.new.exe`, `.exe.old` — 둘 다 `EXE_STEM` 기준, 회사판은 지금 이름과 바이트까지 같음).

- [ ] **Step 1: 실패하는 시험** `t_names_wired.py` — 판마다 서브프로세스에서 `isolate()` 후 import:
```python
check("launcher port", launcher.PORT == E.PORT)            # 5656 / 5657
check("updater repo", updater.REPO == E.REPO)
check("updater assets", updater.asset_names() == (E.EXE_STEM + ".exe", E.EXE_STEM + ".exe.sha256"))
check("updater state dir", os.path.basename(os.path.dirname(updater.auto_update_state_file())) == E.DATA_DIRNAME)  # USERPROFILE 아래
check("app data dir default", os.path.basename(app_dd_without_override) == E.DATA_DIRNAME)
check("dock", (mini_window.DOCK_NAME, mini_window.DOCK_RANK) == (E.DOCK_NAME, E.DOCK_RANK))
check("index title", "<title>%s</title>" % E.APP_TITLE in client.get("/").get_data(as_text=True))
check("mini title", "<title>%s</title>" % E.MINI_TITLE in client.get("/mini").get_data(as_text=True))
check("no hardcoded leftovers", grep_literals(["\"NanoBanana.new.exe\"", "\".nanobanana\"", "5656"], files) == [])  # edition.py 제외
```
- [ ] **Step 2:** 실행 → 실패 확인
- [ ] **Step 3:** 모든 하드코딩을 `edition` 으로. `_focus_existing_nanobanana_window` 의 비교는 `edition.is_own_main_title(buf.value)` 로. `updateTitleDirty` 의 `base` 는 `<meta name="nb-edition">` 옆에 둔 제목(문서 첫 `<title>`)에서. 두 spec 의 hiddenimports 에 `'edition', 'personal_keys', 'nb_gateway'` 추가
- [ ] **Step 4:** 실행 → 통과. 회사판 회귀: `node --check static\app.js`, 스크래치 `t_models.py` A 부분 그대로 통과
- [ ] **Step 5:** 커밋

### Task 3: `_MEI` 청소가 쓰는 중인 폴더를 건드리지 않게

**Files:** Modify `launcher.py:_sweep_stale_mei` (164-207), Test `tests/personal/t_mei_sweep.py`

**Interfaces — Produces:** `_sweep_stale_mei(temp_dir: str | None = None, our_mei: str | None = None, now: float | None = None, force: bool = False) -> list[str]` (지운 경로). 인자 없이 부르면 지금처럼(frozen·win32 에서만, 한 번만). `force=True` 는 시험용으로 frozen 검사와 한 번만 검사를 건너뛴다.

알고리즘(정해 둔다): 후보(`^_MEI[0-9a-fA-F]+$`, 자기 것 아님, 5분 지남)마다 `os.rename(path, path + ".nbdel")` — **실패하면 쓰는 중이라 건너뜀**(안에 열린 파일·올라간 DLL 이 있으면 Windows 가 폴더 이름 바꾸기를 거절한다). 성공하면 바뀐 이름을 `rmtree`.

- [ ] **Step 1: 실패하는 시험**
```python
a = mk("_MEI1A2B", age=600, hold_open="templates/index.html")   # 파일을 열어 둔 채 (다른 앱이 쓰는 중 흉내)
b = mk("_MEI3C4D", age=600)                                     # 버려진 폴더
c = mk("_MEI5E6F", age=60)                                      # 막 풀린 폴더
own = mk("_MEI7788", age=600)
gone = L._sweep_stale_mei(temp_dir=T, our_mei=own, now=time.time(), force=True)
check("in-use kept whole", os.path.isfile(os.path.join(a, "templates", "index.html")) and os.path.isfile(os.path.join(a, "static", "app.js")))
check("stale removed", not os.path.exists(b) and not os.path.exists(b + ".nbdel"))
check("fresh kept", os.path.isdir(c)); check("own kept", os.path.isdir(own))
check("returned list", gone == [b])
```
(지금 코드에선 `a` 의 `static/app.js` 가 지워져 실패한다 — 그게 고치는 버그.)
- [ ] **Step 2:** 실행 → `in-use kept whole` 실패 확인
- [ ] **Step 3:** 구현
- [ ] **Step 4:** 실행 → 통과
- [ ] **Step 5:** 커밋

### Task 4: `personal_keys.py` — 금고 · 다듬기 · 무료 확인 · 회사 키 조회

**Files:** Create `personal_keys.py`, Test `tests/personal/t_personal_keys.py`

**Interfaces — Produces:**
- `PROVIDERS = ("studio", "openai", "ark")`, `VAULT_NAME = "personal_keys"`
- 기록 모양: `{"v": 1, "keys": {provider: {"key": str, "state": "ok"|"unchecked"|"rejected", "company": "clear"|"unknown", "checked_at": str, "models_off": [app_model_id, ...]}}}`
- `load(data_dir) -> tuple[str, dict | None]` — `("ok", rec)` · `("missing", None)`(파일 없음) · `("unreadable", None)`(파일은 있는데 `nb_gateway.vault_get` 이 None)
- `save(data_dir, rec) -> bool` — `nb_gateway.vault_put` (실패 False, 평문 대체 없음)
- `clean_key(raw) -> tuple[str | None, str | None]` — 앞뒤 공백·줄바꿈·감싼 따옴표·`Bearer ` 제거; 가운데 공백, 8자 미만, 400자 초과, 글자 아님(`_text_problem` 같은 기준) → `(None, "bad_key")`
- `verify(provider, key, ark_models: dict[str, str] | None = None, ark_base: str | None = None) -> dict` — `{"result": "ok"|"invalid"|"net", "reason": str, "models_off": [..]}`
  - studio: `GET {GEMINI}/v1beta/models?pageSize=200` 헤더 `x-goog-api-key` (키를 주소에 넣지 않는다). 200 ok · 400/401/403 invalid · 그 밖·예외 net
  - openai: `GET {OPENAI}/v1/models` Bearer. 200 ok · 401/403 invalid · 그 밖 net
  - ark: `ark_models`(앱 id → 보내는 id)마다 `POST {ark_base}/images/generations` `{"model": wire, "prompt": "x", "size": "1x1"}` Bearer — 400 → 그 모델 됨 · 404 → `models_off` · 401/403 → 곧바로 invalid · 그 밖 net. 하나라도 400 이면 ok
  - 주소 기본값은 진짜 주소, `NANOBANANA_TEST_GEMINI_BASE`·`NANOBANANA_TEST_OPENAI_BASE`·`NANOBANANA_TEST_ARK_BASE` 는 **frozen 이 아닐 때만** 듣는다. 시간 제한 15초
- `fingerprint_prefix(key) -> str` — `sha256(key).hexdigest()[:8]`
- `company_match(key, gateway_url) -> bool | None` — `POST {gw}/personal/check {"p": prefix}` → 200 이고 `hashes` 에 전체 sha256 있으면 True, 없으면 False, 그 밖·예외 None. 보낸 본문에 prefix 말고 아무것도 없음
- `public_status(rec) -> dict` — `{provider: {"set": bool, "tail": 끝4, "state", "company", "models_off"}}` (키 원문 없음)
- `client_keys(rec) -> dict` — `{"studio", "openai", "ark": 원문 또는 "", "vertex": None}` (`state == "rejected"` 도 그대로 넘긴다 — 사용자가 다시 넣을 때까지 오류가 분명히 보이게)

- [ ] **Step 1: 실패하는 시험** (FakeHTTP 로 세 회사·게이트웨이)
```python
def test_clean_key():                       # Review Focus 1
    check("trim", clean_key("  AIzaXYZ12345\n") == ("AIzaXYZ12345", None))
    check("quotes", clean_key('"sk-abc12345"') == ("sk-abc12345", None))
    check("bearer", clean_key("Bearer ark-abc12345") == ("ark-abc12345", None))
    check("inner space", clean_key("sk-abc 12345")[1] == "bad_key")
    check("not text", clean_key({"value": "x"})[1] == "bad_key")
check("studio ok", verify("studio", "k1")["result"] == "ok" and fake.calls[-1].headers["x-goog-api-key"] == "k1" and "k1" not in fake.calls[-1].path)
check("studio invalid 400", verify("studio", "bad")["result"] == "invalid")
check("openai invalid 401", verify("openai", "bad")["result"] == "invalid")
r = verify("ark", "k3", ark_models={"seedream-5-5-261028": "dola-seedream-5-5-261028", "seedream-4-5-251128": "seedream-4-5-251128"}, ark_base=fake.base)
check("ark per model", r["result"] == "ok" and r["models_off"] == ["seedream-5-5-261028"])   # 가짜: 5-5 는 404, 4-5 는 400
check("net", verify("openai", "k", ) ["result"] == "net")                                    # 서버 내림
check("company yes", company_match(KEY_A, fake_gw.base) is True)
check("company no", company_match(KEY_B, fake_gw.base) is False)
check("company sends prefix only", fake_gw.calls[-1].body == {"p": fingerprint_prefix(KEY_B)})
check("company down", company_match(KEY_B, "http://127.0.0.1:9") is None)
check("vault roundtrip", save(dd, REC) and load(dd) == ("ok", REC))
check("vault file has no key text", KEY_A.encode() not in open(vault_path, "rb").read())
check("missing", load(empty_dd) == ("missing", None))
check("unreadable", load(garbage_dd) == ("unreadable", None))                               # 아무 바이트나 쓴 파일
check("status no secrets", KEY_A not in json.dumps(public_status(REC)))
```
- [ ] **Step 2:** 실행 → 실패 확인
- [ ] **Step 3:** 구현 (`urllib.request`, 새 의존성 없음)
- [ ] **Step 4:** 실행 → 통과
- [ ] **Step 5:** 커밋

### Task 5: app.py — 개인판 키 연결 · 생성 오류 안내 · 회사 것 0건

**Files:** Modify `app.py` (`_init_keys` 8357, `_keys_changed_since` 8407, `_spool_usage_row` 3206 부근, `_start_usage_flusher` 8448, `_no_client_error` 8432, `_client_gate` 5440, `image_failed` 이벤트 3541, `/api/status` 4081 `gw`, 새 라우트), Test `tests/personal/t_personal_app.py`

**Interfaces — Consumes:** Task 4 전부, 기존 `_apply_keys(keys, source)`, `SEEDREAM_MODEL_IDS`, `_seedream_endpoint(model)`, `_ARK_ENDPOINT_DEFAULTS`.
**Produces:**
- `shared.personal = {"phase": "loading"|"ready"|"unreadable", "rec": dict}` (개인판에서만 의미)
- `_init_personal_keys() -> None` — `load()` 가 `unreadable` 이면 0.5초마다 최대 60번 다시; 끝내 안 되면 phase `unreadable`(빈 rec 로 계속). 그다음 `_apply_keys(personal_keys.client_keys(rec), "personal")`, `_set_gw("personal")`, 뒤 스레드 `_personal_recheck()`
- `_personal_recheck() -> None` — 키마다 `company_match` (True → 그 키 삭제·저장·`_apply_keys`·이벤트 `{"type": "personal_keys", "removed_company": provider}`; False → `company: "clear"`; None → 그대로), `state == "unchecked"` 면 `verify` 해서 갱신
- `_personal_hint(err: str) -> str` — `organization must be verified` → `"OpenAI 조직 인증이 필요해요 — platform.openai.com 설정 > Organization 에서 인증해 주세요 (" + err + ")"`; Gemini `limit: 0`(429/RESOURCE_EXHAUSTED) → `"Google 계정에 결제를 연결해야 이 이미지 모델을 쓸 수 있어요 (" + err + ")"`; 401 류(`_is_auth_error`) → `"키가 거절됐어요 — 내 키에서 다시 넣어 주세요 (" + err + ")"`. 회사판에선 `err` 그대로
- 라우트(회사판 404): `GET /api/personal/keys` → `{ok, phase, keys: public_status(rec)}` · `POST /api/personal/keys {provider, key}` → `{ok, state, reason?, models_off?}` 또는 `{ok: false, error: "bad_key"|"company_key"|"invalid_key"|"vault_failed"|"loading"|"bad_provider", reason?}` · `DELETE /api/personal/keys {provider}`
- 저장 흐름: `clean_key` → `company_match`(True → `company_key`) → `verify`(invalid → 저장 안 함) → rec 갱신(`net` 이면 `unchecked`, 조회가 None 이면 `company: "unknown"`) → `save` → `_apply_keys`
- 개인판에서: `_keys_changed_since` 는 그 회사 키를 `rejected` 로 저장하고 False · `_spool_usage_row`/`_start_usage_flusher` 즉시 return · `_no_client_error(what)` → `"%s 키가 없어요 — 왼쪽 위 '내 키' 에서 넣어 주세요" % what` · `_client_gate` 는 phase `loading` 이면 `"키를 불러오는 중이에요 — 잠시 뒤 다시 눌러 주세요"`, 시드림 모델이 `models_off` 에 있으면 `"BytePlus 콘솔에서 이 모델을 활성화해 주세요"` · `image_failed` 의 `error` 와 `_batch_note` 의 오류에 `_personal_hint` · `/api/status` 의 `gw` 는 `{"state": "personal", "phase": ...}`

- [ ] **Step 1: 실패하는 시험** `t_personal_app.py` — `isolate("personal")` + 직원 PC 흉내: `OPENAI_API_KEY`·`ARK_API_KEY`·`NANOBANANA_STUDIO_KEY`·`GOOGLE_APPLICATION_CREDENTIALS` 에 가짜 값, 임시 `USERPROFILE\.nanobanana\` 에 가짜 `gw_keys.vault`·`gateway.json`. `NANOBANANA_GATEWAY_URL` = 가짜 게이트웨이, 세 회사 = FakeHTTP. `nb_gateway` 의 공개 함수 전부를 호출 기록 래퍼로 감싼 뒤 `app` import → `init_app()`.
```python
check("company zero: only gateway_url + vault", set(gw_calls) <= {"gateway_url", "vault_get", "vault_put"})
check("company zero: no env keys used", applied[-1] == {"studio": "", "openai": "", "ark": "", "vertex": None})
check("company zero: no usage spool", not os.path.exists(os.path.join(dd, "usage_spool.jsonl")))
check("company zero: gateway paths", {c.path for c in fake_gw.calls} <= {"/personal/check"})
r = post("/api/personal/keys", {"provider": "openai", "key": " sk-good1234 "})
check("save ok", r["ok"] and r["state"] == "ok" and applied[-1]["openai"] == "sk-good1234")
check("invalid not saved", post(..., {"provider": "openai", "key": "sk-bad12345"})["error"] == "invalid_key" and get_keys()["openai"]["tail"] == "1234")
check("company refused", post(..., {"provider": "studio", "key": COMPANY_KEY})["error"] == "company_key" and not get_keys()["studio"]["set"])
def test_offline_then_recheck():            # Review Focus 2
    providers_down(); r = post(..., {"provider": "ark", "key": "ark-k1234567"}); check("offline saved unchecked", r["ok"] and r["state"] == "unchecked")
    providers_up(); restart_app(); check("recheck -> ok", get_keys()["ark"]["state"] == "ok")
    gw_marks_company("ark-k1234567"); restart_app(); check("recheck removes company key", not get_keys()["ark"]["set"])
def test_unreadable_vault():                # Review Focus 3 (재시도 간격은 시험에서 줄인다: PERSONAL_RETRY_S=0.01)
    write_garbage_vault(); restart_app(); check("phase unreadable", status()["gw"]["phase"] == "unreadable")
    check("save still works", post(..., {"provider": "openai", "key": "sk-good1234"})["ok"])
def test_delete_key_mid_batch():           # Review Focus 4 — 생성 함수는 가짜로 바꿔 끼운다(돈 0)
    start_fake_batch(model="gpt-image-2", n=5); delete("/api/personal/keys", {"provider": "openai"})
    check("rest fail with key-needed", wait_batch()["failed"] >= 1 and all("키" in e for e in batch_errors()))
    check("no exception in log", "Traceback" not in logs())
check("hint org", "조직 인증" in A._personal_hint("Error code: 403 ... organization must be verified ..."))
check("hint gemini billing", "결제" in A._personal_hint("429 RESOURCE_EXHAUSTED ... limit: 0 ..."))
check("seedream off refused", generate(model="seedream-5-5-261028")["error"].startswith("BytePlus 콘솔"))
check("no key text anywhere", "sk-good1234" not in logs() + events_dump() + project_file_text())
```
- [ ] **Step 2:** 실행 → 실패 확인
- [ ] **Step 3:** 구현 (재시도 간격은 모듈 상수 `PERSONAL_RETRY_S = 0.5`, 시험에서 줄임)
- [ ] **Step 4:** 실행 → 통과. 회사판으로 같은 앱 import 해 `/api/personal/keys` 404·`_personal_hint(x) == x` 확인
- [ ] **Step 5:** 커밋

### Task 6: app.py — 팀·프로젝트 · 에이전트 경로 · 프로젝트 파일

**Files:** Modify `app.py` (`_billing_usable` 5404, `_billing_gate` 5461, `/api/billing/*` 5279·5313·5333, `start_generate` 5419, `api_projects` 6364, `_project_summary`), Test `tests/personal/t_personal_paths.py`

**Interfaces — Produces:** 개인판에서 `_billing_usable(p) -> True`, `_billing_gate(p, write) -> None`, `/api/billing/*` → 404 `{ok: false, error: "not_in_personal"}`, `/api/generate` 에 `pid`·`batch_id`·`check_only` 중 하나라도 → 403 `{ok: false, error: "not_in_personal"}`(아무것도 만들지 않음), `/api/projects` → `agent_pid: false, features: []`, 탭 요약의 팀·프로젝트 미설정 표시 없음.
설계 조정(설계서 §5.2 의 "자리표시 id"): **자리표시 값을 두지 않고** 위 두 함수가 개인판이면 통과한다 — 파일에 개인용 id 가 써질 길 자체가 없어져, 회사판 쪽 거절 코드가 필요 없다. 설계서 §5.2·§6-5 를 이 문장으로 고친다.

- [ ] **Step 1: 실패하는 시험**
```python
check("window generate not blocked by billing", gen_window()["ok"])               # 가짜 생성
check("agent pid refused", post("/api/generate", {"pid": pid, "prompt": "x", "settings": {"model": "gpt-image-2"}})["error"] == "not_in_personal")
check("batch_id refused", post("/api/generate", {"batch_id": "abcd1234"})["error"] == "not_in_personal")
check("nothing queued", queue_len() == 0)
check("features empty", get("/api/projects")["features"] == [] and get("/api/projects")["agent_pid"] is False)
check("billing routes 404", status_of("/api/billing/catalog") == 404 and status_of_post("/api/billing/set") == 404)
# 프로젝트 파일 왕복: 회사판이 저장한 파일(팀·프로젝트·confirmed 들어 있음)을 개인판에서 열고 생성·저장
check("company billing fields preserved", billing_fields(saved_after) == billing_fields(original))
check("personal new file has no billing", billing_fields(new_personal_file) in ({}, EMPTY_BILLING))
# 회사판 서브프로세스: 개인판 새 파일을 열면 팀·프로젝트 다시 묻기(needs_billing)
check("company asks for personal file", company_open_and_gate(new_personal_file)["needs_billing"] is True)
```
- [ ] **Step 2:** 실행 → 실패 확인
- [ ] **Step 3:** 구현 + 설계서 §5.2·§6-5 문장 고침
- [ ] **Step 4:** 실행 → 통과 + 회사판 에이전트 시험(스크래치에 있는 것) 그대로 통과
- [ ] **Step 5:** 커밋

### Task 7: 창 토큰 — HTML 에서 빼고 launcher 창에만

**Files:** Create `static/nb_token.js`; Modify `app.py` (`_csrf_guard` 3760, `_render_html` 3905, 새 `/api/back-to-main`), `launcher.py` (창 주소 1036·584·`open_prompt_popup`·미니 1065, `_ask_running_app_back_to_main` 266), `templates/index.html`·`viewer.html`·`prompt_popup.html`·`mini.html` (head 맨 앞), `static/app.js:328`, `templates/viewer.html:358`, Test `tests/personal/t_token.py`

**Interfaces — Produces:**
- `app.window_url(path: str) -> str` — 회사 `"http://127.0.0.1:%d%s" % (PORT, path)`, 개인 같은 것 + `"#nbt=" + CSRF_TOKEN`. launcher 가 창을 만들거나 `load_url` 할 때 **전부** 이걸 쓴다
- 개인판 `_csrf_guard`: `/api/*` 는 방법 무관, 토큰(`X-NB-Token` 헤더 또는 쿠키 `nbt`, `hmac.compare_digest`) 없으면 403. 예외 `/api/version`·`/api/back-to-main`. 회사판 그대로
- 개인판 `_render_html`: `__CSRF_TOKEN__` → `""`
- `POST /api/back-to-main` (두 판 다): 미니창이 보이면 `MINI_HOOKS["toggle"]()` → `{ok: true, shown: true}`, 아니면 `{ok: true, shown: false}`. 그 밖엔 아무것도 안 함
- `nb_token.js` 동작(정해 둔다): `location.hash` 에서 `nbt=` 를 찾으면 → `window.NB_TOKEN` 에 두고, `document.cookie = "nbt=" + v + "; path=/; SameSite=Strict"`, `history.replaceState(null, "", location.pathname + location.search)`. 없으면 쿠키 `nbt`, 그것도 없으면 `<meta name="nb-csrf">` 값. `app.js`·`viewer.html` 의 토큰은 `window.NB_TOKEN`
- 개인판 `_ask_running_app_back_to_main`: `/api/mini/visible` 를 읽지 않고 `POST /api/back-to-main` 하나

- [ ] **Step 1: 실패하는 시험** (Flask test client, 판별 서브프로세스)
```python
# personal
check("html has no token", A.CSRF_TOKEN not in c.get("/").get_data(as_text=True) and A.CSRF_TOKEN not in c.get("/viewer?path=x").get_data(as_text=True))
check("GET api without token 403", c.get("/api/projects").status_code == 403 and c.get("/api/gallery/thumb?path=x").status_code == 403)
check("POST api without token 403", c.post("/api/generate").status_code == 403)
check("header ok", c.get("/api/status", headers={"X-NB-Token": A.CSRF_TOKEN}).status_code == 200)
c.set_cookie("nbt", A.CSRF_TOKEN, domain="localhost")
check("cookie ok", c.get("/api/status").status_code == 200)
check("version open", c2.get("/api/version").status_code == 200)
check("back-to-main open, harmless", c2.post("/api/back-to-main").get_json() == {"ok": True, "shown": False})
check("window_url fragment", A.window_url("/viewer?path=a").endswith("#nbt=" + A.CSRF_TOKEN))
# company (회귀)
check("company html has token", A.CSRF_TOKEN in c.get("/").get_data(as_text=True))
check("company GET open", c.get("/api/projects").status_code == 200)
check("company window_url plain", "#" not in A.window_url("/"))
```
- [ ] **Step 2:** 실행 → 실패 확인
- [ ] **Step 3:** 구현. 네 템플릿 head 맨 앞에 `<script src="/static/nb_token.js?v=__VERSION__"></script>` (`mini.html` 은 `{% raw %}` 안이어도 됨 — 치환은 `_render_html` 이 한다; `/mini`·`/prompt-popup` 이 `_render_html` 을 지나는지 확인하고 아니면 지나게)
- [ ] **Step 4:** 실행 → 통과. 진짜 창 확인은 Task 10
- [ ] **Step 5:** 커밋

### Task 8: 개인판 화면 — 내 키 · 띠 · 잠긴 모델 · 숨김

**Files:** Modify `templates/index.html` (Status 칸 47, 헤더 점 20, 새 `#myKeysModal`), `static/app.js` (`rebuildModelSelect` 1424, `refreshApiStatus` 4817, `renderGwBanner` 4858, `promptBillingIfNeeded` 846, `refreshBillingBar` 4273, 탭 "!" 표시), `static/style.css`

**Interfaces — Consumes:** Task 5 라우트·`gw.state == "personal"`·이벤트 `personal_keys`; Task 2 `<meta name="nb-edition">`.
**Produces:** `const PERSONAL = (meta nb-edition) === "personal"`, `openMyKeys()`, `closeMyKeys()`, `refreshMyKeys() -> Promise<void>`(→ `_myKeys`), `saveMyKey(provider)`, `deleteMyKey(provider)`, `_modelLock(m) -> "" | "키 필요" | "활성화 필요"`.

화면(설계서 §5.3 스케치대로):
- 개인판: `#billingBar` 숨기고 같은 자리에 `#myKeysBtn` ("🔑 내 키 N/3"); 헤더의 Vertex 점·글자 숨김; `promptBillingIfNeeded`·`refreshBillingBar`·탭 "!" 건너뜀
- 띠(`renderGwBanner` 의 개인판 갈래): 키 0개 → "개인판이에요 — 내 키를 넣어야 생성돼요 [키 넣기]" · phase `unreadable` → "저장해 둔 키를 열지 못했어요 — 다시 넣어 주세요 [키 넣기]" · phase `loading` → 띠 없음. 개인판 띠는 클릭을 받는다(회사 띠의 `pointer-events: none` 과 다름)
- 내 키 창: 회사마다 입력(비밀번호 칸) · [확인·저장] · 상태(✓ 확인됨 …끝4 / 확인 전 / 거절됨 / 이유) · [지우기] · 키 만드는 곳 링크(새 창 `window.open` — 실행자는 각 주소가 맞는 페이지인지 직접 열어 확인) · 한 줄 주의(Gemini 결제 · OpenAI 조직 인증 · BytePlus 모델 활성화) · 맨 아래 두 줄(암호화 · 요금은 본인 계정)
- `rebuildModelSelect`: 개인판이면 `_modelLock(m)` 이 있는 option 은 `disabled` + 글자 뒤 `" — " + lock`. 지금 탭의 모델이 잠겨도 그 값은 그대로 보인다(서버가 분명한 이유로 거절)
- `personal_keys` 이벤트의 `removed_company` → 토스트 "회사 키라서 지웠어요 — 개인 키를 넣어 주세요"
- 회사판 화면은 한 픽셀도 바뀌지 않는다

- [ ] **Step 1:** 구현 (`node --check static\app.js`; CSS 는 파서로 검증 — 메모리 `no-line-based-css-edits`)
- [ ] **Step 2: 격리 서버로 화면 확인** — 소스 실행(`NANOBANANA_EDITION=personal`, 격리 폴더, 가짜 세 회사·게이트웨이) 을 브라우저 창 대신 진짜 pywebview 로 띄우는 스크래치 하네스(`run_personal_preview.py`)로: 키 0개 띠 → 내 키 창 → 맞는 키/틀린 키/회사 키/인터넷 없음 → 잠긴 모델 메뉴 → 회사판으로 같은 화면이 그대로인지. 스크린샷 저장
- [ ] **Step 3: 사용자에게 화면을 보여 주고 OK 받기** (아이콘 색 시안도 같이 — Task 10 의 `app-personal.ico` 후보 2~3개). OK 전엔 Task 10 빌드로 넘어가지 않는다
- [ ] **Step 4:** 고칠 것 반영 후 커밋

### Task 9: 워커 `POST /personal/check`

**Files:** Modify `gateway/worker.js` (라우터 2410 부근, `knownKeyHashes` 305 재사용), Test `tests/personal/t_worker_check.mjs`

**Interfaces — Produces:** `POST /personal/check` 본문 `{p}` — `p` 가 `^[0-9a-f]{8}$` 아니면 400 `{ok:false}`. 맞으면 200 `{ok: true, hashes: (await knownKeyHashes(env)).filter(h => h.startsWith(p))}`. 출입증 없이 받는다. 횟수 제한: 격리 단위 메모리 `Map(ip → {n, t0})`, 10분에 60번 넘으면 429 (KV 쓰기 없음 — 무료 한도를 쓰지 않으려고). 로그·DB 에 아무것도 남기지 않는다.

- [ ] **Step 1: 실패하는 시험** (`import worker from "../../gateway/worker.js"`, 가짜 env)
```js
const env = { OPENAI_KEY: "sk-company-1", STUDIO_KEY: "", ARK_KEY: "ark-company-2", RETIRED_KEY_HASHES: sha("old-3"), TICKET_HASHES: "" };
ok("match current", (await call({ p: sha("sk-company-1").slice(0, 8) })).hashes.includes(sha("sk-company-1")));
ok("match retired", (await call({ p: sha("old-3").slice(0, 8) })).hashes.includes(sha("old-3")));
ok("no match", (await call({ p: "00000000" })).hashes.length === 0 || !(await call({ p: "00000000" })).hashes.includes(sha("sk-mine")));
ok("bad prefix 400", (await raw({ p: "xyz" })).status === 400 && (await raw({ key: "sk-company-1" })).status === 400);
ok("rate limit 429", await hammer(61) === 429);
ok("other routes untouched", (await rawPath("/health")).status === 200);
```
- [ ] **Step 2:** `node tests/personal/t_worker_check.mjs` → 실패 확인
- [ ] **Step 3:** 구현
- [ ] **Step 4:** 실행 → 통과
- [ ] **Step 5:** 커밋. **배포는 사용자 확인 뒤**: `cd gateway; env -u CLOUDFLARE_API_TOKEN npx wrangler deploy` (메모리 `cloudflare-wrangler-auth`). 배포 뒤 진짜 주소로 `p=00000000` 한 번(200, 빈 목록)과, 개발 PC 가 게이트웨이 시험 신원으로 받은 회사 키 하나의 앞 8글자로 한 번(그 해시가 들어 있음) — 키 원문은 화면에 찍지 않는다. 시험 신원 정리(메모리 `nanobanana-test-isolation`)

### Task 10: 빌드 · EXE 시험 (두 판 · 같이 켜기)

**Files:** Create `NanoBananaPersonal.spec`, `app-personal.ico`(Task 8 에서 사용자가 고른 색), `tests/personal/exe_both.py`

**Interfaces — Consumes:** 모든 앞 작업. **Produces:** `C:\NanoBanana_build\src\dist\NanoBanana.exe`, `...\dist\NanoBanana-Personal.exe`.

- [ ] **Step 1:** `NanoBananaPersonal.spec` = `NanoBanana.spec` 복사 + datas 에 `(os.path.join(base_dir, 'edition_personal', 'EDITION'), '.')` + `name='NanoBanana-Personal'` + `icon_path = app-personal.ico`. 회사 spec 엔 EDITION 이 없음을 빌드 뒤 확인(`pyi-archive_viewer` 로 두 EXE 목록 비교: 개인판에만 `EDITION`)
- [ ] **Step 2:** src 동기화(배포 스킬 §2 목록 + `edition.py personal_keys.py mini_window.py edition_personal\ static\nb_token.js templates\mini.html NanoBananaPersonal.spec app-personal.ico`) → build/dist 삭제 → 두 spec 각각 클린 빌드
- [ ] **Step 3:** 회사 EXE 스모크 — 지난 릴리스 스모크 스크립트(26건) 그대로 통과 + frozen 회사 EXE 를 `NANOBANANA_EDITION=personal` 환경으로 켜도 5656·회사 제목·HTML 에 토큰 있음
- [ ] **Step 4:** `exe_both.py` (격리 홈·데이터, 진짜 앱이 떠 있으면 중단):
```python
check("both up", port_open(5656) and port_open(5657))
check("titles", own_window(company_pid) == "NanoBanana" and own_window(personal_pid) == "NanoBanana Personal")
check("data split", exists(home/".nanobanana") and exists(home/".nanobanana-personal"))
check("personal html no token", token_meta(5657) == "" and token_meta(5656) != "")
check("personal api closed", http_get(5657, "/api/projects").status == 403)
check("second launch -> own window", relaunch(personal_exe) and foreground_title() .startswith("NanoBanana Personal"))
check("second launch company -> own window", relaunch(company_exe) and not foreground_title().startswith("NanoBanana Personal"))
# _MEI: 개인판을 켜고 6분 기다린 뒤(폴더 시각을 과거로 돌려도 됨) 회사판을 켬 → 개인판에서 뷰어·미니창 열기 정상
check("mei sweep spared live app", personal_viewer_opens() and personal_mini_shows())
# 미니창 셋 (Review Focus 5): 회사·개인 미니창 + 시험용 시댄스 기록(mini_harness 의 가짜) 같은 구석 → 겹침 0
check("three minis stacked", no_overlap(dock_rects()))
check("mini toggle via fragment token", click_mini_button(personal) and mini_visible(personal))
```
- [ ] **Step 5:** 개인판 EXE 단독: 키 0개 띠·내 키 창·뷰어·프롬프트 팝업·미니창이 토큰 문제 없이 동작(`window.open` 뷰어 대체 경로 포함)
- [ ] **Step 6:** 커밋 (spec·ico·시험)

### Task 11: 배포 저장소 · 첫 릴리스 · E2E · 문서

**Files:** 새 저장소의 `README.md`(스크래치 clone), Modify `CLAUDE.md`, `.claude/skills/나노바나나api배포/SKILL.md`, `인수인계_2026-07-30_전체.md`, `..\Freewill_미니창_공통규칙.md`

- [ ] **Step 1 (사용자 확인 뒤):** `gh repo create productionkhu-tech/freewill-nanobanana-personal --public --description "..."` → README 한 장(설계서 §5.6 항목: 무엇·받는 링크 `…/releases/latest/download/NanoBanana-Personal.exe`·SmartScreen·키 만드는 곳 3곳과 주의·요금은 본인 계정·무엇이 어디로 가는지·MCP 안 됨·윈도우만) 커밋·push
- [ ] **Step 2:** 문서: CLAUDE.md §0·§1·§2.5(설계서 요약)·§4(두 판 릴리스)·규칙 28~31(설계서 §7 의 네 줄)·§7 함정(`_MEI` 청소) / 배포 스킬에 개인판 동기화 목록·빌드·릴리스·E2E / 미니창 공통 규칙에 `nanobanana-personal.json` rank 1.5 한 줄 / 인수인계 기록. 시댄스 세션에 알림(ListAgents 로 찾고 SendMessage, 없으면 공통 규칙 문서에 적어 둔 것으로)
- [ ] **Step 3:** `personal` → main fast-forward, VERSION 을 공개하는 날 기준으로 올림, 배포 스킬 §1~§4 로 **회사판 릴리스**(평소대로, 노트엔 회사 사용자에게 보이는 것만 — 사실상 "고친 것: 두 번째 실행·임시 폴더 정리")
- [ ] **Step 4:** 같은 VERSION 으로 **개인판 릴리스**: `freewill-nanobanana-personal` 에 태그(그 저장소의 커밋) → draft → `NanoBanana-Personal.exe` + `NanoBanana-Personal.exe.sha256` → 공개. 노트는 한국어 평문
- [ ] **Step 5:** E2E 두 개 (공개 뒤 1분 넘게 기다림):
  - 회사: 직전 릴리스 EXE → 손대지 않고 새 버전, sha256 일치 (배포 스킬 §5)
  - 개인: 같은 소스를 옛 VERSION(예: 오늘 날짜 00)으로 찍은 개인판 EXE 를 격리 폴더에서 켬 → 손대지 않고 `freewill-nanobanana-personal` 의 새 릴리스로 바뀌는지, 바뀐 파일 sha256 == 릴리스 sha256. 끝나면 시험 폴더·`~/.nanobanana-personal` 시험 흔적 정리
  - 고정 링크 `…/releases/latest/download/NanoBanana-Personal.exe` 가 200 이고 sha256 같은지
- [ ] **Step 6:** 사용자에게 보고(받는 링크, 남은 일: 실제 개인 키로 무료 확인 → 사용자 OK 뒤 회사마다 한 장)
