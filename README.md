# 네이버 카페 동영상 다운로더

네이버 카페 게시글에 올라온 동영상을 **1080p 원본 mp4** 로 내려받는 도구입니다.
파일 이름은 게시글 제목의 **대괄호 안 내용**을 그대로 사용합니다.

```
게시글 제목 : 7월 1일 [OT/Be동사1]
저장 파일   : downloads/OT-Be동사1.mp4
```

- 게시글 하나만 받거나, **게시판(메뉴) 전체를 한 번에** 받을 수 있습니다.
- 로그인 쿠키(`NID_AUT` / `NID_JST` / `NID_SES`)만 넣으면 알아서 받습니다.
- **설치할 패키지가 없습니다.** 파이썬 표준 라이브러리만 사용합니다. (ffmpeg 불필요)
- 중간에 끊겨도 **이어받기**가 되고, 이미 받은 파일은 자동으로 건너뜁니다.

---

## 1. 준비물

| | |
|---|---|
| 파이썬 | **Python 3.7 이상** — <https://www.python.org/downloads/> |
| 계정 | 해당 카페 게시판을 **읽을 수 있는 등급의 네이버 계정** |

> 윈도우에서 파이썬을 설치할 때, 설치 첫 화면의
> **`Add python.exe to PATH`** 체크박스를 반드시 켜 주세요.

---

## 2. 빠른 시작

### 윈도우

