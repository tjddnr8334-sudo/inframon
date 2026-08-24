<#
  inframon 포터블 부트스트랩 — "압축 풀고 더블클릭"의 전부.

  하는 일(멱등 — 매 실행 시 빠진 것만 채움):
    1. Python 3.10+ 탐지 — 없으면 자동 설치(winget → 공식 인스톨러 순, 관리자 불필요)
    2. .venv 생성 + pip 의존성 전부 설치(기본: dashboard/search/hyp3/report/bim/pinn + pywebview)
       — pyproject.toml 해시를 마커로 저장해 두 번째 실행부터는 설치를 건너뛰고 바로 구동
    3. inframon 데스크톱 창(--app) 실행 — pywebview 실패 시 브라우저 대시보드 폴백

  옵션(환경변수):
    INFRAMON_FULL=1     cv(transformers·torch 대형) extra 까지 설치
    INFRAMON_EXTRAS=..  기본 extras 묶음을 직접 지정(쉼표 구분)
    INFRAMON_NO_RUN=1   설치만 하고 실행은 생략(프로비저닝 검증용)
    INFRAMON_WSL=1|0    SARvey 레인(WSL+ISCE2) 구축을 묻지 않고 진행(1)/건너뜀(0).
                        미지정이면 콘솔에서 물어본다(대답 불가 환경이면 건너뜀).
#>
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8

# ── 리포 루트: zip 루트에 복사돼 있으면 그 자리, 리포 안(packaging\portable)이면 두 단계 위 ──
if (Test-Path (Join-Path $PSScriptRoot "pyproject.toml")) { $Root = $PSScriptRoot }
else { $Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }
if (-not (Test-Path (Join-Path $Root "pyproject.toml"))) {
    throw "pyproject.toml 을 찾지 못했습니다 — 압축을 폴더째 풀었는지 확인하세요: $Root"
}
Write-Host "=== inframon 포터블 부트스트랩 ===" -ForegroundColor Cyan
Write-Host "  위치: $Root"

# ── 1. Python 3.10+ 탐지 ─────────────────────────────────────────────────────
function Find-Python {
    $cands = @(
        @("py", "-3.12"), @("py", "-3.11"), @("py", "-3.10"), @("python", $null))
    foreach ($c in $cands) {
        $exe = $c[0]; $sw = $c[1]
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        try {
            if ($sw) { $v = & $exe $sw --version 2>$null } else { $v = & $exe --version 2>$null }
        } catch { continue }
        if ($v -match "Python 3\.(\d+)") {
            if ([int]$Matches[1] -ge 10) {
                if ($sw) { return @($exe, $sw) }
                return @($exe)
            }
        }
    }
    return $null
}

$Py = Find-Python
if (-not $Py) {
    Write-Host ">> Python 이 없습니다 — 자동 설치를 시도합니다(관리자 불필요)." -ForegroundColor Yellow
    $installed = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "   winget 으로 Python 3.11 설치 중..."
        winget install -e --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -eq 0) { $installed = $true }
    }
    if (-not $installed) {
        $url = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
        $tmp = Join-Path $env:TEMP "python-3.11.9-amd64.exe"
        Write-Host "   python.org 인스톨러 다운로드 중... ($url)"
        Invoke-WebRequest -Uri $url -OutFile $tmp
        Write-Host "   조용히 설치 중(사용자 영역)..."
        Start-Process $tmp -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1" -Wait
    }
    # 새 PATH 는 이 세션에 아직 없으므로 설치 표준 경로를 직접 찾는다
    $direct = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"
    if (Test-Path $direct) { $Py = @($direct) } else { $Py = Find-Python }
    if (-not $Py) {
        throw "Python 자동 설치에 실패했습니다. https://www.python.org/downloads/ 에서 3.11 설치 후 재실행하세요."
    }
}
Write-Host "  Python: $($Py -join ' ')"

# ── 2. venv + 의존성 (pyproject 해시 마커로 멱등) ────────────────────────────
# venv 는 패키지 폴더가 아니라 **짧은 고정 경로**에 둔다: torch 등의 내부 경로가 깊어
# 패키지를 깊은 폴더에 풀면 Windows MAX_PATH(260자)로 pip 가 죽는다(WinError 206).
# 압축 위치별로 분리되도록 루트 경로 해시를 이름에 붙인다.
$RootHash = ([BitConverter]::ToString(
    [Security.Cryptography.SHA256]::Create().ComputeHash(
        [Text.Encoding]::UTF8.GetBytes($Root.ToLowerInvariant()))) -replace "-", "").Substring(0, 8)
$Venv = Join-Path $env:LOCALAPPDATA "inframon\venv-$RootHash"
$VPy  = Join-Path $Venv "Scripts\python.exe"
Write-Host "  가상환경: $Venv"
if (-not (Test-Path $VPy)) {
    Write-Host ">> 가상환경 생성: $Venv"
    $PyArgs = @()
    if ($Py.Count -gt 1) { $PyArgs = @($Py[1]) }
    & $Py[0] @PyArgs -m venv $Venv
}

