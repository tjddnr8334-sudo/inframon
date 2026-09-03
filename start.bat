@echo off
REM inframon — 더블클릭 한 번으로 설치부터 결과까지.
REM (자세한 옵션은 python start.py --help)
cd /d "%~dp0"
python start.py %*
if errorlevel 1 (
  echo.
  echo [inframon] 문제가 있었습니다. 위 메시지를 확인하세요.
)
pause
