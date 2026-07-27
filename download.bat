@echo off
chcp 65001 >nul
setlocal
title 네이버 카페 동영상 다운로더
cd /d "%~dp0"

rem ---------------------------------------------------------------------------
rem  사용법
rem    1) 그냥 더블클릭  -> URL 을 물어봅니다 (쿠키가 없으면 쿠키도 물어봅니다)
rem    2) 이 bat 파일 위에 URL 을 드래그 & 드롭
rem    3) 명령 프롬프트에서:  download.bat <URL> [옵션]
rem
rem  자주 쓰는 주소를 고정하고 싶으면 바로 아래 줄의 rem 을 지우고 URL 을 바꾸세요.
rem set "FIXED_URL=https://cafe.naver.com/f-e/cafes/16075980/menus/194?viewType=L"
rem ---------------------------------------------------------------------------

set "PY="

where py >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if defined PY goto :havepy

where python >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto :havepy

where python3 >nul 2>&1
if not errorlevel 1 set "PY=python3"
if defined PY goto :havepy

echo.
echo  [X] 파이썬을 찾을 수 없습니다.
echo.
echo      https://www.python.org/downloads/  에서 Python 3 을 설치하세요.
echo      설치 첫 화면의 [Add python.exe to PATH] 를 반드시 체크해야 합니다.
echo      설치 후 이 창을 닫고 download.bat 을 다시 실행하세요.
echo.
pause
exit /b 1

:havepy
if not defined FIXED_URL goto :run
if not "%~1"=="" goto :run
%PY% "%~dp0naver_cafe_dl.py" "%FIXED_URL%"
goto :done

:run
%PY% "%~dp0naver_cafe_dl.py" %*

:done
echo.
echo  ----------------------------------------------------------------
echo   받은 파일은  %~dp0downloads  폴더에 있습니다.
echo   쿠키를 다시 입력하려면 cookies.txt 파일을 지우고 실행하세요.
echo  ----------------------------------------------------------------
echo.
pause
endlocal
