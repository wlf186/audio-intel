param(
    [ValidateSet("all", "api", "asr", "tts")]
    [string]$Target = "all"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $RootDir ".runtime"
$UvVersion = "0.12.5"
$UvWindowsSha256 = "4c4d49d8738847d9b71ba319e49a5688c93eac0fe6204b1df24e98528dddf39a"
$UvBin = Join-Path $RuntimeDir "bin\uv.exe"

if (-not [Environment]::Is64BitOperatingSystem) { throw "Only 64-bit Windows is supported" }

$env:UV_CACHE_DIR = Join-Path $RootDir "cache\uv"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $RuntimeDir "python"
$env:UV_PYTHON_PREFERENCE = "only-managed"
$env:PIP_CACHE_DIR = Join-Path $RootDir "cache\pip"
$env:HF_HOME = Join-Path $RootDir "cache\huggingface"
$env:HUGGINGFACE_HUB_CACHE = Join-Path $RootDir "cache\huggingface\hub"
$env:MODELSCOPE_CACHE = Join-Path $RootDir "cache\modelscope"
$env:TORCH_HOME = Join-Path $RootDir "cache\torch"
$env:XDG_CACHE_HOME = Join-Path $RootDir "cache\xdg"
$env:TMPDIR = Join-Path $RootDir "tmp"
$env:TMP = $env:TMPDIR
$env:TEMP = $env:TMPDIR
$env:HF_HUB_OFFLINE = "0"
$env:TRANSFORMERS_OFFLINE = "0"

foreach ($directory in @((Join-Path $RuntimeDir "bin"), $env:UV_CACHE_DIR, $env:PIP_CACHE_DIR, $env:TMPDIR, (Join-Path $RootDir "models"))) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}

function Invoke-Native {
    param([scriptblock]$Operation, [string]$Description)
    & $Operation | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "$Description failed with exit code $LASTEXITCODE" }
}

function Invoke-WithRetry {
    param([scriptblock]$Operation, [string]$Description, [int]$Attempts = 3)
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            Invoke-Native $Operation $Description
            return
        } catch {
            if ($attempt -eq $Attempts) { throw }
            Write-Warning "$Description failed; retrying ($($attempt + 1)/$Attempts)"
            Start-Sleep -Seconds (2 * $attempt)
        }
    }
}

$uvReady = Test-Path $UvBin
if ($uvReady) {
    try { $uvReady = (& $UvBin --version 2>$null) -like "uv $UvVersion*" } catch { $uvReady = $false }
}
if (-not $uvReady) {
    Write-Host "[setup] Downloading uv $UvVersion for Windows x64..."
    $archive = Join-Path $RuntimeDir "uv.zip"
    $extract = Join-Path $RuntimeDir "uv-extract"
    $url = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip"
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $archive
            break
        } catch {
            if ($attempt -eq 3) { throw }
            Start-Sleep -Seconds (2 * $attempt)
        }
    }
    $actualHash = (Get-FileHash -Path $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $UvWindowsSha256) { throw "uv checksum verification failed" }
    Remove-Item $extract -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive -Path $archive -DestinationPath $extract -Force
    $downloadedUv = Get-ChildItem -Path $extract -Filter "uv.exe" -Recurse | Select-Object -First 1
    if ($null -eq $downloadedUv) { throw "uv.exe was not found in the downloaded archive" }
    Copy-Item $downloadedUv.FullName $UvBin -Force
    Remove-Item $archive -Force -ErrorAction SilentlyContinue
    Remove-Item $extract -Recurse -Force -ErrorAction SilentlyContinue
}

function Get-PythonPath {
    param([string]$Name)
    return Join-Path $RuntimeDir "$Name\Scripts\python.exe"
}

function Ensure-Environment {
    param([string]$Name)
    $python = Get-PythonPath $Name
    if (-not (Test-Path $python)) {
        Invoke-WithRetry { & $UvBin python install 3.12 } "Install managed Python 3.12"
        Invoke-Native { & $UvBin venv --python 3.12 (Join-Path $RuntimeDir $Name) } "Create $Name environment"
    }
    return $python
}

function Install-Requirements {
    param([string]$Name, [string]$Python, [string]$Requirements)
    $lock = Join-Path $RootDir "requirements-lock\windows\$Name.txt"
    if (Test-Path $lock) {
        Invoke-WithRetry { & $UvBin pip sync --python $Python --require-hashes --strict $lock } "Sync $Name environment"
    } else {
        Invoke-WithRetry { & $UvBin pip install --python $Python -r (Join-Path $RootDir $Requirements) } "Install $Requirements"
    }
}

function Install-ModelEnvironment {
    param([string]$Name, [string]$Python, [string]$Requirements)
    $lock = Join-Path $RootDir "requirements-lock\windows\$Name.txt"
    if (Test-Path $lock) {
        Invoke-WithRetry { & $UvBin pip sync --python $Python --torch-backend cu130 --require-hashes --strict $lock } "Sync $Name environment"
    } else {
        Invoke-WithRetry { & $UvBin pip install --python $Python --torch-backend cu130 -r (Join-Path $RootDir $Requirements) } "Install $Name dependencies"
    }
}

function Invoke-Pnpm {
    param([string[]]$Arguments)
    $corepack = Get-Command "corepack.cmd" -ErrorAction SilentlyContinue
    if ($null -eq $corepack) { $corepack = Get-Command "corepack" -ErrorAction SilentlyContinue }
    if ($null -ne $corepack) {
        & $corepack.Source "pnpm@10.15.1" @Arguments
    } else {
        $npx = Get-Command "npx.cmd" -ErrorAction SilentlyContinue
        if ($null -eq $npx) { throw "Node.js with npm/npx is required to build the frontend" }
        & $npx.Source --yes "corepack@0.35.0" "pnpm@10.15.1" @Arguments
    }
}

if ($Target -eq "all" -or $Target -eq "api") {
    $apiPython = Ensure-Environment "api"
    Install-Requirements "api" $apiPython "requirements-api.txt"
    if (Test-Path (Join-Path $RootDir "frontend\package.json")) {
        Write-Host "[setup] Building the local frontend..."
        Push-Location (Join-Path $RootDir "frontend")
        try {
            Invoke-WithRetry { Invoke-Pnpm -Arguments @("install", "--frozen-lockfile") } "Install frontend dependencies"
            Invoke-Native { Invoke-Pnpm -Arguments @("build") } "Build frontend"
        } finally {
            Pop-Location
        }
    }
}

if ($Target -eq "all" -or $Target -eq "asr") {
    $asrPython = Ensure-Environment "asr"
    Install-ModelEnvironment "asr" $asrPython "requirements-asr.txt"
    Invoke-Native { & $asrPython (Join-Path $RootDir "scripts\download_models.py") asr } "Download ASR models"
}

if ($Target -eq "all" -or $Target -eq "tts") {
    $ttsPython = Ensure-Environment "tts"
    Install-ModelEnvironment "tts" $ttsPython "requirements-tts.txt"
    $alignerPython = Ensure-Environment "aligner"
    Install-ModelEnvironment "aligner" $alignerPython "requirements-aligner.txt"
    Invoke-Native { & $ttsPython (Join-Path $RootDir "scripts\download_models.py") tts } "Download TTS models"
}

Write-Host "[setup] $Target is ready. All runtime files are inside $RootDir"
