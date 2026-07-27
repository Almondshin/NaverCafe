#!/usr/bin/env bash
# 네이버 카페 동영상 다운로더 (macOS / Linux 용 실행 스크립트)
#
#   ./download.sh                       -> URL 을 물어봅니다
#   ./download.sh <URL> [옵션]
#
# 처음 한 번만:  chmod +x download.sh

set -u
cd "$(dirname "$0")"

PY=""
for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
        PY="$cand"
        break
    fi
done

if [ -z "$PY" ]; then
    echo
    echo " [X] 파이썬 3 을 찾을 수 없습니다."
    echo "     macOS:  brew install python3   또는  xcode-select --install"
    echo
    exit 1
fi

"$PY" "./naver_cafe_dl.py" "$@"
status=$?

echo
echo " ----------------------------------------------------------------"
echo "  받은 파일은 $(pwd)/downloads 폴더에 있습니다."
echo "  쿠키를 다시 입력하려면 cookies.txt 파일을 지우고 실행하세요."
echo " ----------------------------------------------------------------"
exit $status
