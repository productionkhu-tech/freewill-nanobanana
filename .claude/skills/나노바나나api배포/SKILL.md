---
name: 나노바나나api배포
description: NanoBanana 정식 배포(릴리스) 절차 전체 — VERSION bump → 클린 빌드 → 시크릿 스캔 → 커밋/태그 → GitHub Release(EXE + sha256 사이드카) → E2E 자동업데이트 검증 → 로컬 재기동. "배포해줘", "릴리스 내줘", "패치 내줘", "버전 올려줘", "XX 패치해봐" 요청 시 반드시 이 스킬을 따를 것.
---

# NanoBanana 배포 스킬

2026-07-20 배포 세션(v2026-07-2001~2004)에서 실전 검증된 절차. **순서대로 전부** 수행한다.
업데이트 관련 코드(launcher.py / updater.py / app.py의 update 경로)가 바뀐 릴리스는 E2E(§7)가 **의무**다 (CLAUDE.md 규칙 15).

## 0. 준비물 확인

- `GH_TOKEN` 환경변수 (GitHub 토큰) — 없으면 릴리스 생성/업로드 불가, 사용자에게 요청
- 빌드 venv: `C:\NanoBanana_build\venv\Scripts\python.exe` (CLAUDE.md의 venv_build 표기는 낡은 것)
- Reve 키 bat: `레베 api key.bat` (프로젝트 루트) — 재기동 시 키 주입용
- 릴리스 저장소: `productionkhu-tech/freewill-nanobanana`

## 1. VERSION bump

- 형식 `vYYYY-MM-DDNN`. **오늘 첫 릴리스면 NN=01**, 같은 날 추가 릴리스는 02, 03… (날짜가 바뀌면 다시 01부터)
- ```powershell
  Set-Content -Path VERSION -Value "vYYYY-MM-DDNN" -Encoding ascii -NoNewline
  ```

## 2. 소스 동기화 + 클린 빌드

```powershell
Set-Location "C:\Users\user\Desktop\기획 파일\TA\앱개발\나노바나나 api"
# 문법 체크 (수정된 파일에 한해)
node --check static\app.js
& "C:\NanoBanana_build\venv\Scripts\python.exe" -m py_compile app.py
& "C:\NanoBanana_build\venv\Scripts\python.exe" -m py_compile launcher.py
& "C:\NanoBanana_build\venv\Scripts\python.exe" -m py_compile updater.py
# 동기화 (변경된 파일 + VERSION은 항상)
Copy-Item ".\app.py",".\launcher.py",".\updater.py",".\VERSION" "C:\NanoBanana_build\src\" -Force
Copy-Item ".\static\app.js",".\static\style.css" "C:\NanoBanana_build\src\static\" -Force
Copy-Item ".\templates\index.html",".\templates\viewer.html",".\templates\prompt_popup.html" "C:\NanoBanana_build\src\templates\" -Force
# 클린 빌드 (캐시 재사용 금지 — Remove-Item은 훅 오해 방지를 위해 개별 명령으로)
Get-Process NanoBanana -ErrorAction SilentlyContinue | Stop-Process -Force -Confirm:$false
Remove-Item "C:\NanoBanana_build\src\build" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item "C:\NanoBanana_build\src\dist" -Recurse -Force -ErrorAction SilentlyContinue
& "C:\NanoBanana_build\venv\Scripts\python.exe" -m PyInstaller "C:\NanoBanana_build\src\NanoBanana.spec" --noconfirm --clean --distpath "C:\NanoBanana_build\src\dist" --workpath "C:\NanoBanana_build\src\build"
# dist 배포 + 해시 (소문자 hex — 릴리스 body/사이드카에 쓸 값)
Copy-Item "C:\NanoBanana_build\src\dist\NanoBanana.exe" ".\dist\NanoBanana\NanoBanana.exe" -Force
(Get-FileHash ".\dist\NanoBanana\NanoBanana.exe" -Algorithm SHA256).Hash.ToLower()
```

주의: 은퇴 소스(`_retired/`)는 빌드 src에 절대 복사하지 않는다.

## 3. 시크릿 스캔 + 커밋 + 태그

