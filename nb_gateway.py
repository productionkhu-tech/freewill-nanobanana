"""Client side of the NanoBanana key gateway.

The app used to carry the provider key itself, which meant reinstalling on ~70
machines every time a key rotated. With a gateway the machine carries a personal
token instead, and the token is obtained automatically: the app presents the
TICKET everyone already has (the retired OpenAI key still in their environment)
and gets a token of its own back.

Kept out of app.py because launcher.py imports app.py in the frozen build, and
this module has to stay importable from the gateway tests too.
"""
import json
import os
import platform
import sys
import threading
import time
import urllib.error
import urllib.request

TOKEN_FILENAME = "gateway.json"

# Where the gateway is, looked up at launch instead of compiled in.
#
# Baking an address into the build means a new office, a new router, or a DHCP
# lease change breaks every machine at once and can only be fixed by shipping a
# new release to 70 people. This file holds one line — the current address — so
# moving the gateway is a one-line edit that everyone picks up on next launch.
# It is public, which is fine: reaching the gateway still needs a ticket to
# enroll and a token to generate.
ENDPOINT_SOURCE = ("https://raw.githubusercontent.com/productionkhu-tech/"
                   "freewill-nanobanana/main/gateway_endpoint.txt")
ENDPOINT_CACHE = "gateway_endpoint.txt"

# Last-resort address compiled in. Empty = behave exactly like the old build and
# talk to the provider directly.
DEFAULT_GATEWAY_URL = ""


def _valid_url(u):
    u = (u or "").strip().rstrip("/")
    return u if u.startswith(("http://", "https://")) and len(u) < 300 else ""


