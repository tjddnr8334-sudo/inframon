<#
  inframon 원클릭 설치 (관리자 불필요) — PowerShell 내장 기능만 사용.

  하는 일:
    1. dist\inframon\  전체를  %LOCALAPPDATA%\Programs\inframon  으로 복사
    2. 바탕화면 + 시작메뉴에 바로가기(.lnk, 아이콘 포함) 생성
    3. 프로그램 추가/제거 목록에 등록(+ uninstall.ps1)

  사용:
    dist 폴더가 있는 리포 루트에서:
      powershell -ExecutionPolicy Bypass -File packaging\install.ps1
    제거:
      powershell -ExecutionPolicy Bypass -File "%LOCALAPPDATA%\Programs\inframon\uninstall.ps1"
#>
$ErrorActionPreference = "Stop"
$AppName   = "inframon"
$Publisher = "inframon"
$SrcDir    = Join-Path (Split-Path -Parent $PSScriptRoot) "dist\inframon"
$InstallDir= Join-Path $env:LOCALAPPDATA "Programs\inframon"
$ExePath   = Join-Path $InstallDir "inframon.exe"
$IconPath  = Join-Path $InstallDir "_icon\inframon.ico"

if (-not (Test-Path (Join-Path $SrcDir "inframon.exe"))) {
    throw "빌드 산출물이 없습니다: $SrcDir\inframon.exe  (먼저 'pyinstaller inframon.spec --noconfirm')"
}

Write-Host ">> 설치 위치: $InstallDir"
if (Test-Path $InstallDir) { Remove-Item $InstallDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Write-Host ">> 파일 복사 중... (수백 MB, 잠시 소요)"
Copy-Item (Join-Path $SrcDir "*") $InstallDir -Recurse -Force

# 아이콘 동봉(바로가기용) — assets\inframon.ico 가 있으면 사용
$AssetIcon = Join-Path (Split-Path -Parent $PSScriptRoot) "assets\inframon.ico"
New-Item -ItemType Directory -Force -Path (Split-Path $IconPath) | Out-Null
if (Test-Path $AssetIcon) { Copy-Item $AssetIcon $IconPath -Force } else { $IconPath = $ExePath }

function New-Shortcut($LnkPath) {
    $ws = New-Object -ComObject WScript.Shell
    $sc = $ws.CreateShortcut($LnkPath)
    $sc.TargetPath       = $ExePath
    $sc.WorkingDirectory = $InstallDir
    $sc.IconLocation     = $IconPath
    $sc.Description       = "교량 인프라 모니터링 대시보드"
    $sc.Save()
}

$Desktop   = [Environment]::GetFolderPath("Desktop")
$StartMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
New-Shortcut (Join-Path $Desktop   "inframon.lnk")
New-Shortcut (Join-Path $StartMenu "inframon.lnk")
Write-Host ">> 바로가기 생성: 바탕화면 + 시작메뉴"

# 프로그램 추가/제거 등록(현재 사용자)
$UninstallPs1 = Join-Path $InstallDir "uninstall.ps1"
@"
`$ErrorActionPreference='SilentlyContinue'
Get-Process inframon | Stop-Process -Force
Remove-Item '$Desktop\inframon.lnk','$StartMenu\inframon.lnk' -Force
Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\inframon' -Recurse -Force
Start-Sleep 1
Remove-Item '$InstallDir' -Recurse -Force
Write-Host 'inframon 제거 완료.'
"@ | Set-Content -Path $UninstallPs1 -Encoding UTF8

$RegKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\inframon"
New-Item -Path $RegKey -Force | Out-Null
Set-ItemProperty $RegKey DisplayName    $AppName
Set-ItemProperty $RegKey DisplayIcon    $IconPath
Set-ItemProperty $RegKey Publisher      $Publisher
Set-ItemProperty $RegKey InstallLocation $InstallDir
Set-ItemProperty $RegKey UninstallString "powershell -ExecutionPolicy Bypass -File `"$UninstallPs1`""
Set-ItemProperty $RegKey NoModify 1
Set-ItemProperty $RegKey NoRepair 1

Write-Host ""
Write-Host "설치 완료! 바탕화면의 'inframon' 아이콘을 더블클릭하세요." -ForegroundColor Green
Write-Host "  실행파일: $ExePath"
Write-Host "  제거    : 설정>앱 목록 'inframon' 또는 $UninstallPs1"