- **`git add .` 금지 — 변경 파일을 이름으로만 add.** 절대 커밋 금지: `setup_env.bat`, `*api key.bat`, `keys.env`, `*-key.json`, `service_account*.json`, `_retired/`
- ```powershell
  git add VERSION app.py launcher.py  # 실제 변경 파일만 나열
  git diff --cached | Select-String -Pattern "papi\.|ark-|AIzaSy|sk-|BEGIN PRIVATE KEY" | Select-Object -First 3   # 히트 0건이어야 함
  ```
- 커밋 메시지에 따옴표/한글이 섞이면 PS 5.1에서 깨진다 → **`git commit -F <파일>`** (메시지를 UTF-8 파일로 먼저 작성). 기술 상세는 커밋 메시지에(개발자용), 사용자용 문구는 릴리스 노트에.
- ```powershell
  git push origin main
  git tag -a vYYYY-MM-DDNN -m vYYYY-MM-DDNN
  git push origin vYYYY-MM-DDNN
  ```

## 4. GitHub Release 생성 + 자산 2종 업로드

**⚠ 반드시 draft:true로 만들고 → 자산 2종 업로드 → 마지막에 draft:false로 공개 (2026-07-30 사고)**
릴리스를 공개 상태로 먼저 만들면 GitHub이 **그 즉시** `/releases/latest`에 새 태그를 노출한다.
36MB EXE 업로드에 4~5초가 걸리므로 그 사이에 업데이트를 시도한 앱은 아직 없는 파일을 받으려다
**HTTP 404**를 맞는다(실측: 모든 릴리스에서 4~5초 창 존재). 자동 업데이트가 켜진 지금은
그 창에 걸린 사용자에게 실패로 보인다. **draft로 올리면 자산이 다 붙기 전엔 아무도 못 보므로 창이 0초**가 된다.

한국어 body는 **반드시 Python urllib으로 JSON 바이트 POST** (bash/PS 인라인은 cp949로 깨짐).
스크래치 폴더에 아래 스크립트를 쓰고 빌드 venv 파이썬으로 실행한다 (`PYTHONIOENCODING=utf-8`, `PYTHONUTF8=1`).

```python
import os, json, subprocess, urllib.request
token = os.environ["GH_TOKEN"]
repo = "productionkhu-tech/freewill-nanobanana"
tag = "vYYYY-MM-DDNN"
sha = "<소문자 sha256>"

# 릴리스 노트 톤: 평문 한국어 bullet 2~5줄, "뭐가 좋아졌는지"만.
# 마크다운/백틱/개발 용어(sha256, subprocess, API, 폴백 등) 금지 — sha256: 줄만 예외(프론트가 자동 필터).
body = """- 사용자용 한국어 bullet
- ...

sha256: %s""" % sha

# draft:true -> 자산이 다 붙을 때까지 /releases/latest에 안 보인다 (404 창 제거)
payload = json.dumps({"tag_name": tag, "name": tag, "body": body, "draft": True},
                     ensure_ascii=False).encode("utf-8")
req = urllib.request.Request("https://api.github.com/repos/%s/releases" % repo, method="POST", data=payload,
    headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
             "Content-Type": "application/json; charset=utf-8", "User-Agent": "NB"})
rid = json.loads(urllib.request.urlopen(req, timeout=30).read())["id"]
print("release id (draft):", rid)

# 자산 1: EXE
r = subprocess.run(["curl", "-sS", "-X", "POST", "-H", "Authorization: Bearer " + token,
    "-H", "Content-Type: application/octet-stream",
    "--data-binary", "@dist/NanoBanana/NanoBanana.exe",
    "https://uploads.github.com/repos/%s/releases/%s/assets?name=NanoBanana.exe" % (repo, rid)],
    capture_output=True, text=True, timeout=600)
print(json.loads(r.stdout).get("state"))

# 자산 2: sha256 사이드카 — **필수** (api.github.com 403 시 업데이터 폴백이 이걸 읽음, v2026-07-2004+)
sp = os.path.join(os.environ.get("TEMP", "."), "NanoBanana.exe.sha256")
open(sp, "w", encoding="ascii").write(sha + "  NanoBanana.exe\n")
r2 = subprocess.run(["curl", "-sS", "-X", "POST", "-H", "Authorization: Bearer " + token,
    "-H", "Content-Type: text/plain", "--data-binary", "@" + sp,
    "https://uploads.github.com/repos/%s/releases/%s/assets?name=NanoBanana.exe.sha256" % (repo, rid)],
    capture_output=True, text=True, timeout=120)
print(json.loads(r2.stdout).get("state"))

# ★ 자산 2종이 다 올라간 뒤에만 공개 (여기서부터 사용자에게 보임)
pub = json.dumps({"draft": False}).encode("utf-8")
req3 = urllib.request.Request("https://api.github.com/repos/%s/releases/%s" % (repo, rid),
    method="PATCH", data=pub,
    headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
             "Content-Type": "application/json", "User-Agent": "NB"})
print("published:", json.loads(urllib.request.urlopen(req3, timeout=30).read())["draft"] is False)
```