$Hash   = (Get-FileHash (Join-Path $Root "pyproject.toml") -Algorithm SHA256).Hash
$Marker = Join-Path $Venv "provisioned.txt"
$Extras = $env:INFRAMON_EXTRAS
if (-not $Extras) { $Extras = "dev,dashboard,search,hyp3,report,bim,pinn" }
if ($env:INFRAMON_FULL -eq "1") { $Extras = "$Extras,cv" }
$Stamp = "$Hash extras=$Extras"

if ((Test-Path $Marker) -and ((Get-Content $Marker -Raw).Trim() -eq $Stamp)) {
    Write-Host "  의존성: 이미 설치됨(마커 일치) — 건너뜀"
} else {
    Write-Host ">> pip 의존성 설치 중... (첫 실행은 수분~수십분, 수 GB 다운로드 가능)" -ForegroundColor Yellow
    & $VPy -m pip install --upgrade pip
    & $VPy -m pip install -e "$Root[$Extras]" pywebview
    if ($LASTEXITCODE -ne 0) { throw "pip 설치 실패 — 네트워크/디스크 확인 후 재실행하세요." }
    Set-Content -Path $Marker -Value $Stamp -Encoding UTF8
    Write-Host "  설치 완료 ✔" -ForegroundColor Green
    Write-Host "  (SARvey/WSL 레인이 필요하면: python -m inframon --insar-tools-install)"
}

# ── 3. 선택: SARvey 레인(WSL + ISCE2 툴체인) ─────────────────────────────────
# 최고품질 full PSI 처리에만 필요 — SNAP(--snap-auto)·HyP3(--hyp3-insar) 레인은 이것
# 없이 동작한다. WSL 설치는 관리자 권한 + 재부팅 1회가 필요해 '선택'으로 묻는다.
function Ask-YesNo($msg) {
    if ($env:INFRAMON_WSL -eq "1") { return $true }
    if ($env:INFRAMON_WSL -eq "0") { return $false }
    try { $a = Read-Host "$msg [y/N]" } catch { return $false }   # 비대화형 → 건너뜀
    return ($a -match "^[yY]")
}

$wslReady = $false
try { $d = (wsl -l -q 2>$null) -replace "`0", ""; $wslReady = [bool]($d -and $d.Trim()) } catch {}
if (-not $wslReady) {
    if (Ask-YesNo ">> [선택] SARvey 레인용 WSL(Ubuntu)을 설치할까요? (관리자 승인 + 재부팅 필요)") {
        Write-Host "   관리자 권한으로 WSL 설치를 시작합니다 — UAC 창을 승인하세요."
        Start-Process powershell -Verb RunAs -Wait -ArgumentList `
            "-NoProfile", "-Command", "wsl --install -d Ubuntu-22.04"
        Write-Host "   ⚠️ 재부팅 후 Ubuntu 첫 실행(사용자 생성)을 마치고, 이 bat 을 다시 실행하면" -ForegroundColor Yellow
        Write-Host "      ISCE2 툴체인 구축을 이어서 진행합니다." -ForegroundColor Yellow
    } else {
        Write-Host "  WSL 건너뜀 — SNAP/HyP3 레인은 그대로 사용 가능. 나중에: python -m inframon --insar-tools"
    }
} else {
    & $VPy -m inframon --insar-tools *> $null
    if ($LASTEXITCODE -ne 0) {
        if (Ask-YesNo ">> [선택] WSL 은 있으나 ISCE2 툴체인이 없습니다. 지금 구축할까요? (수 GB·수십 분)") {
            & $VPy -m inframon --insar-tools-install
            if ($LASTEXITCODE -ne 0) { Write-Host "  ⚠️ 툴체인 구축 실패 — 위 출력 확인 후 재실행 가능" -ForegroundColor Yellow }
        } else {
            Write-Host "  툴체인 건너뜀 — 나중에: python -m inframon --insar-tools-install"
        }
    } else {
        Write-Host "  SARvey 레인: WSL + ISCE2 툴체인 준비됨 ✔" -ForegroundColor Green
    }
}

# SNAP(레인 A, Windows 네이티브) 상태도 알려만 준다 — GUI 인스톨러라 자동화하지 않는다.
if (-not (Test-Path "C:\Program Files\esa-snap\bin\gpt.exe")) {
    Write-Host "  ⓘ SNAP(레인 A) 미설치 — --snap-auto 를 쓰려면: https://step.esa.int/main/download/snap-download/"
}

# ── 4. 구동 ──────────────────────────────────────────────────────────────────
if ($env:INFRAMON_NO_RUN -eq "1") { Write-Host "INFRAMON_NO_RUN=1 — 설치만 완료."; exit 0 }
Write-Host ">> inframon 시작 (전용 창)..."
Set-Location $Root
& $VPy -m inframon --app
if ($LASTEXITCODE -ne 0) {
    Write-Host ">> 전용 창 실패 — 브라우저 대시보드로 폴백합니다." -ForegroundColor Yellow
    & $VPy -m streamlit run (Join-Path $Root "src\inframon\dashboard\app.py")
}
