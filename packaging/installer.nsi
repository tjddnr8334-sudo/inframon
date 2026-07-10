; inframon 배포용 단일파일 설치기 (NSIS) — 선택사항.
; 빌드:  makensis packaging\installer.nsi   (NSIS 설치 필요: https://nsis.sourceforge.io)
; 산출:  packaging\inframon-setup.exe  (더블클릭 설치)
;
; dist\inframon\ (PyInstaller onedir) 전체를 담아 Program Files 에 설치하고
; 시작메뉴·바탕화면 바로가기와 제거기를 만든다.

!define APP "inframon"
!define VER "0.1.0"
Unicode true
Name "${APP} ${VER}"
OutFile "inframon-setup.exe"
InstallDir "$PROGRAMFILES64\${APP}"
InstallDirRegKey HKLM "Software\${APP}" "InstallDir"
RequestExecutionLevel admin
Icon "..\assets\inframon.ico"
UninstallIcon "..\assets\inframon.ico"

Page directory
Page instfiles
UninstPage uninstConfirm
UninstPage instfiles

Section "Install"
  SetOutPath "$INSTDIR"
  File /r "..\dist\inframon\*.*"
  File "..\assets\inframon.ico"

  CreateDirectory "$SMPROGRAMS\${APP}"
  CreateShortcut "$SMPROGRAMS\${APP}\${APP}.lnk" "$INSTDIR\inframon.exe" "" "$INSTDIR\inframon.ico"
  CreateShortcut "$DESKTOP\${APP}.lnk" "$INSTDIR\inframon.exe" "" "$INSTDIR\inframon.ico"

  WriteRegStr HKLM "Software\${APP}" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP}" \
    "DisplayName" "${APP}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP}" \
    "DisplayIcon" "$INSTDIR\inframon.ico"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP}" \
    "UninstallString" "$INSTDIR\uninstall.exe"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP}" \
    "DisplayVersion" "${VER}"
  WriteUninstaller "$INSTDIR\uninstall.exe"
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\${APP}.lnk"
  RMDir /r "$SMPROGRAMS\${APP}"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP}"
  DeleteRegKey HKLM "Software\${APP}"
SectionEnd
