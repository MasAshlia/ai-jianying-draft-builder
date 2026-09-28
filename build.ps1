param([string]$TestMediaDir = "")

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "Virtual environment not found. Follow README to create .venv first."
}

$distRoot = Join-Path $projectRoot "dist"
$running = Get-CimInstance Win32_Process -Filter "Name='AIDraftBuilder.exe'" | Where-Object {
    $_.ExecutablePath -and $_.ExecutablePath.StartsWith($distRoot + '\', [StringComparison]::OrdinalIgnoreCase)
}
if ($running) {
    Write-Output "An older AIDraftBuilder is running. Building in a new version directory; existing programs and files will be preserved."
}
if (-not $TestMediaDir) {
    $samples = @(Get-ChildItem -LiteralPath (Join-Path $projectRoot "test_materials") -Directory -ErrorAction SilentlyContinue | Where-Object {
        @(Get-ChildItem -LiteralPath $_.FullName -Filter '*.mp4' -File).Count -ge 2
    })
    if ($samples.Count -eq 1) { $TestMediaDir = $samples[0].FullName }
}
if (-not (Test-Path -LiteralPath $TestMediaDir -PathType Container)) {
    throw "Real media is required for a release build. Pass -TestMediaDir with a folder containing at least two MP4 videos."
}
$env:AIDRAFT_TEST_MEDIA = (Resolve-Path -LiteralPath $TestMediaDir).Path
$version = & $pythonExe -c "import sys; sys.path.insert(0, sys.argv[1]); from ai_draft_builder import __version__; print(__version__)" (Join-Path $projectRoot 'src')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$releaseDir = Join-Path $distRoot ("releases\" + $version + "-" + (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + [guid]::NewGuid().ToString('N').Substring(0,6))

Push-Location $projectRoot
try {
    & $pythonExe -m pytest
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $pythonExe -m PyInstaller --noconfirm --distpath $releaseDir (Join-Path $projectRoot "AIDraftBuilder.spec")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $output = Join-Path $releaseDir "smoke-output"
    $releaseExe = Join-Path $releaseDir "AIDraftBuilder\AIDraftBuilder.exe"
    $smoke = Start-Process -FilePath $releaseExe -ArgumentList @('--smoke-test', ('"' + $env:AIDRAFT_TEST_MEDIA + '"'), '--output', ('"' + $output + '"')) -PassThru -WindowStyle Hidden
    $deadline = [DateTime]::UtcNow.AddMinutes(4)
    while (-not $smoke.WaitForExit(1000)) {
        if ([DateTime]::UtcNow -gt $deadline) {
            Stop-Process -Id $smoke.Id -Force
            throw "Packaged worker smoke test timed out. Release not packaged."
        }
    }
    if ($smoke.ExitCode -ne 0) { throw "Packaged smoke test failed. See $output" }
    $report = Get-Content -LiteralPath (Join-Path $output "smoke-result.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $report.success -or $report.clip_count -lt 2) { throw "Packaged smoke test did not validate two videos." }
    & $pythonExe tools/package_release.py --release-dir $releaseDir --audit-only
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $canonical = [IO.Path]::GetFullPath((Join-Path $distRoot 'AIDraftBuilder'))
    if ((Split-Path -Parent $canonical) -ne [IO.Path]::GetFullPath($distRoot)) {
        throw 'Unexpected output path; refusing to replace it.'
    }
    $active = Get-CimInstance Win32_Process -Filter "Name='AIDraftBuilder.exe'" | Where-Object {
        $_.ExecutablePath -and $_.ExecutablePath.StartsWith($canonical + '\', [StringComparison]::OrdinalIgnoreCase)
    }
    if ($active) { throw 'Close the old dist/AIDraftBuilder program and retry. The verified staging build has been preserved.' }
    $backupDir = Join-Path $distRoot ('backups\' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $backupDir | Out-Null
    if (Test-Path -LiteralPath $canonical) {
        $existing = Get-Item -LiteralPath $canonical
        if ($existing.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing to move a linked output directory.' }
        Move-Item -LiteralPath $canonical -Destination (Join-Path $backupDir 'AIDraftBuilder')
    }
    Move-Item -LiteralPath (Join-Path $releaseDir 'AIDraftBuilder') -Destination $canonical

    $gui = Start-Process -FilePath (Join-Path $canonical 'AIDraftBuilder.exe') -PassThru -WindowStyle Hidden
    try {
        $deadline = [DateTime]::UtcNow.AddSeconds(20)
        do {
            Start-Sleep -Milliseconds 250
            $gui.Refresh()
            if ($gui.HasExited) { throw 'Packaged GUI exited before opening its main window.' }
        } while ((-not $gui.MainWindowTitle.Contains($version)) -and [DateTime]::UtcNow -lt $deadline)
        if (-not $gui.MainWindowTitle.Contains($version) -or -not $gui.Responding) {
            throw 'Packaged GUI did not open a responding versioned main window.'
        }
        Write-Output ('GUI startup passed: ' + $gui.MainWindowTitle)
    } finally {
        if (-not $gui.HasExited) {
            $null = $gui.CloseMainWindow()
            if (-not $gui.WaitForExit(5000)) { Stop-Process -Id $gui.Id -Force }
        }
    }
    $zipName = 'AIDraftBuilder-v' + $version + '-win64.zip'
    foreach ($name in @($zipName, $zipName + '.sha256')) {
        $oldArchive = Join-Path $distRoot $name
        if (Test-Path -LiteralPath $oldArchive) { Move-Item -LiteralPath $oldArchive -Destination (Join-Path $backupDir $name) }
    }
    & $pythonExe tools/package_release.py --release-dir $distRoot
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Output "Release ready: $distRoot"
} finally {
    Pop-Location
}
