# inframon — 새 컴퓨터용 한 줄 설치기 (PowerShell 5.1+ / 7)
#
#   irm https://raw.githubusercontent.com/tjddnr8334-sudo/inframon/main/install.ps1 | iex
#
# 하는 일: Python·Git 확인(없으면 winget 으로 설치) → GitHub 에서 받기(있으면 갱신)
#          → python start.py --full --dashboard  (전부 설치 → 데모 → 대시보드 자동 열림)
# 설치 위치: $HOME\inframon  (바꾸려면 실행 전 $env:INFRAMON_DIR = "D:\어디" )

# 주의: 네이티브 명령(git·winget)이 stderr 로 진행률을 찍으면 PS 5.1 은 "Stop" 에서 오류로 죽는다 → Continue + 종료코드 확인
$ErrorActionPreference = "Continue"
$Repo = "https://github.com/tjddnr8334-sudo/inframon"
$Dir  = if ($env:INFRAMON_DIR) { $env:INFRAMON_DIR } else { Join-Path $HOME "inframon" }

function Say($t) { Write-Host ""; Write-Host "==> $t" -ForegroundColor Cyan }
function RefreshPath {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}
function Fail($t) { Write-Host "    $t" -ForegroundColor Yellow; exit 1 }
function HaveWinget { return [bool](Get-Command winget -ErrorAction SilentlyContinue) }

# ---------- 1. Python 3.11+ ----------
function FindPython {
    foreach ($c in @("python", "python3", "py")) {
        $cmd = Get-Command $c -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        if ($cmd.Source -like "*WindowsApps*") { continue }     # 스토어 stub 은 제외
        try {
            $v = & $cmd.Source -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
            if ($v -and [version]$v -ge [version]"3.11") { return $cmd.Source }
        } catch {}
    }
    return $null
}

Say "1/4  Python 확인"
$Py = FindPython
if (-not $Py) {
    if (HaveWinget) {
        Write-Host "    Python 3.11+ 가 없어 winget 으로 설치합니다 (1~2분)…"
        winget install -e --id Python.Python.3.12 --scope user --silent `
            --accept-package-agreements --accept-source-agreements | Out-Null
        RefreshPath
        $Py = FindPython
    }
    if (-not $Py) {
        Write-Host "    Python 을 자동 설치하지 못했습니다. https://www.python.org/downloads/ 에서 받아" -ForegroundColor Yellow
        Write-Host "    'Add python.exe to PATH' 를 체크해 설치한 뒤 이 명령을 다시 실행하세요." -ForegroundColor Yellow
        exit 1
    }
}
Write-Host "    $Py  ($(& $Py --version))"

# ---------- 2. Git (없으면 winget, 그것도 안 되면 zip 으로 받음) ----------
Say "2/4  Git 확인"
$Git = Get-Command git -ErrorAction SilentlyContinue
if (-not $Git -and (HaveWinget)) {
    Write-Host "    Git 이 없어 winget 으로 설치합니다…"
    try {
        winget install -e --id Git.Git --silent --accept-package-agreements --accept-source-agreements | Out-Null
        RefreshPath
        $Git = Get-Command git -ErrorAction SilentlyContinue
    } catch {}
}
if ($Git) { Write-Host "    $($Git.Source)" } else { Write-Host "    Git 없음 → zip 으로 받습니다." }

# ---------- 3. 받기 / 갱신 ----------
Say "3/4  inframon 받기 -> $Dir"
if (Test-Path (Join-Path $Dir "start.py")) {
    if ($Git -and (Test-Path (Join-Path $Dir ".git"))) {
        Write-Host "    이미 있음 → git pull 로 최신화"
        & $Git.Source -C $Dir pull --ff-only 2>&1 | ForEach-Object { "    $_" }
    } else {
        Write-Host "    이미 있음 (그대로 사용)"
    }
} elseif ($Git) {
    & $Git.Source clone --depth 1 $Repo $Dir 2>&1 | ForEach-Object { "    $_" }
    if (-not (Test-Path (Join-Path $Dir "start.py"))) { Fail "git clone 실패 — 인터넷 연결을 확인하고 다시 실행하세요." }
} else {
    $zip = Join-Path $env:TEMP "inframon-main.zip"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    try { Invoke-WebRequest "$Repo/archive/refs/heads/main.zip" -OutFile $zip -ErrorAction Stop }
    catch { Fail "zip 다운로드 실패 — 인터넷 연결을 확인하세요. ($($_.Exception.Message))" }
    $tmp = Join-Path $env:TEMP "inframon-unzip"
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    Expand-Archive $zip $tmp
    New-Item -ItemType Directory -Force (Split-Path $Dir) | Out-Null
    Move-Item (Join-Path $tmp "inframon-main") $Dir
    Remove-Item $zip, $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

# ---------- 4. 전부 설치 → 데모 → 대시보드 ----------
Say "4/4  설치 → 데모 → 대시보드  (처음 한 번은 5~10분, 끄려면 Ctrl+C)"
Write-Host "    다음부터 대시보드만:  cd `"$Dir`";  .venv\Scripts\streamlit run src\inframon\dashboard\app.py"
Set-Location $Dir
& $Py start.py --full --dashboard
if ($LASTEXITCODE -ne 0) { Fail "start.py 가 종료코드 $LASTEXITCODE 로 끝났습니다. 위 메시지를 확인하세요." }