1. 이 폴더를 통째로 내려받습니다. (`Code` → `Download ZIP` → 압축 풀기)
2. **`download.bat`** 을 더블클릭합니다.
3. 처음 실행하면 쿠키 3개를 물어봅니다. → [3. 쿠키 얻는 법](#3-쿠키-얻는-법)
4. 이어서 주소를 물어봅니다. 게시판 주소나 게시글 주소를 붙여넣고 Enter.
5. `downloads` 폴더에 mp4 가 쌓입니다.

> 붙여넣기는 명령 프롬프트 창에서 **마우스 오른쪽 클릭** 또는 `Ctrl+V` 입니다.

### macOS / 리눅스

```bash
chmod +x download.sh      # 처음 한 번만
./download.sh
```

### 명령줄에서 바로

```bash
# 게시판 전체
python naver_cafe_dl.py "https://cafe.naver.com/f-e/cafes/16075980/menus/194?viewType=L"

# 게시글 하나
python naver_cafe_dl.py "https://cafe.naver.com/f-e/cafes/16075980/articles/407624"

# 다운로드 없이 어떤 글에 어떤 화질이 있는지 확인만
python naver_cafe_dl.py "<게시판URL>" --list

# 최근 5개만, 720p 로, 다른 폴더에
python naver_cafe_dl.py "<게시판URL>" --limit 5 --quality 720 -o "D:\영문법"
```

---

## 3. 쿠키 얻는 법

1. 크롬에서 <https://cafe.naver.com> 에 **로그인**합니다.
2. `F12` 를 눌러 개발자 도구를 엽니다.
3. 상단 **`Application`** 탭 → 왼쪽 `Storage` → **`Cookies`** → **`https://cafe.naver.com`** 클릭
4. 목록에서 아래 3개를 찾아 **Value** 를 복사합니다.

   | 이름 | 설명 |
   |---|---|
   | `NID_AUT` | 로그인 인증값 **(필수)** |
   | `NID_SES` | 세션값 **(필수)** |
   | `NID_JST` | 보조 토큰 (있으면 함께 넣어주세요) |

5. `download.bat` 이 물어볼 때 하나씩 붙여넣으면 `cookies.txt` 에 저장됩니다.

> **`document.cookie` 로는 안 보입니다.**
> `NID_AUT` 와 `NID_SES` 는 HttpOnly 쿠키라 콘솔에서는 조회되지 않습니다.
> 반드시 `Application` 탭에서 확인하세요.

### cookies.txt 를 직접 만들 수도 있습니다

`cookies.example.txt` 를 복사해 같은 폴더에 `cookies.txt` 로 저장하고 값을 채우면 됩니다.

```
NID_AUT=aBcD...
NID_JST=eFgH...
NID_SES=iJkL...
```

`a=b; c=d` 형태로 통째로 붙여넣어도 알아서 필요한 값만 뽑아 씁니다.

> ⚠️ **쿠키는 로그인 세션 그 자체입니다.** 남에게 보여주거나 깃허브에 올리지 마세요.
> (`.gitignore` 에 `cookies.txt` 가 등록되어 있습니다.)
> 로그아웃하거나 시간이 지나면 만료되므로, 인증 오류가 나면 다시 복사하면 됩니다.

---

## 4. 옵션

| 옵션 | 설명 | 기본값 |
|---|---|---|
| `-o`, `--outdir` | 저장 폴더 | `downloads` |
| `-q`, `--quality` | `1080` / `720` / `480` / `best` / `worst` | `1080` |
| `-c`, `--cookies` | 쿠키 파일 경로 | 스크립트 옆 `cookies.txt` |
| `--pages` | 게시판 목록을 몇 페이지까지 읽을지 | `10` |
| `--per-page` | 목록 한 페이지당 글 수 | `50` |
| `--limit` | 최대 처리할 게시글 수 (`0` = 전부) | `0` |
| `--oldest-first` | 오래된 글부터 처리 | 최신순 |
| `--skip-notice` | 공지글 건너뛰기 | 포함 |
| `--full-title` | 대괄호 대신 **제목 전체**를 파일명으로 | 대괄호 |
| `--prefix-id` | 파일명 앞에 게시글 번호 붙이기 | 안 붙임 |
| `--overwrite` | 이미 있는 파일도 다시 받기 | 건너뜀 |
| `--list` | 다운로드 없이 목록/화질만 출력 | — |
| `--sleep` | 영상 하나 받은 뒤 쉬는 시간(초) | `1.0` |

쿠키는 환경변수 `NID_AUT` / `NID_JST` / `NID_SES` 로 넘겨도 됩니다.

---

## 5. 파일 이름 규칙

| 게시글 제목 | 저장되는 이름 |
|---|---|
| `7월 1일 [OT/Be동사1]` | `OT-Be동사1.mp4` |
| `7월 27일 [To부정사와 동명사]` | `To부정사와 동명사.mp4` |
| `7월 3일 수업` (대괄호 없음) | `7월 3일 수업.mp4` |
| 한 글에 영상이 2개 | `이름_1.mp4`, `이름_2.mp4` |

- 윈도우에서 쓸 수 없는 문자(`\ / : * ? " < > |`)는 `-` 로 바뀝니다.
  그래서 `[OT/Be동사1]` → `OT-Be동사1` 이 됩니다.
- **이름이 겹치면 건너뜁니다.** 이어받기·재실행에 편하지만, 서로 다른 글의
  대괄호 내용이 같다면 뒤쪽 글이 저장되지 않습니다. 이럴 땐 `--prefix-id` 를
  쓰면 `407624_OT-Be동사1.mp4` 처럼 글 번호가 붙어 겹치지 않습니다.

---

## 6. 동작 원리

```
게시판 URL
  └─ 목록 API   apis.naver.com/cafe-web/cafe-boardlist-api/v1/cafes/{카페}/menus/{메뉴}/articles
       └─ 게시글 API apis.naver.com/cafe-web/cafe-articleapi/v3/cafes/{카페}/articles/{글번호}
            ├─ subject      → 제목 → 대괄호 추출 → 파일 이름
            └─ contentHtml  → <script class="__se_module_data" data-module='{"data":{"vid":..,"inkey":..}}'>
                 └─ 재생정보 API  apis.naver.com/rmcnmv/rmcnmv/vod/play/v2.0/{vid}?key={inkey}
                      ├─ JSON 응답  → videos.list[].source (encodingOption.height == 1080)
                      └─ DASH(MPD) → <Representation id="PD_1080P_01" height="1080"><BaseURL>...mp4
                           └─ 서명된 mp4 주소를 그대로 저장 (Range 이어받기 지원)
```

재생정보 API 는 JSON 으로 올 때도 있고 DASH 매니페스트(XML)로 올 때도 있어서
**두 형태를 모두 처리**합니다. `PD_` 로 시작하는 항목은 영상+음성이 합쳐진
완성된 mp4 라서 ffmpeg 없이 그대로 저장할 수 있습니다.

---

## 7. 자주 생기는 문제

| 증상 | 원인 / 해결 |
|---|---|
| `로그인하지 않았습니다. (errorCode=0004)` | 쿠키 만료. `cookies.txt` 를 지우고 다시 실행해 새 쿠키를 넣으세요. |
| `errorCode=0004` 인데 방금 복사했다 | `NID_AUT` 와 `NID_SES` 를 둘 다 넣었는지, 값 앞뒤에 공백이 섞이지 않았는지 확인하세요. |
| `삭제되었거나 존재하지 않는 게시글입니다` | 글 번호가 잘못됐거나 삭제된 글입니다. |
| `동영상이 없습니다` | 그 글에 카페 업로드 영상이 없습니다. (유튜브 링크는 대상이 아닙니다) |
| `1080p 가 없어 720p 로 받습니다` | 업로더가 1080p 로 올리지 않은 영상입니다. `--list` 로 화질을 확인할 수 있습니다. |
| `재생정보 오류` / 화질 없음 | 유료·DRM 영상이거나 재생 권한이 없는 경우입니다. |
| `파이썬을 찾을 수 없습니다` | 파이썬 설치 시 `Add python.exe to PATH` 를 체크하지 않은 경우입니다. 다시 설치하세요. |
| 한글이 깨짐 | `download.bat` 을 통해 실행하세요 (`chcp 65001` 로 UTF-8 을 켭니다). |
| 중간에 멈춤 | 다시 실행하면 `.part` 파일부터 **이어받습니다.** |

---

## 8. 주의

- 본인이 **정상적으로 접근 권한을 가진** 게시물을, **개인 소장·복습 목적**으로
  내려받는 용도입니다.
- 내려받은 영상의 저작권은 원저작자에게 있습니다. **재배포·재업로드·공유는 하지 마세요.**
- 짧은 시간에 과도하게 요청하지 않도록 기본적으로 영상 사이에 1초를 쉽니다.
  대량으로 받을 때는 `--sleep` 을 늘려 주세요.

---

## 9. 파일 구성

```
naver_cafe_dl.py      본체 (파이썬 표준 라이브러리만 사용)
download.bat          윈도우 실행용 - 더블클릭
download.sh           macOS / 리눅스 실행용
cookies.example.txt   쿠키 파일 양식
cookies.txt           실제 쿠키 (직접 만듦, 깃에 올라가지 않음)
downloads/            받은 영상 (깃에 올라가지 않음)
```
