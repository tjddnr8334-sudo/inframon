@echo off
chcp 65001 >nul
REM inframon portable launcher - double click me.
REM 처음 실행: Python·의존성 자동 설치(수분~수십분). 이후: 바로 구동.
cd /d "%~dp0"
if exist "%~dp0bootstrap.ps1" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap.ps1"
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\portable\bootstrap.ps1"
)
if errorlevel 1 (
  echo.
  echo [inframon] 오류가 발생했습니다. 위 메시지를 확인하세요.
  pause
)