업로드 후 확인: `GET /releases/latest`의 tag_name이 새 태그이고 draft/prerelease가 false이며
**자산 2종(NanoBanana.exe, NanoBanana.exe.sha256)이 모두 붙어 있는지**. 공개 직후 바로 다운로드
URL을 한 번 눌러 200이 나오는지도 확인하면 확실하다.

## 5. E2E 검증 (업데이트 경로 변경 시 의무, 그 외에도 강력 권장)

"코드가 맞으니 되겠지"로 릴리스한 v1733~v1738이 전부 실패했던 역사가 있다. 실제 구버전 바이너리로 검증한다.

**⚠ 필수 사전조건 — 이거 안 하면 결과가 거짓이 된다 (2026-07-30 실제로 당함)**
E2E 시작 전 **다른 NanoBanana가 떠 있으면 안 된다.** 포트 5656이 이미 잡혀 있으면 방금 띄운
테스트 바이너리는 `is_port_in_use` 폴백으로 **조용히 종료**하고, 스크립트는 그것도 모른 채
**남의 앱(사용자 실행본)의 /api/version을 측정**한다. 그 앱이 마침 자동 업데이트되면
"버전은 올라갔는데 sha256 불일치" 라는 가짜 FAIL이 뜬다.

**그리고 `/api/events` 를 보는 창이 하나도 없어야 한다.** 브라우저 패널·다른 탭이 같은 포트를
열어두면 이벤트 큐(pop 방식)를 가로채 앱 창이 `update_status` 를 못 받고, 자동 업데이트가
조용히 일어나지 않는다 (2026-08-05 실제로 당함 — 가짜 FAIL. CLAUDE.md §2에 적어둔 함정을
테스트 도구가 그대로 밟은 것). 테스트 전에 그 창을 다른 주소로 옮길 것.

```powershell
Get-Process NanoBanana -ErrorAction SilentlyContinue | Select-Object Id, Path   # 0건이어야 함
(Test-NetConnection 127.0.0.1 -Port 5656 -WarningAction SilentlyContinue).TcpTestSucceeded  # False여야 함
```
스크립트 안에서도 방어할 것: 시작 전 5656이 열려 있으면 즉시 중단, 그리고 마지막에
**측정 대상 파일의 sha256이 새 버전과 일치하는지**를 버전 문자열보다 우선해서 판정.

스크래치에 아래 스크립트 작성 → 실행 (OLD_TAG = 직전 릴리스, NEW_VER/NEW_SHA = 방금 릴리스):

