#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
naver_cafe_dl.py - 네이버 카페 동영상 다운로더 (기본 1080p)

- 게시글 URL 하나 또는 게시판(메뉴) URL 전체를 받아서 동영상을 내려받습니다.
- 파일 이름은 게시글 제목의 대괄호 안 내용을 사용합니다.
    예) "7월 1일 [OT/Be동사1]"  ->  "OT-Be동사1.mp4"
- 로그인 쿠키(NID_AUT / NID_JST / NID_SES)로 인증합니다.
- 파이썬 표준 라이브러리만 사용합니다 (설치할 패키지 없음).

사용 예)
    python naver_cafe_dl.py https://cafe.naver.com/f-e/cafes/16075980/menus/194?viewType=L
    python naver_cafe_dl.py https://cafe.naver.com/f-e/cafes/16075980/articles/12345
    python naver_cafe_dl.py <URL> --quality 720 -o D:\\강의
    python naver_cafe_dl.py <메뉴URL> --list        (다운로드 없이 목록만)
"""

from __future__ import annotations

import argparse
import gzip
import html as html_mod
import json
import os
import re
import sys
import threading
import time
import zlib
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

__version__ = "1.0.0"

# --------------------------------------------------------------------------- #
# 상수
# --------------------------------------------------------------------------- #

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)
CAFE_REFERER = "https://cafe.naver.com/"
COOKIE_KEYS = ("NID_AUT", "NID_JST", "NID_SES")
DEFAULT_OUTDIR = "downloads"
CHUNK = 256 * 1024
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 게시글 본문 API (우선순위 순서대로 시도. v2 는 500 을 반환하므로 제외)
ARTICLE_ENDPOINTS = (
    "https://article.cafe.naver.com/gw/v4/cafes/{cafe}/articles/{art}"
    "?useCafeId=true&requestFrom=A",
    "https://apis.naver.com/cafe-web/cafe-articleapi/v4/cafes/{cafe}/articles/{art}"
    "?useCafeId=true&requestFrom=A",
    "https://apis.naver.com/cafe-web/cafe-articleapi/v3/cafes/{cafe}/articles/{art}"
    "?query=&useCafeId=true&requestFrom=A",
    "https://apis.naver.com/cafe-web/cafe-articleapi/v2.1/cafes/{cafe}/articles/{art}"
    "?query=&useCafeId=true&requestFrom=A",
)

# 게시판(메뉴) 글 목록 API (우선순위 순서대로 시도)
LIST_ENDPOINTS = (
    "https://apis.naver.com/cafe-web/cafe-boardlist-api/v1/cafes/{cafe}/menus/{menu}/articles"
    "?page={page}&pageSize={size}&sortBy=TIME&viewType=L",
    "https://apis.naver.com/cafe-web/cafe2/ArticleListV2dot1.json"
    "?search.clubid={cafe}&search.menuid={menu}&search.queryType=lastArticle"
    "&search.page={page}&search.perPage={size}&ad=false",
)

# 동영상 재생정보 API.
#   현재 카페 플레이어는 neonplayer(DASH MPD)를 쓰고, 예전 rmcnmv(JSON)도 아직 살아 있습니다.
#   URL 이 아니라 "응답의 첫 글자"로 형식을 판별합니다.
PLAY_ENDPOINTS = (
    ("https://apis.naver.com/neonplayer/vodplay/v3/playback/{vid}", "application/dash+xml"),
    ("https://apis.naver.com/neonplayer/vodplay/v2/playback/{vid}", "application/dash+xml"),
    ("https://apis.naver.com/rmcnmv/rmcnmv/vod/play/v2.0/{vid}", "application/json"),
    ("http://play.rmcnmv.naver.com/vod/play/v2.0/{vid}", "application/json"),
)
PLAY_REFERER = "https://serviceapi.nmv.naver.com/"
# 일부 서버가 400 을 낼 때만 덧붙이는 보조 파라미터
PLAY_PARAMS_EXTRA = {"env": "real", "lc": "ko_KR", "cpl": "ko_KR"}

# --------------------------------------------------------------------------- #
# 출력 유틸
# --------------------------------------------------------------------------- #


# 여러 스레드가 동시에 출력해도 줄이 섞이지 않도록
PRINT_LOCK = threading.RLock()
# Ctrl+C 등으로 전체 중단을 알리는 신호 (다운로드 루프가 매 청크마다 확인)
ABORT = threading.Event()


def init_console() -> None:
    """윈도우 cp949 콘솔에서 한글이 깨지지 않도록."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def log(msg: str = "") -> None:
    print(msg, flush=True)


def info(msg: str) -> None:
    print("[i] " + msg, flush=True)


def warn(msg: str) -> None:
    print("[!] " + msg, flush=True)


def die(msg: str, code: int = 1) -> "None":
    print("\n[X] " + msg, file=sys.stderr, flush=True)
    sys.exit(code)


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{int(n)}B"
        n /= 1024.0
    return f"{n:.1f}GB"


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


class AuthError(RuntimeError):
    """쿠키 만료 / 권한 없음 - 계속 진행해도 소용없는 오류."""


class HttpError(Exception):
    def __init__(self, status: int, url: str, body: bytes = b""):
        self.status = status
        self.url = url
        self.body = body
        super().__init__(f"HTTP {status} :: {url}")


def _decompress(headers, raw: bytes) -> bytes:
    enc = (headers.get("Content-Encoding") or "").lower()
    try:
        if "gzip" in enc:
            return gzip.decompress(raw)
        if "deflate" in enc:
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
    except Exception:
        return raw
    return raw


def build_headers(cookies=None, referer: str = CAFE_REFERER, extra=None) -> dict:
    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "identity",
        "Referer": referer,
        "Connection": "close",
    }
    if cookies:
        headers["Cookie"] = cookie_header(cookies)
    if extra:
        headers.update(extra)
    return headers


def http_get(url: str, cookies=None, referer: str = CAFE_REFERER, extra=None,
             timeout: int = 30, retries: int = 3):
    """(body_bytes, content_type, final_url) 반환. 실패하면 예외."""
    headers = build_headers(cookies, referer, extra)
    last_err: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = _decompress(resp.headers, resp.read())
                return raw, (resp.headers.get("Content-Type") or ""), resp.geturl()
        except urllib.error.HTTPError as e:
            body = b""
            try:
                body = _decompress(e.headers, e.read())
            except Exception:
                pass
            last_err = HttpError(e.code, url, body)
            # 인증/권한/없는 리소스는 재시도해도 소용없음
            if e.code in (400, 401, 403, 404, 410):
                break
        except Exception as e:  # URLError, timeout, ...
            last_err = e
        if attempt < retries - 1:
            time.sleep(1.0 + attempt)
    raise last_err if last_err else RuntimeError("알 수 없는 네트워크 오류: " + url)


def http_get_json(url: str, cookies=None, **kw):
    body, _ctype, _final = http_get(url, cookies=cookies, **kw)
    text = body.decode("utf-8", "replace").lstrip("\ufeff").strip()
    # 일부 응답이 JSONP 형태로 오는 경우 방어
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    return json.loads(text)