def _fetch_endpoint(timeout=6):
    """Read the current address. Kept short: a slow network must not hold up
    startup, and a failure just falls through to the cached value."""
    try:
        req = urllib.request.Request(ENDPOINT_SOURCE,
                                     headers={"Cache-Control": "no-cache",
                                              "User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(4096).decode("utf-8", "replace")
    except Exception:
        return ""
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        return _valid_url(line)
    return ""


def gateway_url(data_dir=None):
    """Address for this run.

    Order: environment variable (a machine pointed somewhere on purpose), then
    the published address, then the last one that worked, then the compiled-in
    default. The cache is what keeps the app working when GitHub is unreachable
    but the gateway is perfectly fine."""
    env = _valid_url(os.environ.get("NANOBANANA_GATEWAY_URL", ""))
    if env:
        return env
    cache_path = os.path.join(data_dir, ENDPOINT_CACHE) if data_dir else None
    live = _fetch_endpoint()
    if live:
        if cache_path:
            try:
                os.makedirs(data_dir, exist_ok=True)
                with open(cache_path, "w", encoding="utf-8") as f:
                    f.write(live)
            except Exception:
                pass
        return live
    if cache_path and os.path.isfile(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = _valid_url(f.read())
            if cached:
                return cached
        except Exception:
            pass
    return _valid_url(DEFAULT_GATEWAY_URL)


def ticket():
    """What the app offers in exchange for a token.

    NANOBANANA_TICKET wins so a machine can be pointed at the gateway without
    disturbing a working OPENAI_API_KEY; otherwise the OpenAI key already on the
    machine IS the ticket, which is the whole point — nothing to distribute."""
    raw = (os.environ.get("NANOBANANA_TICKET", "")
           or os.environ.get("OPENAI_API_KEY", "")) or ""
    # A BOM or stray whitespace from a paste hashes to something else on the
    # server, and the only symptom would be a 403 that looks like a bad ticket.
    return raw.replace("﻿", "").strip()


def _token_path(data_dir):
    return os.path.join(data_dir, TOKEN_FILENAME)


def load_token(data_dir, url):
    """Cached token for this gateway, or None. A token issued by a different
    gateway is ignored rather than sent somewhere it means nothing."""
    try:
        with open(_token_path(data_dir), "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    if (d.get("url") or "").rstrip("/") != url:
        return None
    tok = d.get("token") or ""
    return tok if tok.startswith("nbt_") else None


def _save_token(data_dir, url, payload):
    os.makedirs(data_dir, exist_ok=True)
    p = _token_path(data_dir)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


# Cloudflare turns away the default urllib agent as a bot, and the whole
# exchange would fail with a 403 that looks nothing like a real problem.
USER_AGENT = "NanoBanana/1.0"


def _post(url, obj, timeout=20, headers=None):
    data = json.dumps(obj).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": USER_AGENT,
                                          **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8", "replace") or "{}")


def enroll(data_dir, url, app_version="", log=None):
    """Trade the ticket for a personal token and cache it.

    Returns (token, message). token is None when enrolment did not happen; the
    message is for the log, never for a dialog — a machine that cannot enrol
    falls back to the direct key and keeps working."""
    def _say(m):
        if log:
            log(m)

    tk = ticket()
    if not tk:
        return None, "gateway: no ticket on this machine"
    body = {
        "ticket": tk,
        "user": os.environ.get("USERNAME") or os.environ.get("USER") or "unknown",
        "machine": platform.node() or "unknown",
        "app_version": app_version,
    }
    try:
        status, d = _post(url + "/enroll", body)
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read().decode("utf-8", "replace") or "{}")
        except Exception:
            d = {}
        msg = d.get("error") or ("HTTP %d" % e.code)
        return None, "gateway: enrollment refused (%s)" % msg
    except Exception as e:
        return None, "gateway: unreachable (%s)" % str(e)[:80]

    tok = d.get("token") or ""
    if status != 200 or not tok:
        return None, "gateway: enrollment failed (%s)" % (d.get("error") or status)
    _save_token(data_dir, url, {
        "url": url,
        "token": tok,
        "token_id": d.get("token_id", ""),
        "user": body["user"],
        "machine": body["machine"],
        "enrolled_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    _say("gateway: enrolled as %s (%s)" % (d.get("token_id", "?"),
                                           "reused" if d.get("reused") else "new"))
    return tok, "ok"


def get_token(data_dir, app_version="", log=None):
    """Token to use for this run, or None to fall back to the direct key.

    Cached first: enrolment is a one-time event, and a gateway that is briefly
    down must not stop an app that already has its token."""
    url = gateway_url(data_dir)
    if not url:
        return None, ""
    tok = load_token(data_dir, url)
    if tok:
        return tok, url
    tok, msg = enroll(data_dir, url, app_version=app_version, log=log)
    if tok:
        return tok, url
    if log:
        log(msg)
    return None, url

# ---------------------------------------------------------------- key refresh
KEY_STAMP = "key_stamp.txt"


def _persist_windows_env(name, value):
    """Write a user environment variable and tell running programs about it.

    setx is used rather than the registry directly because it broadcasts the
    change; without that, Explorer keeps handing the old value to everything it
    launches until the next sign-in."""
    import subprocess
    try:
        # Discard setx's output instead of decoding it: on a Korean Windows it
        # comes back in cp949, and capturing it as text blew up in subprocess's
        # reader thread the moment the interpreter's default encoding was UTF-8.
        # Nothing here ever reads that output — only the exit code matters.
        r = subprocess.run(["setx", name, value],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=30, creationflags=0x08000000)  # no console window
        return r.returncode == 0
    except Exception:
        return False


def _persist_mac_env(name, value):
    """macOS reads its keys from keys.env, so update the line there."""
    import re
    home = os.path.expanduser("~")
    for path in (os.path.join(os.path.dirname(os.path.abspath(__file__)), "keys.env"),
                 os.path.join(home, ".nanobanana", "keys.env")):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            pat = r"(?m)^\s*%s\s*=.*$" % re.escape(name)
            new, n = re.subn(pat, "%s=%s" % (name, value), text)
            if n == 0:
                new = text.rstrip("\n") + "\n%s=%s\n" % (name, value)
            with open(path, "w", encoding="utf-8") as f:
                f.write(new)
            return True
        except Exception:
            continue
    return False


def refresh_provider_key(data_dir, log=None, validate=None):
    """Collect the current provider key and install it on this machine.

    The machine proves itself with the key it already has — the retired one to
    begin with — so nothing has to be handed out by hand. A key that arrives
    broken is discarded rather than installed: replacing a working key with a
    dead one would take the machine offline for no reason.

    Returns (changed, message)."""
    def say(m):
        if log:
            log(m)

    url = gateway_url(data_dir)
    if not url:
        return False, ""
    tk = ticket()
    if not tk:
        return False, "key refresh: nothing to identify this machine with"

    body = {
        "ticket": tk,
        "user": os.environ.get("USERNAME") or os.environ.get("USER") or "unknown",
        "machine": platform.node() or "unknown",
        "app_version": os.environ.get("NANOBANANA_APP_VERSION", ""),
    }
    # A token first: the key server no longer answers to the ticket alone, so a
    # leaked ticket is worth nothing once enrollment is closed, and one machine
    # can be cut off without disturbing the rest.
    token, _ = get_token(data_dir, app_version=body["app_version"], log=log)
    if not token:
        return False, "key refresh: this machine is not enrolled yet"
    try:
        status, d = _post(url + "/key", body, timeout=20,
                          headers={"Authorization": "Bearer " + token})
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read().decode("utf-8", "replace") or "{}")
        except Exception:
            d = {}
        return False, "key refresh refused (%s)" % (d.get("error") or ("HTTP %d" % e.code))
    except Exception as e:
        return False, "key server unreachable (%s)" % str(e)[:70]

    new_key = (d.get("key") or "").strip()
    if status != 200 or not new_key:
        return False, "key refresh failed (%s)" % (d.get("error") or status)

    current = (os.environ.get("OPENAI_API_KEY", "") or "").strip()
    if new_key == current:
        return False, ""          # already current, nothing to say

    if validate is not None and not validate(new_key):
        return False, "key refresh: the key that came back does not work, keeping the current one"

    os.environ["OPENAI_API_KEY"] = new_key      # this run
    ok = False
    if sys.platform == "win32":
        ok = _persist_windows_env("OPENAI_API_KEY", new_key)
    elif sys.platform == "darwin":
        ok = _persist_mac_env("OPENAI_API_KEY", new_key)
    try:
        os.makedirs(data_dir, exist_ok=True)
        with open(os.path.join(data_dir, KEY_STAMP), "w", encoding="utf-8") as f:
            f.write("%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%S"), d.get("key_id", "")))
    except Exception:
        pass
    say("key updated from the key server (%s)%s"
        % (d.get("key_id", "?"), "" if ok else " - this session only, could not save"))
    return True, "ok"


