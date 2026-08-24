<#
  포터블 배포 zip 생성 — "타 컴퓨터에서 압축 풀고 INFRAMON_시작.bat 더블클릭"용.

  구성: git archive(추적 파일만 — 데이터/venv 미포함) + zip 루트에 런처 2종 복사.
  zip 압축은 .NET ZipFile 에 **UTF-8 엔트리 인코딩을 명시** — bsdtar(zip 쓰기가 CP437
  고정)와 Compress-Archive 는 한글 파일명(docs/*.md 등)을 깨뜨리므로 쓰지 않는다.

  사용(리포 루트에서):
    powershell -ExecutionPolicy Bypass -File packaging\make_portable.ps1
  산출: dist\inframon_portable.zip
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Dist = Join-Path $Root "dist"
$Stage = Join-Path $env:TEMP "inframon_portable_stage"
$OutZip = Join-Path $Dist "inframon_portable.zip"

Write-Host ">> 스테이징: git archive (추적 파일만)"
if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Stage, $Dist | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem
$Utf8 = [Text.UTF8Encoding]::new($false)
# ⚠️ tar.exe(bsdtar)는 한글 파일명을 CP437 로 깨뜨린다(추출·압축 양쪽) — git zip + .NET 만 쓴다.
$SrcZip = Join-Path $env:TEMP "inframon_src.zip"
git -C $Root archive --format=zip -o $SrcZip HEAD
if ($LASTEXITCODE -ne 0) { throw "git archive 실패 — 리포 루트에서 실행했는지 확인" }
[IO.Compression.ZipFile]::ExtractToDirectory($SrcZip, $Stage, $Utf8)
Remove-Item $SrcZip -Force

Write-Host ">> 런처를 zip 루트에 배치"
Copy-Item (Join-Path $Root "packaging\portable\INFRAMON_시작.bat") $Stage -Force
Copy-Item (Join-Path $Root "packaging\portable\bootstrap.ps1") $Stage -Force

Write-Host ">> 압축: $OutZip (UTF-8 파일명, '/' 구분자)"
if (Test-Path $OutZip) { Remove-Item $OutZip -Force }
# CreateFromDirectory 는 '\' 구분자를 쓰므로 엔트리를 직접 만들어 '/' 로 정규화한다.
$Zip = [IO.Compression.ZipFile]::Open($OutZip, "Create", $Utf8)
try {
    Get-ChildItem $Stage -Recurse -File | ForEach-Object {
        $rel = $_.FullName.Substring($Stage.Length + 1) -replace "\\", "/"
        [IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $Zip, $_.FullName, $rel, [IO.Compression.CompressionLevel]::Optimal) | Out-Null
    }
} finally { $Zip.Dispose() }
Remove-Item $Stage -Recurse -Force

$mb = [math]::Round((Get-Item $OutZip).Length / 1MB, 1)
Write-Host ""
Write-Host "완료: $OutZip ($mb MB)" -ForegroundColor Green
Write-Host "타 컴퓨터에서: 압축 해제 → INFRAMON_시작.bat 더블클릭"
Write-Host "  첫 실행: Python·의존성 자동 설치 후 구동 / 이후: 바로 구동"