# --------------------------------------------------------------------------- #
# 쿠키
# --------------------------------------------------------------------------- #

COOKIE_RE = re.compile(
    r"\b(NID_AUT|NID_JST|NID_SES)[\"']?\s*[=:]\s*[\"']?([^;,\r\n\"']+)", re.I
)
PLACEHOLDERS = {
    "", "your_value", "여기에", "여기에_붙여넣기", "여기에값붙여넣기",
    "paste_here", "xxx", "값", "value",
}


def cookie_header(cookies: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items() if v)


def parse_cookie_text(text: str) -> dict:
    """`KEY=VALUE`, `KEY: VALUE`, `a=b; c=d` 등 어떤 형태로 붙여넣어도 뽑아냅니다."""
    out: dict = {}
    for key, val in COOKIE_RE.findall(text):
        val = val.strip()
        if val and val.lower() not in PLACEHOLDERS:
            out[key.upper()] = val
    return out


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        return f.read()


def cookies_ok(c: dict) -> bool:
    # 실무상 NID_AUT + NID_SES 조합이면 인증됩니다. NID_JST는 있으면 함께 보냅니다.
    return bool(c.get("NID_AUT") and c.get("NID_SES"))


def prompt_cookies() -> dict:
    log("")
    log("=" * 68)
    log(" 네이버 로그인 쿠키 입력")
    log("=" * 68)
    log(" 크롬에서 https://cafe.naver.com 접속 후 F12 →")
    log(" [Application] 탭 → 좌측 Storage → Cookies → https://cafe.naver.com")
    log(" 에서 NID_AUT, NID_JST, NID_SES 의 Value 를 복사해 붙여넣으세요.")
    log("")
    log(" ※ NID_AUT / NID_SES 는 HttpOnly 라서 콘솔의 document.cookie 로는")
    log("   보이지 않습니다. 반드시 Application 탭에서 확인하세요.")
    log("-" * 68)

    collected: dict = {}
    for key in COOKIE_KEYS:
        if collected.get(key):
            continue
        try:
            raw = input(f"  {key} = ").strip()
        except (EOFError, KeyboardInterrupt):
            log("")
            die("입력이 취소되었습니다.")
        if not raw:
            continue
        # 통째로 붙여넣은 경우(여러 쿠키가 한 줄에) 자동 인식
        found = parse_cookie_text(raw)
        if found:
            collected.update(found)
        else:
            collected[key] = raw

    if not cookies_ok(collected):
        die("NID_AUT 와 NID_SES 는 반드시 필요합니다. 다시 실행해 주세요.")

    path = os.path.join(SCRIPT_DIR, "cookies.txt")
    try:
        ans = input(f"\n  이 값을 {path} 에 저장할까요? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = "n"
    if ans in ("", "y", "yes", "ㅛ"):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("# 네이버 로그인 쿠키 (이 파일은 절대 공유하지 마세요)\n")
                for key in COOKIE_KEYS:
                    if collected.get(key):
                        f.write(f"{key}={collected[key]}\n")
            info(f"저장했습니다: {path}")
        except OSError as e:
            warn(f"저장 실패: {e}")
    log("")
    return collected


def load_cookies(path: str | None, interactive: bool = True) -> dict:
    if path:
        if not os.path.exists(path):
            die(f"쿠키 파일을 찾을 수 없습니다: {path}")
        found = parse_cookie_text(read_text(path))
        if not cookies_ok(found):
            die(f"{path} 안에서 NID_AUT / NID_SES 를 찾지 못했습니다.")
        info(f"쿠키 파일 사용: {path}")
        return found

    env = {k: os.environ.get(k, "").strip() for k in COOKIE_KEYS}
    env = {k: v for k, v in env.items() if v}
    if cookies_ok(env):
        info("환경변수에서 쿠키를 읽었습니다.")
        return env

    for cand in (os.path.join(SCRIPT_DIR, "cookies.txt"),
                 os.path.join(os.getcwd(), "cookies.txt")):
        if os.path.exists(cand):
            found = parse_cookie_text(read_text(cand))
            if cookies_ok(found):
                info(f"쿠키 파일 사용: {cand}")
                return found
            warn(f"{cand} 에서 유효한 쿠키를 찾지 못했습니다.")

    if interactive and sys.stdin is not None and sys.stdin.isatty():
        return prompt_cookies()

    die("쿠키가 없습니다. cookies.txt 를 만들거나 --cookies 옵션을 사용하세요.")
    return {}  # unreachable


# --------------------------------------------------------------------------- #
# URL 파싱
# --------------------------------------------------------------------------- #

RE_NEW_ARTICLE = re.compile(r"cafe\.naver\.com/(?:f-e|ca-fe)/cafes/(\d+)/articles/(\d+)")
RE_NEW_MENU = re.compile(r"cafe\.naver\.com/(?:f-e|ca-fe)/cafes/(\d+)/menus/(\d+)")
RE_LEGACY_ARTICLE = re.compile(r"cafe\.naver\.com/([A-Za-z0-9_-]+)/(\d+)")
RE_LEGACY_NAME = re.compile(r"cafe\.naver\.com/([A-Za-z0-9_-]+)/?$")
RE_Q_CLUB = re.compile(r"[?&](?:clubid|cafeId|search\.clubid)=(\d+)", re.I)
RE_Q_ART = re.compile(r"[?&](?:articleid|articleId)=(\d+)", re.I)
RE_Q_MENU = re.compile(r"[?&](?:menuid|menuId|search\.menuid)=(\d+)", re.I)


def resolve_cafe_id(name: str, cookies: dict) -> str | None:
    """카페 주소(영문 ID)로 숫자 clubId 를 찾습니다."""
    try:
        body, _ct, _fu = http_get(f"https://cafe.naver.com/{name}", cookies=cookies,
                                  referer="https://www.naver.com/")
    except Exception:
        return None
    text = body.decode("utf-8", "replace")
    for pat in (r"g_sClubId\s*=\s*[\"'](\d+)[\"']",
                r"\"cafeId\"\s*:\s*\"?(\d+)",
                r"clubid=(\d+)",
                r"cafes/(\d+)/"):
        m = re.search(pat, text, re.I)
        if m:
            return m.group(1)
    return None


def parse_target(url: str, cookies: dict) -> dict:
    """URL 을 {kind, cafe_id, article_id, menu_id} 로 해석합니다."""
    url = url.strip().strip('"').strip("'")
    if not url.startswith("http"):
        url = "https://" + url.lstrip("/")

    m = RE_NEW_ARTICLE.search(url)
    if m:
        return {"kind": "article", "cafe_id": m.group(1), "article_id": m.group(2)}

    m = RE_NEW_MENU.search(url)
    if m:
        return {"kind": "menu", "cafe_id": m.group(1), "menu_id": m.group(2)}

    club = RE_Q_CLUB.search(url)
    art = RE_Q_ART.search(url)
    menu = RE_Q_MENU.search(url)
    if club and art:
        return {"kind": "article", "cafe_id": club.group(1), "article_id": art.group(1)}
    if club and menu:
        return {"kind": "menu", "cafe_id": club.group(1), "menu_id": menu.group(1)}

    m = RE_LEGACY_ARTICLE.search(url)
    if m and m.group(1) not in ("f-e", "ca-fe", "ArticleRead.nhn"):
        cafe_id = resolve_cafe_id(m.group(1), cookies)
        if cafe_id:
            return {"kind": "article", "cafe_id": cafe_id, "article_id": m.group(2)}

    m = RE_LEGACY_NAME.search(url)
    if m:
        cafe_id = resolve_cafe_id(m.group(1), cookies)
        if cafe_id and menu:
            return {"kind": "menu", "cafe_id": cafe_id, "menu_id": menu.group(1)}

    die(
        "URL 을 해석하지 못했습니다.\n"
        "    게시판 예) https://cafe.naver.com/f-e/cafes/16075980/menus/194?viewType=L\n"
        "    게시글 예) https://cafe.naver.com/f-e/cafes/16075980/articles/12345"
    )
    return {}  # unreachable


# --------------------------------------------------------------------------- #
# JSON 탐색 유틸 (API 스키마가 바뀌어도 견디도록 재귀 탐색)
# --------------------------------------------------------------------------- #


def dig(obj, *path):
    cur = obj
    for key in path:
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return None
    return cur


def deep_find_str(obj, keys, predicate=None, _depth=0):
    """중첩 구조 어디에 있든 keys 중 하나에 해당하는 첫 문자열 값을 찾습니다."""
    if _depth > 12:
        return None
    if isinstance(obj, dict):
        for key in keys:
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                if predicate is None or predicate(val):
                    return val
        for val in obj.values():
            found = deep_find_str(val, keys, predicate, _depth + 1)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = deep_find_str(item, keys, predicate, _depth + 1)
            if found is not None:
                return found
    return None


def deep_collect_articles(obj, out: list, seen: set, row_type=None, _depth=0) -> None:
    """articleId + subject 를 가진 모든 노드를 순서대로 수집합니다.

    새 목록 API 는 {"type":"ARTICLE","item":{"articleId":..,"subject":..,"hasMovie":..}}
    구조라서 바깥 노드의 type 을 안쪽으로 물려줍니다.
    """
    if _depth > 12:
        return
    if isinstance(obj, dict):
        kind = obj.get("type") if isinstance(obj.get("type"), str) else row_type
        aid = obj.get("articleId", obj.get("articleid"))
        subj = obj.get("subject", obj.get("articleTitle"))
        if aid is not None and isinstance(subj, str):
            sid = str(aid)
            if sid.isdigit() and sid not in seen:
                seen.add(sid)
                notice = bool(obj.get("noticeArticle") or obj.get("notice"))
                if kind and kind != "ARTICLE":
                    notice = True          # REQUIRED_NOTICE / MENU_NOTICE / UP_ARTICLE ...
                out.append({
                    "article_id": sid,
                    "subject": html_mod.unescape(subj).strip(),
                    "notice": notice,
                    # 목록 단계에서 영상 유무를 알 수 있으면 본문 조회를 건너뛸 수 있습니다.
                    "has_movie": bool(obj.get("hasMovie") or obj.get("attachMovie")),
                    "movie_flag_known": ("hasMovie" in obj or "attachMovie" in obj),
                })
        for val in obj.values():
            deep_collect_articles(val, out, seen, kind, _depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            deep_collect_articles(item, out, seen, row_type, _depth + 1)


# --------------------------------------------------------------------------- #
# 카페 API
# --------------------------------------------------------------------------- #


# 오류 코드 → 사람이 읽을 수 있는 설명 (reason 이 비어 있을 때만 사용)
ERROR_HINTS = {
    "0004": "로그인하지 않았습니다 (쿠키 만료).",
    "0005": "비공개 카페입니다. 가입 후 이용하세요.",
    "0010": "카페 번호(cafeId)가 잘못되었습니다. 숫자 ID 가 필요합니다.",
    "2000": "게시판 등급 제한으로 읽을 수 없습니다.",
    "3009": "읽기 권한이 없는 회원 등급입니다.",
    "3030": "실명 확인이 필요한 게시글입니다.",
    "3031": "성인 인증이 필요한 게시글입니다.",
    "3032": "성인 인증이 필요한 게시글입니다.",
    "4003": "삭제되었거나 존재하지 않는 게시글입니다.",
    "4004": "읽기 권한이 없는 회원 등급입니다.",
    "4005": "게시판 등급 제한으로 읽을 수 없습니다.",
    "4007": "접근이 제한된 게시판입니다.",
    "11005": "게시판(menuId)을 찾을 수 없습니다.",
    "45005": "숨김 처리된 게시판입니다.",
}


def naver_error(payload) -> str | None:
    """네이버 API 응답 안의 오류 사유를 뽑아냅니다.

    실제 응답 예)
      401 {"result":{"errorCode":"0004","reason":"로그인하지 않았습니다.", ...}}
      404 {"result":{"errorCode":"4003","reason":"삭제되었거나 존재하지 않는 게시글입니다.", ...}}
    """
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = json.loads(payload.decode("utf-8", "replace"))
        except Exception:
            return None
    if not isinstance(payload, dict):
        return None
    code = dig(payload, "result", "errorCode") or payload.get("errorCode")
    reason = (dig(payload, "result", "reason")
              or dig(payload, "message", "error", "msg")
              or payload.get("reason"))
    if reason:
        return f"{reason} (errorCode={code})" if code else str(reason)
    if code:
        hint = ERROR_HINTS.get(str(code))
        return f"{hint} (errorCode={code})" if hint else f"errorCode={code}"
    return None


AUTH_HELP = ("\n    → 브라우저에서 카페에 다시 로그인한 뒤 쿠키(NID_AUT/NID_JST/NID_SES)를"
             "\n      새로 복사해 cookies.txt 를 갱신해 주세요.")


def _auth_hint(err: Exception) -> str:
    if isinstance(err, HttpError):
        reason = naver_error(err.body)
        if err.status in (401, 403):
            base = reason or "로그인 쿠키가 만료되었거나 해당 게시판 접근 권한이 없습니다."
            return base + AUTH_HELP
        if reason:
            return reason
    return str(err)


def fetch_article(cafe_id: str, article_id: str, cookies: dict) -> dict:
    """{'subject':..., 'html':...} 반환."""
    last_err: Exception | None = None
    for tmpl in ARTICLE_ENDPOINTS:
        url = tmpl.format(cafe=cafe_id, art=article_id)
        try:
            data = http_get_json(
                url, cookies=cookies,
                referer=f"https://cafe.naver.com/f-e/cafes/{cafe_id}/articles/{article_id}",
                extra={"X-Cafe-Product": "pc"},
            )
        except HttpError as e:
            last_err = e
            # 인증 실패는 다른 버전의 API 를 시도해도 동일하므로 바로 중단
            if e.status in (401, 403):
                break
            continue
        except Exception as e:
            last_err = e
            continue

        err = naver_error(data)
        if err:
            last_err = RuntimeError(err)
            continue

        subject = (dig(data, "result", "article", "subject")
                   or deep_find_str(data, ("subject", "articleTitle")))
        body_html = (dig(data, "result", "article", "contentHtml")
                     or dig(data, "result", "article", "content")
                     or deep_find_str(data, ("contentHtml",))
                     or deep_find_str(data, ("content",), predicate=lambda s: "<" in s))
        if subject or body_html:
            return {
                "subject": html_mod.unescape(subject or "").strip(),
                "html": body_html or "",
            }
        last_err = RuntimeError("본문을 찾지 못했습니다 (응답 형식 변경?)")

    if isinstance(last_err, HttpError) and last_err.status in (401, 403):
        raise AuthError(_auth_hint(last_err))
    raise RuntimeError(_auth_hint(last_err) if last_err else "게시글을 읽지 못했습니다.")


def fetch_article_list(cafe_id: str, menu_id: str, cookies: dict,
                       pages: int, per_page: int) -> list:
    """게시판의 글 목록을 [{'article_id','subject','notice'}] 로 반환."""
    referer = f"https://cafe.naver.com/f-e/cafes/{cafe_id}/menus/{menu_id}?viewType=L"
    working_tmpl: str | None = None
    articles: list = []
    seen: set = set()
    last_err: Exception | None = None

    for page in range(1, pages + 1):
        templates = (working_tmpl,) if working_tmpl else LIST_ENDPOINTS
        page_items: list = []
        for tmpl in templates:
            url = tmpl.format(cafe=cafe_id, menu=menu_id, page=page, size=per_page)
            try:
                data = http_get_json(url, cookies=cookies, referer=referer,
                                     extra={"X-Cafe-Product": "pc"})
            except Exception as e:
                last_err = e
                continue
            found: list = []
            deep_collect_articles(data, found, seen)
            if found:
                working_tmpl = tmpl
                page_items = found
                break
            # 응답은 정상인데 글이 없다 -> 다음 후보 엔드포인트를 계속 시도

        if not page_items:
            break
        articles.extend(page_items)
        info(f"목록 {page}페이지: {len(page_items)}개 (누적 {len(articles)}개)")
        if len(page_items) < per_page:
            break
        time.sleep(0.4)

    if not articles and last_err is not None:
        if isinstance(last_err, HttpError) and last_err.status in (401, 403):
            raise AuthError(_auth_hint(last_err))
        raise RuntimeError(_auth_hint(last_err))
    return articles


# --------------------------------------------------------------------------- #
# 본문 HTML → 동영상 (vid, inkey)
# --------------------------------------------------------------------------- #

# 스마트에디터는 data-module / data-module-v2 두 가지 속성에 같은 JSON 을 넣습니다.
RE_DATA_MODULE = re.compile(r"data-module(?:-v2)?\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", re.I)
# vid 와 inkey 는 가까이 붙어 있으므로 근접 매칭으로 짝을 맞춥니다.
RE_PAIR = re.compile(
    r"[\"']?vid[\"']?\s*[:=]\s*[\"']([A-Za-z0-9_\-]{8,})[\"']"
    r"(?:(?![\"']?vid[\"']?\s*[:=])[\s\S]){0,500}?"
    r"[\"']?in_?[kK]ey[\"']?\s*[:=]\s*[\"']([^\"']{8,})[\"']"
)
RE_ATTR_VID = re.compile(r"data-vid\s*=\s*[\"']([^\"']+)[\"']", re.I)
RE_ATTR_INKEY = re.compile(r"data-in-?key\s*=\s*[\"']([^\"']+)[\"']", re.I)
# 옛날 글에 남아 있는 임베드 형식 (속성 순서가 바뀌기도 합니다)
RE_LEGACY_SPAN = re.compile(r"<span[^>]*_naverVideo[^>]*>", re.I)
RE_ANY_VID = re.compile(r"\bvid\s*=\s*[\"']([^\"']+)[\"']", re.I)
RE_ANY_KEY = re.compile(r"\b(?:in_?key|key)\s*=\s*[\"']([^\"']+)[\"']", re.I)


def _pick(d: dict, *names):
    for name in names:
        val = d.get(name)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def extract_videos(body_html: str) -> list:
    """본문 HTML 에서 [{'vid':..., 'inkey':...}] 목록을 뽑습니다."""
    found: list = []
    seen: set = set()

    def add(vid, inkey):
        # 같은 영상이 data-module 과 data-module-v2 양쪽에 들어 있으므로 vid 로 중복 제거
        if vid and inkey and vid not in seen:
            seen.add(vid)
            found.append({"vid": vid, "inkey": inkey})

    # 1) 스마트에디터 모듈 데이터 (가장 정확)
    for m in RE_DATA_MODULE.finditer(body_html):
        raw = m.group(1) if m.group(1) is not None else m.group(2)
        if not raw or "video" not in raw.lower():
            continue
        try:
            mod = json.loads(html_mod.unescape(raw))
        except Exception:
            continue
        data = mod.get("data") if isinstance(mod, dict) else None
        if not isinstance(data, dict):
            continue
        add(_pick(data, "vid", "videoId", "videoid"),
            _pick(data, "inkey", "inKey", "in_key", "key"))

    # 2) 속성 형태 (data-vid / data-inkey)
    for vid, inkey in zip(RE_ATTR_VID.findall(body_html), RE_ATTR_INKEY.findall(body_html)):
        add(vid, inkey)

    # 3) 옛날 임베드 <span class="_naverVideo" vid=".." key="..">
    for tag in RE_LEGACY_SPAN.findall(body_html):
        vid = RE_ANY_VID.search(tag)
        key = RE_ANY_KEY.search(tag)
        if vid and key:
            add(vid.group(1), key.group(1))

    # 4) 최후의 보루: HTML 전체에서 vid/inkey 를 근접 매칭
    if not found:
        for vid, inkey in RE_PAIR.findall(html_mod.unescape(body_html)):
            add(vid, inkey)

    return found


# --------------------------------------------------------------------------- #
# 재생정보 API → 실제 mp4 주소
# --------------------------------------------------------------------------- #


class DrmError(RuntimeError):
    """DRM/유료 보호 영상 - 직접 받을 수 있는 트랙이 없음."""


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _quality_from_label(text: str) -> int:
    """'1080P_01', '720p', 'PD_1080P_1920_8000_192' → 1080 / 720 / 1080"""
    m = re.search(r"(\d{3,4})\s*[pP]", text or "")
    return int(m.group(1)) if m else 0


def _tag(el) -> str:
    return el.tag.split("}")[-1] if isinstance(el.tag, str) else ""


def _candidate(quality: int, url: str, label: str, size: int = 0,
               progressive: bool = True) -> dict:
    return {"quality": quality, "url": url, "label": label or f"{quality}p",
            "size": size, "progressive": progressive}


def parse_play_json(data: dict) -> list:
    """예전 rmcnmv JSON 응답 → 후보 목록."""
    out: list = []
    for video in (dig(data, "videos", "list") or []):
        if not isinstance(video, dict):
            continue
        url = video.get("source")
        if not isinstance(url, str) or not url.startswith("http"):
            continue
        enc = video.get("encodingOption") or {}
        label = str(enc.get("id") or enc.get("name") or "")
        # 세로 영상은 width=480 height=854 처럼 나오므로 height 로 정렬하면 안 됩니다.
        # 화질 숫자는 항상 "짧은 변" 기준입니다.
        quality = _quality_from_label(label)
        if not quality:
            width, height = _int(enc.get("width")), _int(enc.get("height"))
            quality = min(width, height) if width and height else (height or width)
        out.append(_candidate(quality, url, label, _int(video.get("size"))))
    return out


def _mpd_representations(root, manifest_url: str) -> tuple:
    """(후보목록, DRM여부)"""
    prefix = manifest_url
    for el in root:
        if _tag(el) == "BaseURL" and (el.text or "").strip():
            prefix = urllib.parse.urljoin(manifest_url, el.text.strip())
            break

    drm = any(_tag(el) == "ContentProtection" for el in root.iter())

    out: list = []
    for rep in root.iter():
        if _tag(rep) != "Representation":
            continue
        rep_id = rep.get("id") or ""
        mime = rep.get("mimeType") or ""

        base = ""
        for child in rep:
            if _tag(child) == "BaseURL" and (child.text or "").strip():
                base = child.text.strip()
                break
        if not base:
            continue                      # 분할 세그먼트 또는 DRM 트랙
        if base.endswith("/") or base.rstrip("/").endswith("/hls"):
            continue                      # 세그먼트 디렉터리 (HLS)
        if "mp2t" in mime:
            continue                      # HLS(TS) 어댑테이션셋

        # PD_ = Progressive Download. 영상+음성이 합쳐진 완성된 mp4 → ffmpeg 불필요.
        progressive = rep_id.upper().startswith("PD")
        if not progressive:
            codecs = rep.get("codecs") or ""
            progressive = "avc" in codecs and "mp4a" in codecs
        if not progressive and any(_tag(c).startswith("Segment") for c in rep):
            continue

        quality = 0
        for el in rep.iter():
            if _tag(el) == "Label" and el.get("kind") == "resolution":
                quality = _int(re.sub(r"[^0-9]", "", el.text or ""))
                break
        if not quality:
            m = re.match(r"PD_(\d+)P", rep_id, re.I)
            quality = int(m.group(1)) if m else _quality_from_label(rep_id)
        if not quality:
            width, height = _int(rep.get("width")), _int(rep.get("height"))
            quality = min(width, height) if width and height else (height or width)

        url = base if base.startswith("http") else urllib.parse.urljoin(prefix, base)
        out.append(_candidate(quality, url, rep_id, 0, progressive))
    return out, drm


def parse_play_mpd(xml_text: str, manifest_url: str) -> tuple:
    """네이버 DASH 매니페스트(XML) → (후보목록, DRM여부).

    네임스페이스가 urn:naver:vod:2015 / :2020 등으로 바뀌어도 되도록
    태그 이름의 로컬 파트만 보고 판단합니다.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return [], False
    return _mpd_representations(root, manifest_url)


def parse_play_mpd_json(data: dict, manifest_url: str) -> tuple:
    """Accept 헤더가 무시돼 MPD 가 JSON 으로 온 경우: {"$version":.., "MPD":[..]}"""
    out: list = []
    drm = False

    def walk(node):
        nonlocal drm
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        if "ContentProtection" in node:
            drm = True
        rep_id = node.get("id")
        base = node.get("BaseURL")
        if isinstance(base, list):
            base = next((b for b in base if isinstance(b, str)), None)
        if isinstance(base, dict):
            base = base.get("#text") or base.get("value")
        if isinstance(rep_id, str) and isinstance(base, str) and base.strip():
            base = base.strip()
            if not (base.endswith("/") or base.rstrip("/").endswith("/hls")):
                progressive = rep_id.upper().startswith("PD")
                m = re.match(r"PD_(\d+)P", rep_id, re.I)
                quality = int(m.group(1)) if m else _quality_from_label(rep_id)
                if not quality:
                    width, height = _int(node.get("width")), _int(node.get("height"))
                    quality = min(width, height) if width and height else (height or width)
                url = base if base.startswith("http") else urllib.parse.urljoin(manifest_url, base)
                out.append(_candidate(quality, url, rep_id, 0, progressive))
        for value in node.values():
            walk(value)

    walk(data.get("MPD", data))
    return out, drm


def play_error(body) -> str | None:
    """재생정보 API 의 오류 본문에서 사람이 읽을 메시지를 뽑습니다.

    실제 응답 예)
      neonplayer 401: <Error code="ACCESS_DENIED"><message>호출 정보가 잘못되어 ...</message></Error>
      rmcnmv    200: {"errorCode":"INVALID_VIDEOID","errorMessage":"호출 정보가 잘못되어 ..."}
    """
    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8", "replace")
    text = (body or "").strip()
    if not text:
        return None

    if text[:1] == "<":
        msg = re.search(r"<message>(.*?)</message>", text, re.S)
        code = re.search(r"<Error[^>]*code=[\"']([^\"']+)", text)
        msg = msg.group(1).strip() if msg else ""
        code = code.group(1) if code else ""
        if msg and code:
            return f"{msg} ({code})"
        return msg or code or None

    try:
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    msg = (data.get("errorMessage") or data.get("message")
           or dig(data, "error", "message") or dig(data, "error", "msg"))
    code = data.get("errorCode") or dig(data, "error", "code")
    if msg and code:
        return f"{msg} ({code})"
    return msg or (str(code) if code else None)


def fetch_play_info(vid: str, inkey: str, cookies: dict = None) -> list:
    """재생정보 API 를 호출해 다운로드 후보 목록을 만듭니다.

    neonplayer(DASH MPD) → 예전 rmcnmv(JSON) 순으로 시도하며,
    응답 형식은 URL 이 아니라 본문의 첫 글자로 판별합니다.
    """
    errors: list = []
    drm_seen = False

    for tmpl, accept in PLAY_ENDPOINTS:
        base_url = tmpl.format(vid=urllib.parse.quote(vid, safe=""))
        for extra_params in ({}, PLAY_PARAMS_EXTRA):
            params = dict(extra_params, key=inkey)
            url = base_url + "?" + urllib.parse.urlencode(params)
            try:
                body, _ctype, final_url = http_get(
                    url, cookies=cookies, referer=PLAY_REFERER,
                    extra={"Accept": accept}, retries=2)
            except HttpError as e:
                errors.append(play_error(e.body) or f"HTTP {e.status}")
                if e.status == 400 and not extra_params:
                    continue            # 보조 파라미터를 붙여 한 번 더
                break
            except Exception as e:
                errors.append(str(e))
                break

            text = body.decode("utf-8", "replace").lstrip("﻿").strip()
            if not text:
                break

            candidates: list = []
            if text[:1] == "<":
                candidates, drm = parse_play_mpd(text, final_url)
                drm_seen = drm_seen or drm
            elif text[:1] in "{[":
                try:
                    data = json.loads(text)
                except Exception:
                    data = None
                if isinstance(data, dict):
                    if data.get("errorCode") or dig(data, "error", "code"):
                        errors.append(play_error(text) or "재생정보 오류")
                        break
                    if "MPD" in data:
                        candidates, drm = parse_play_mpd_json(data, final_url)
                        drm_seen = drm_seen or drm
                    else:
                        provider = str(dig(data, "meta", "provider", "name") or "")
                        if provider.lower() == "drm":
                            drm_seen = True
                        candidates = parse_play_json(data)
                        if not candidates:
                            # JSON 안에 매니페스트 주소만 있는 경우 한 번 더 따라갑니다.
                            for stream in (data.get("streams") or []):
                                src = (stream or {}).get("source")
                                if isinstance(src, str) and ".mpd" in src:
                                    try:
                                        mbody, _ct, mfinal = http_get(
                                            src, cookies=cookies,
                                            referer=PLAY_REFERER, retries=2)
                                        candidates, drm = parse_play_mpd(
                                            mbody.decode("utf-8", "replace"), mfinal)
                                        drm_seen = drm_seen or drm
                                    except Exception as e:
                                        errors.append(str(e))
                                    if candidates:
                                        break

            if candidates:
                return candidates
            break                       # 응답은 받았으나 쓸 트랙이 없음 → 다음 엔드포인트

    if drm_seen:
        raise DrmError("DRM/유료 보호 영상이라 직접 받을 수 있는 트랙(PD)이 없습니다.")
    if errors:
        raise RuntimeError(f"재생정보를 가져오지 못했습니다 ({errors[0]}). "
                           "게시글을 다시 조회하거나 쿠키를 갱신해 보세요.")
    raise RuntimeError("재생 가능한 화질을 찾지 못했습니다 (유료/DRM 영상일 수 있습니다).")


def choose_quality(candidates: list, wanted: str):
    """wanted: '1080' | '720' | ... | 'best' | 'worst' → (선택, 전체목록)"""
    usable = [c for c in candidates if c.get("progressive")] or candidates
    if not usable:
        return None, []
    usable = sorted(usable, key=lambda c: (c.get("quality") or 0), reverse=True)

    if wanted == "best":
        return usable[0], usable
    if wanted == "worst":
        return usable[-1], usable

    target = _int(re.sub(r"[^0-9]", "", wanted), 1080) or 1080

    exact = [c for c in usable if c.get("quality") == target]
    if exact:
        return exact[0], usable
    lower = [c for c in usable if 0 < (c.get("quality") or 0) < target]
    if lower:
        return lower[0], usable          # 요청보다 낮은 것 중 가장 높은 화질
    return usable[0], usable             # 전부 더 높으면 가장 높은 것


# --------------------------------------------------------------------------- #
# 파일 이름
# --------------------------------------------------------------------------- #

BRACKET_RE = re.compile(r"[\[\uFF3B【]([^\]\uFF3D】]{1,120})[\]\uFF3D】]")
WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str) -> str:
    name = html_mod.unescape(name or "").strip()
    name = re.sub(r"[\\/:*?\"<>|]", "-", name)      # 윈도우 금지 문자
    name = re.sub(r"[\x00-\x1f]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = name.strip(". ")                          # 윈도우: 끝의 점/공백 금지
    if name.upper() in WIN_RESERVED:
        name = "_" + name
    if len(name) > 120:
        name = name[:120].rstrip()
    return name or "video"


def title_to_basename(subject: str, use_full: bool = False) -> str:
    if not use_full:
        m = BRACKET_RE.search(subject or "")
        if m:
            return sanitize_filename(m.group(1))
    return sanitize_filename(subject)


# --------------------------------------------------------------------------- #
# 다운로드
# --------------------------------------------------------------------------- #


def _progress(done: int, total: int, started: float) -> None:
    if not sys.stdout.isatty():
        return
    elapsed = max(time.time() - started, 0.001)
    speed = done / elapsed
    if total:
        pct = done * 100.0 / total
        bar_len = 24
        filled = int(bar_len * done / total)
        bar = "#" * filled + "." * (bar_len - filled)
        line = f"    [{bar}] {pct:5.1f}%  {human(done)}/{human(total)}  {human(speed)}/s"
    else:
        line = f"    {human(done)}  {human(speed)}/s"
    sys.stdout.write("\r" + line + " " * 6)
    sys.stdout.flush()


def download_file(url: str, dest: str, retries: int = 4, refresh=None,
                  show_progress: bool = True, say=None) -> int:
    """이어받기 지원 다운로드. 최종 파일 크기를 반환.

    refresh:       서명이 만료돼 400/403 이 나올 때 새 주소를 만들어 주는 함수(선택).
                   네이버 CDN 서명(_lsu_sa_)은 약 8시간 뒤 만료됩니다.
    show_progress: 진행률 바 표시 여부 (병렬 모드에서는 줄이 겹치므로 끕니다).
    say:           메시지 출력 함수 (병렬 모드에서 글 번호를 붙이기 위해).
    """
    say = say or (lambda msg: warn(msg))
    part = dest + ".part"
    os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
    last_err: Exception | None = None
    refreshed = 0

    attempt = 0
    while attempt < retries:
        attempt += 1
        pos = os.path.getsize(part) if os.path.exists(part) else 0
        # CDN 은 URL 안의 서명만으로 인증합니다. 쿠키는 절대 보내지 않습니다.
        headers = {
            "User-Agent": UA,
            "Accept": "*/*",
            "Accept-Encoding": "identity",
            "Connection": "close",
        }
        if pos:
            headers["Range"] = f"bytes={pos}-"

        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                status = getattr(resp, "status", resp.getcode())
                if pos and status != 206:
                    pos = 0                      # 서버가 이어받기를 거부 → 처음부터
                total = 0
                clen = resp.headers.get("Content-Length")
                if clen and clen.isdigit():
                    total = int(clen) + (pos if status == 206 else 0)
                crange = resp.headers.get("Content-Range") or ""
                m = re.search(r"/(\d+)\s*$", crange)
                if m:
                    total = int(m.group(1))

                mode = "ab" if pos else "wb"
                started = time.time()
                done = pos
                last_draw = 0.0
                with open(part, mode) as f:
                    while True:
                        if ABORT.is_set():
                            raise KeyboardInterrupt
                        chunk = resp.read(CHUNK)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        now = time.time()
                        if show_progress and now - last_draw > 0.2:
                            _progress(done, total, started)
                            last_draw = now
                if show_progress:
                    _progress(done, total, started)
                    if sys.stdout.isatty():
                        sys.stdout.write("\n")
                        sys.stdout.flush()

            if total and os.path.getsize(part) < total:
                raise IOError(f"전송이 중간에 끊겼습니다 ({human(os.path.getsize(part))}/{human(total)})")

            os.replace(part, dest)
            return os.path.getsize(dest)

        except KeyboardInterrupt:
            raise
        except urllib.error.HTTPError as e:
            last_err = e
            # 서명 만료 → 재생정보를 다시 받아 이어서 진행
            if e.code in (400, 403, 410) and refresh and refreshed < 3:
                refreshed += 1
                attempt -= 1
                try:
                    fresh = refresh()
                except Exception:
                    fresh = None
                if fresh:
                    say("다운로드 링크가 만료되어 새로 발급받았습니다.")
                    url = fresh
                    continue
            if attempt < retries:
                say(f"다운로드 실패({attempt}/{retries}): HTTP {e.code} → 재시도합니다.")
                time.sleep(2 * attempt)
        except Exception as e:
            last_err = e
            if attempt < retries:
                say(f"다운로드 실패({attempt}/{retries}): {e} → 재시도합니다.")
                time.sleep(2 * attempt)

    raise RuntimeError(f"다운로드 실패: {last_err}")


# --------------------------------------------------------------------------- #
# 메인 처리
# --------------------------------------------------------------------------- #


class Stats:
    """여러 스레드가 함께 증가시키므로 락으로 보호합니다."""

    FIELDS = ("ok", "skipped", "failed", "no_video", "listed")

    def __init__(self):
        self._lock = threading.Lock()
        for name in self.FIELDS:
            setattr(self, name, 0)

    def bump(self, field: str) -> None:
        with self._lock:
            setattr(self, field, getattr(self, field) + 1)


# 서로 다른 글의 제목이 같으면 같은 파일명이 나옵니다.
# 두 스레드가 동시에 같은 .part 에 쓰지 않도록 미리 예약합니다.
_CLAIM_LOCK = threading.Lock()
_CLAIMED: set = set()


def claim_target(path: str) -> bool:
    with _CLAIM_LOCK:
        if path in _CLAIMED:
            return False
        _CLAIMED.add(path)
        return True


def release_target(path: str) -> None:
    with _CLAIM_LOCK:
        _CLAIMED.discard(path)


def process_article(cafe_id: str, article_id: str, subject_hint: str,
                    cookies: dict, args, stats: Stats,
                    tag: str = "", show_progress: bool = True) -> None:
    """tag 가 있으면 병렬 모드 - 모든 줄 앞에 [n/N] 을 붙이고 진행률 바는 끕니다."""
    indent = "" if tag else "    "

    def say(msg: str = "") -> None:
        with PRINT_LOCK:
            print((tag + msg) if msg else "", flush=True)

    if not tag:
        say()                      # 순차 모드에서는 글마다 빈 줄로 구분

    try:
        article = fetch_article(cafe_id, article_id, cookies)
    except AuthError:
        raise
    except Exception as e:
        say(f"[!] [{article_id}] 게시글 읽기 실패: {e}")
        stats.bump("failed")
        return

    subject = article["subject"] or subject_hint or f"article_{article_id}"
    videos = extract_videos(article["html"])

    say(("── " if not tag else "") + f"[{article_id}] {subject}")
    if not videos:
        say(f"{indent}동영상이 없습니다. 건너뜁니다.")
        stats.bump("no_video")
        return

    base = title_to_basename(subject, args.full_title)
    if args.prefix_id:
        base = f"{article_id}_{base}"

    for idx, video in enumerate(videos, start=1):
        name = base if len(videos) == 1 else f"{base}_{idx}"

        def resolve(_v=video):
            """재생정보를 (다시) 받아 화질 후보를 고릅니다."""
            return choose_quality(
                fetch_play_info(_v["vid"], _v["inkey"], cookies), args.quality)

        if ABORT.is_set():
            return

        try:
            chosen, usable = resolve()
        except DrmError as e:
            say(f"{indent}[!] {e}")
            stats.bump("failed")
            continue
        except Exception as e:
            say(f"{indent}[!] 재생정보 실패: {e}")
            stats.bump("failed")
            continue

        if not chosen:
            say(f"{indent}[!] 다운로드 가능한 화질이 없습니다. (DRM 이거나 분할 스트림만 존재)")
            stats.bump("failed")
            continue

        avail = ", ".join(f"{c['quality']}p" for c in usable if c.get("quality")) or "알 수 없음"
        want = None if args.quality in ("best", "worst") else re.sub(r"[^0-9]", "", args.quality)
        if want and str(chosen.get("quality")) != want:
            if args.strict_quality:
                say(f"{indent}[!] {want}p 화질이 없어 건너뜁니다. (가능: {avail})")
                stats.bump("skipped")
                continue
            say(f"{indent}[!] {want}p 가 없어 {chosen.get('quality')}p 로 받습니다. (가능: {avail})")

        target = os.path.join(args.outdir, sanitize_filename(name) + ".mp4")
        if os.path.exists(target) and not args.overwrite:
            say(f"{indent}이미 있음 → 건너뜀: {os.path.basename(target)}")
            stats.bump("skipped")
            continue

        size_hint = f" ({human(chosen['size'])})" if chosen.get("size") else ""
        say(f"{indent}{chosen.get('quality') or '?'}p [{chosen['label']}]{size_hint}"
            f" → {os.path.basename(target)}")

        if args.list_only:
            stats.bump("listed")
            continue

        if not claim_target(target):
            say(f"{indent}[!] 같은 이름을 다른 글이 받는 중이라 건너뜁니다"
                f": {os.path.basename(target)}  (--prefix-id 를 쓰면 겹치지 않습니다)")
            stats.bump("skipped")
            continue

        def refresh():
            """CDN 서명이 만료됐을 때 새 주소를 발급."""
            again, _ = resolve()
            return again["url"] if again else None

        began = time.time()
        try:
            written = download_file(
                chosen["url"], target, refresh=refresh,
                show_progress=show_progress,
                say=lambda m: say(f"{indent}[!] {m}"))
            say(f"{indent}완료: {os.path.basename(target)}"
                f"  {human(written)}  {time.time() - began:.0f}초")
            stats.bump("ok")
        except KeyboardInterrupt:
            raise
        except Exception as e:
            say(f"{indent}[!] {e}")
            stats.bump("failed")
        finally:
            release_target(target)

        if args.sleep > 0 and not ABORT.is_set():
            time.sleep(args.sleep)


def run_articles(cafe_id: str, articles: list, cookies: dict, args,
                 stats: Stats, jobs: int) -> None:
    """게시글 목록을 순차 또는 병렬로 처리합니다."""
    total = len(articles)
    width = len(str(total))

    if jobs <= 1:
        for article in articles:
            process_article(cafe_id, article["article_id"], article["subject"],
                            cookies, args, stats)
        return

    fatal: list = []

    def worker(index: int, article: dict) -> None:
        if ABORT.is_set():
            return
        try:
            process_article(cafe_id, article["article_id"], article["subject"],
                            cookies, args, stats,
                            tag=f"[{index:>{width}}/{total}] ", show_progress=False)
        except AuthError as e:
            # 쿠키가 죽었으면 나머지를 계속 시도해도 의미가 없습니다.
            ABORT.set()
            fatal.append(e)
        except KeyboardInterrupt:
            ABORT.set()

    executor = ThreadPoolExecutor(max_workers=jobs)
    futures = [executor.submit(worker, i, a) for i, a in enumerate(articles, start=1)]
    try:
        wait(futures, return_when=FIRST_EXCEPTION)
    except KeyboardInterrupt:
        ABORT.set()
        raise
    finally:
        if ABORT.is_set():
            for future in futures:
                future.cancel()
        executor.shutdown(wait=True)

    if fatal:
        raise fatal[0]


def interactive_url() -> str:
    log("")
    log("=" * 68)
    log(" 네이버 카페 동영상 다운로더 v" + __version__)
    log("=" * 68)
    log(" 다운로드할 주소를 붙여넣고 Enter 를 누르세요.")
    log("   · 게시판 전체: https://cafe.naver.com/f-e/cafes/16075980/menus/194?viewType=L")
    log("   · 게시글 하나: https://cafe.naver.com/f-e/cafes/16075980/articles/12345")
    log("-" * 68)
    try:
        url = input("  URL = ").strip()
    except (EOFError, KeyboardInterrupt):
        log("")
        die("입력이 취소되었습니다.")
    if not url:
        die("URL 이 입력되지 않았습니다.")
    return url


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="naver_cafe_dl.py",
        description="네이버 카페 동영상 다운로더 (기본 1080p, 파일명은 제목의 [대괄호] 안 내용)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("url", nargs="?", help="게시글 URL 또는 게시판(메뉴) URL")
    p.add_argument("-o", "--outdir", default=DEFAULT_OUTDIR, help="저장 폴더 (기본: downloads)")
    p.add_argument("-q", "--quality", default="1080",
                   help="원하는 화질: 1080 / 720 / 480 / best / worst (기본: 1080)")
    p.add_argument("-c", "--cookies", help="쿠키 파일 경로 (기본: 스크립트 옆 cookies.txt)")
    p.add_argument("-j", "--jobs", type=int, default=3,
                   help="동시에 받을 개수 1~8 (기본: 3). 1 이면 진행률 바가 나옵니다")
    p.add_argument("--pages", type=int, default=10, help="게시판 모드에서 읽을 목록 페이지 수 (기본: 10)")
    p.add_argument("--per-page", type=int, default=50,
                   help="목록 페이지당 글 수 (최대 50, 기본: 50)")
    p.add_argument("--limit", type=int, default=0, help="최대 처리할 게시글 수 (0 = 제한 없음)")
    p.add_argument("--strict-quality", action="store_true",
                   help="요청한 화질이 없으면 낮은 화질로 받지 않고 건너뛰기")
    p.add_argument("--all-articles", action="store_true",
                   help="목록의 '동영상 있음' 표시를 무시하고 모든 글을 열어보기")
    p.add_argument("--oldest-first", action="store_true", help="오래된 글부터 처리")
    p.add_argument("--skip-notice", action="store_true", help="공지글 건너뛰기")
    p.add_argument("--full-title", action="store_true",
                   help="대괄호 대신 제목 전체를 파일명으로 사용")
    p.add_argument("--prefix-id", action="store_true", help="파일명 앞에 게시글 번호 붙이기")
    p.add_argument("--overwrite", action="store_true", help="이미 있는 파일도 다시 받기")
    p.add_argument("--list", dest="list_only", action="store_true",
                   help="다운로드 없이 목록/화질만 확인")
    p.add_argument("--sleep", type=float, default=1.0, help="영상 사이 대기 초 (기본: 1.0)")
    p.add_argument("--version", action="version", version=__version__)
    return p


def main(argv=None) -> int:
    init_console()
    args = build_parser().parse_args(argv)
    args.quality = (args.quality or "1080").strip().lower()
    args.per_page = max(1, min(args.per_page, 50))   # 목록 API 상한이 50 (초과 시 15로 떨어짐)
    args.jobs = max(1, min(args.jobs, 8))            # 서버에 무리가 가지 않도록 상한

    url = args.url or interactive_url()
    cookies = load_cookies(args.cookies)
    target = parse_target(url, cookies)

    os.makedirs(args.outdir, exist_ok=True)
    info(f"저장 폴더: {os.path.abspath(args.outdir)}")

    stats = Stats()
    started = time.time()

    try:
        if target["kind"] == "article":
            process_article(target["cafe_id"], target["article_id"], "", cookies, args, stats)
        else:
            info(f"게시판 목록을 읽는 중... (카페 {target['cafe_id']} / 메뉴 {target['menu_id']})")
            articles = fetch_article_list(
                target["cafe_id"], target["menu_id"], cookies, args.pages, args.per_page)
            if args.skip_notice:
                articles = [a for a in articles if not a["notice"]]
            # 목록이 '동영상 있음'을 알려주면 그 글만 열어봅니다 (본문 조회 횟수를 크게 줄임)
            if not args.all_articles and any(a["movie_flag_known"] for a in articles):
                with_movie = [a for a in articles if a["has_movie"]]
                if with_movie:
                    info(f"동영상이 있는 글만 처리합니다: {len(with_movie)}/{len(articles)}개"
                         " (전부 확인하려면 --all-articles)")
                    articles = with_movie
            if args.oldest_first:
                articles = list(reversed(articles))
            if args.limit > 0:
                articles = articles[: args.limit]
            if not articles:
                die("게시글을 찾지 못했습니다. 게시판 주소와 접근 권한을 확인해 주세요.")

            jobs = 1 if (args.list_only or len(articles) == 1) else args.jobs
            info(f"총 {len(articles)}개 게시글을 처리합니다."
                 + (f" (동시 {jobs}개)" if jobs > 1 else ""))
            run_articles(target["cafe_id"], articles, cookies, args, stats, jobs)
    except KeyboardInterrupt:
        log("")
        warn("사용자가 중단했습니다. (.part 파일이 남아 있으면 다시 실행 시 이어받습니다)")
    except SystemExit:
        raise
    except Exception as e:
        die(str(e))

    log("")
    log("=" * 68)
    if args.list_only:
        log(f" 확인 완료  영상 {stats.listed}개 / 실패 {stats.failed} / 영상없음 {stats.no_video}"
            f"   ({time.time() - started:.0f}초)")
        log(" --list 모드라 실제 다운로드는 하지 않았습니다. --list 를 빼고 다시 실행하세요.")
    else:
        log(f" 완료  성공 {stats.ok} / 건너뜀 {stats.skipped} / 실패 {stats.failed}"
            f" / 영상없음 {stats.no_video}   ({time.time() - started:.0f}초)")
    log("=" * 68)
    return 1 if stats.failed and not (stats.ok or stats.listed) else 0


if __name__ == "__main__":
    sys.exit(main())