```python
# E2E: 직전 릴리스 asset을 받아 실행 -> HTTP로 apply-update -> 스왑 -> 새 버전 부팅 확인
import os, json, time, hashlib, re, subprocess, urllib.request
token = os.environ["GH_TOKEN"]
repo = "productionkhu-tech/freewill-nanobanana"
OLD_TAG = "v..."; NEW_VER = "v..."; NEW_SHA = "<hex>"
d = r"C:\NanoBanana_build\e2e"; os.makedirs(d, exist_ok=True)
exe = os.path.join(d, "NanoBanana.exe")

req = urllib.request.Request("https://api.github.com/repos/%s/releases/tags/%s" % (repo, OLD_TAG),
    headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "User-Agent": "NB"})
rel = json.loads(urllib.request.urlopen(req, timeout=30).read())
aid = [a for a in rel["assets"] if a["name"] == "NanoBanana.exe"][0]["id"]
print("old asset id", aid, "size", [a for a in rel["assets"] if a["name"] == "NanoBanana.exe"][0]["size"])
req2 = urllib.request.Request("https://api.github.com/repos/%s/releases/assets/%s" % (repo, aid),
    headers={"Authorization": "Bearer " + token, "Accept": "application/octet-stream", "User-Agent": "NB"})
open(exe, "wb").write(urllib.request.urlopen(req2, timeout=600).read())
print("downloaded old exe:", os.path.getsize(exe))

subprocess.Popen([exe], cwd=d)
for _ in range(60):
    time.sleep(1)
    try:
        v = json.loads(urllib.request.urlopen("http://127.0.0.1:5656/api/version", timeout=2).read())["version"]
        break
    except Exception: pass
print("old boot version:", v)
assert v == OLD_TAG
html = urllib.request.urlopen("http://127.0.0.1:5656/", timeout=10).read().decode("utf-8")
tok = re.search(r'name="nb-csrf" content="([^"]+)"', html).group(1)
req3 = urllib.request.Request("http://127.0.0.1:5656/api/apply-update", method="POST", data=b"{}",
    headers={"Content-Type": "application/json", "X-NB-Token": tok})
print("apply-update:", urllib.request.urlopen(req3, timeout=30).read().decode())
time.sleep(5)
nv = ""
for _ in range(120):
    time.sleep(1)
    try:
        nv = json.loads(urllib.request.urlopen("http://127.0.0.1:5656/api/version", timeout=2).read())["version"]
        if nv == NEW_VER: break
    except Exception: pass
print("post-swap version:", nv)
h = hashlib.sha256(open(exe, "rb").read()).hexdigest()
print("swapped exe sha256 ok:", h == NEW_SHA)
print("E2E RESULT:", "PASS" if (nv == NEW_VER and h == NEW_SHA) else "FAIL")
```

PASS 후 정리: NanoBanana 프로세스 kill + `C:\NanoBanana_build\e2e` 삭제.

**부팅 자동 체크 확인** (팝업 경로): 새 빌드를 켜고 `GET /api/logs`에 몇 초 내
`Update available: ...` 또는 `Already on latest version` 로그가 찍히는지 확인.
⚠️ `/api/events`를 직접 폴링하면 이벤트를 가로채 앱 화면의 팝업이 사라진다 — 진단 목적이 아니면 로그로만 볼 것.
⚠️ `/api/check-update`는 호출자에게만 JSON을 주고 화면엔 아무것도 안 띄운다 (원격으로 팝업 못 띄움).

## 6. 로컬 재기동

```powershell
Get-Process NanoBanana -ErrorAction SilentlyContinue | Stop-Process -Force -Confirm:$false
$bat = Get-Content ".\레베 api key.bat" -Raw
if ($bat -match 'REVE_API_KEY\s+"([^"]+)"') { $env:REVE_API_KEY = $Matches[1] }
Start-Process ".\dist\NanoBanana\NanoBanana.exe"
# 8초 후 http://127.0.0.1:5656/api/version 이 새 버전인지 확인
```

## 7. 마무리 기록

- `인수인계_*.md`(프로젝트 루트 최신본)에 릴리스 요약 추가 — UTF-8(no BOM) AppendAllText로 (PS Add-Content 인코딩 함정 회피)
- 사용자 안내가 필요한 변경(수동 조치 등)이면 명시적으로 보고

## 8. 함정 모음 (전부 실전에서 당한 것)

| 함정 | 대응 |
|---|---|
| launcher의 `_bg_update_check` 스레드 기동을 끄면 안 됨 | v2026-06-1201에서 주석 처리된 채 6주간 배포 → 전 사용자 팝업 실종. 절대 재발 금지 |
| api.github.com 403 (무인증 IP당 60회/h, 공유 NAT) | 업데이터가 조립 URL+사이드카로 폴백(2004+). **사이드카 업로드를 빼먹으면 폴백 무결성 검증이 빠짐** |
| raw.githubusercontent CDN 스테일 | 체크는 Releases API 1순위 (이미 구현) — raw만 보고 "이미 최신" 판단 금지 |
| 한국어 릴리스 body 인라인 curl | cp949 깨짐 → Python urllib JSON 바이트 POST만 사용 |
| PS 5.1 커밋 메시지 | 따옴표/한글 → `git commit -F 파일` |
| PyInstaller 캐시 | 매 빌드 전 build/dist 삭제 (--clean 포함해도 폴더 삭제 먼저) |
| 6/12~7/20-01 구버전 사용자 | 부팅 팝업 없는 빌드 — 푸터 버전 클릭 1회 안내 필요 (수동 체크는 전 버전 동작) |
| Program Files 설치 사용자 | 자동 업데이트 불가 (UAC) — 일반 폴더로 이동 안내 |