# ==========================================================================
# 사용량 집계 (팀/프로젝트별 비용)
# ==========================================================================
# 게이트웨이는 키만 나눠주고 생성은 앱이 직접 한다(속도 때문에 그렇게 정했다).
# 그래서 워커는 생성 트래픽을 볼 수 없고, 사용량은 앱이 사후 보고해야 한다.
#
# 보고가 생성을 방해하면 안 된다는 게 이 구현의 전부다: 생성 직후에는 로컬
# 파일에 한 줄 덧붙이기만 하고(마이크로초), 전송은 데몬 스레드가 모아서 한다.
# 네트워크가 죽어 있으면 줄이 쌓인 채로 남았다가 다음에 올라간다.

SPOOL_FILENAME = "usage_spool.jsonl"
_spool_lock = threading.Lock()


def _spool_path(data_dir):
    return os.path.join(data_dir, SPOOL_FILENAME)


def spool_usage(data_dir, event):
    """사용량 한 건을 로컬에 적어둔다. 실패해도 절대 위로 던지지 않는다 —
    집계가 안 되는 것보다 생성이 막히는 게 훨씬 나쁘다."""
    try:
        os.makedirs(data_dir, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False)
        with _spool_lock:
            with open(_spool_path(data_dir), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return True
    except Exception:
        return False


def _read_spool(data_dir, limit=200):
    try:
        with _spool_lock:
            with open(_spool_path(data_dir), "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
    except Exception:
        return [], 0
    events = []
    for ln in lines[:limit]:
        try:
            events.append(json.loads(ln))
        except Exception:
            pass          # 깨진 줄은 버린다 (아래에서 같이 소비 처리된다)
    return events, min(len(lines), limit)


def _drop_spool_head(data_dir, n):
    """전송에 성공한 앞쪽 n줄을 덜어낸다. 원자적으로 교체해서, 도중에 죽어도
    파일이 반쯤 잘린 상태로 남지 않게 한다."""
    p = _spool_path(data_dir)
    try:
        with _spool_lock:
            with open(p, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
            rest = lines[n:]
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                if rest:
                    f.write("\n".join(rest) + "\n")
            os.replace(tmp, p)
        return True
    except Exception:
        return False


def fetch_catalog(data_dir, app_version="", log=None):
    """팀/프로젝트 목록. 서버에서 바꾸면 다음 조회에 바로 반영된다.
    Returns (catalog_dict, error_message)."""
    url = gateway_url(data_dir)
    if not url:
        return None, "게이트웨이 주소를 찾지 못했습니다"
    token, _ = get_token(data_dir, app_version=app_version, log=log)
    if not token:
        return None, "이 PC가 아직 게이트웨이에 등록되지 않았습니다"
    try:
        req = urllib.request.Request(
            url + "/catalog",
            headers={"User-Agent": USER_AGENT, "Authorization": "Bearer " + token})
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        return None, "목록을 받지 못했습니다 (HTTP %d)" % e.code
    except Exception as e:
        return None, "목록 서버에 연결하지 못했습니다 (%s)" % str(e)[:60]
    if not d.get("ok"):
        return None, d.get("error") or "목록을 받지 못했습니다"
    return d, None


def flush_usage(data_dir, app_version="", log=None, batch=200):
    """쌓인 사용량을 한 번 올린다. 올린 만큼만 지운다.

    이벤트 id 는 앱이 만들고 서버가 INSERT OR IGNORE 로 받으므로, 전송은
    됐는데 응답을 못 받아 재전송하는 경우에도 중복이 쌓이지 않는다.
    Returns (sent_count, error_message_or_None)."""
    events, taken = _read_spool(data_dir, batch)
    if not taken:
        return 0, None
    url = gateway_url(data_dir)
    if not url:
        return 0, "no gateway"
    token = load_token(data_dir, url)
    if not token:
        return 0, "not enrolled"
    try:
        status, d = _post(url + "/usage", {"events": events}, timeout=25,
                          headers={"Authorization": "Bearer " + token})
    except urllib.error.HTTPError as e:
        return 0, "HTTP %d" % e.code
    except Exception as e:
        return 0, str(e)[:60]
    if status != 200 or not d.get("ok"):
        return 0, str(d.get("error") or status)[:60]
    _drop_spool_head(data_dir, taken)
    return taken, None
