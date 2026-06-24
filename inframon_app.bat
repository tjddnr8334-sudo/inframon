@echo off
REM inframon 데스크톱 앱 — 더블클릭하면 대시보드가 전용 창으로 뜬다.
REM (로그 확인용으로 콘솔을 함께 띄움. 콘솔 없이 쓰려면 아래 python 을 pythonw 로 바꾸세요.)
cd /d "%~dp0"
python -m inframon --app
if errorlevel 1 (
  echo.
  echo [inframon] 실행 중 오류가 발생했습니다. 위 메시지를 확인하세요.
  pause
)
