param(
    [ValidateSet("start", "stop", "restart", "status", "logs", "setup", "doctor", "tls")]
    [string]$Action = "status",
    [string]$Target = "all",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RootDir = $PSScriptRoot
$RuntimeDir = Join-Path $RootDir ".runtime"
$ServiceHelper = Join-Path $RootDir "scripts\service_process.py"
$StartTimeoutSeconds = 20

function Get-ProcessEnvironment {
    param([string]$Name)
    return [Environment]::GetEnvironmentVariable($Name, "Process")
}

function Set-ProcessEnvironment {
    param([string]$Name, [string]$Value)
    [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
}

function Set-DefaultEnvironment {
    param([string]$Name, [string]$Value)
    if ([string]::IsNullOrWhiteSpace((Get-ProcessEnvironment $Name))) {
        Set-ProcessEnvironment $Name $Value
    }
}

function Resolve-ConfiguredPath {
    param([string]$Name, [string]$Default)
    $value = Get-ProcessEnvironment $Name
    if ([string]::IsNullOrWhiteSpace($value)) { $value = $Default }
    if (-not [IO.Path]::IsPathRooted($value)) { $value = Join-Path $RootDir $value }
    return [IO.Path]::GetFullPath($value)
}

function Resolve-OptionalConfiguredPath {
    param([string]$Name)
    $value = Get-ProcessEnvironment $Name
    if ([string]::IsNullOrWhiteSpace($value)) { return "" }
    if (-not [IO.Path]::IsPathRooted($value)) { $value = Join-Path $RootDir $value }
    return [IO.Path]::GetFullPath($value)
}

$DataDir = Resolve-ConfiguredPath "AUDIO_INTEL_DATA_DIR" "data"
$TempDir = Resolve-ConfiguredPath "AUDIO_INTEL_TEMP_DIR" "tmp"
$CacheDir = Resolve-ConfiguredPath "AUDIO_INTEL_CACHE_DIR" "cache"
$LogDir = Resolve-ConfiguredPath "AUDIO_INTEL_LOG_DIR" "logs"
$RunDir = Resolve-ConfiguredPath "AUDIO_INTEL_RUN_DIR" "run"
$ModelsDir = Resolve-ConfiguredPath "AUDIO_INTEL_MODELS_DIR" "models"
$FrontendDir = Resolve-ConfiguredPath "AUDIO_INTEL_FRONTEND_DIR" "frontend\dist"

function Initialize-Environment {
    foreach ($directory in @($RunDir, $LogDir, $DataDir, $TempDir, $CacheDir, $ModelsDir)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }

    Set-DefaultEnvironment "PYTHONPATH" $RootDir
    Set-DefaultEnvironment "PYTHONUNBUFFERED" "1"
    Set-DefaultEnvironment "AUDIO_INTEL_HOST" "0.0.0.0"
    Set-DefaultEnvironment "AUDIO_INTEL_PORT" "20810"
    Set-DefaultEnvironment "AUDIO_INTEL_PROTOCOL" "http"
    Set-ProcessEnvironment "AUDIO_INTEL_PROTOCOL" ((Get-ProcessEnvironment "AUDIO_INTEL_PROTOCOL").Trim().ToLowerInvariant())
    Set-DefaultEnvironment "AUDIO_INTEL_MOCK_MODE" "0"
    Set-ProcessEnvironment "AUDIO_INTEL_DATA_DIR" $DataDir
    Set-ProcessEnvironment "AUDIO_INTEL_TEMP_DIR" $TempDir
    Set-ProcessEnvironment "AUDIO_INTEL_CACHE_DIR" $CacheDir
    Set-ProcessEnvironment "AUDIO_INTEL_LOG_DIR" $LogDir
    Set-ProcessEnvironment "AUDIO_INTEL_RUN_DIR" $RunDir
    Set-ProcessEnvironment "AUDIO_INTEL_MODELS_DIR" $ModelsDir
    Set-ProcessEnvironment "AUDIO_INTEL_FRONTEND_DIR" $FrontendDir
    foreach ($name in @("AUDIO_INTEL_TLS_CERT_FILE", "AUDIO_INTEL_TLS_KEY_FILE", "AUDIO_INTEL_TLS_CA_FILE")) {
        $resolved = Resolve-OptionalConfiguredPath $name
        if (-not [string]::IsNullOrWhiteSpace($resolved)) { Set-ProcessEnvironment $name $resolved }
    }
    Set-DefaultEnvironment "HF_HOME" (Join-Path $CacheDir "huggingface")
    Set-DefaultEnvironment "HUGGINGFACE_HUB_CACHE" (Join-Path $CacheDir "huggingface\hub")
    Set-DefaultEnvironment "MODELSCOPE_CACHE" (Join-Path $CacheDir "modelscope")
    Set-DefaultEnvironment "TORCH_HOME" (Join-Path $CacheDir "torch")
    Set-DefaultEnvironment "XDG_CACHE_HOME" (Join-Path $CacheDir "xdg")
    Set-DefaultEnvironment "HF_HUB_OFFLINE" "1"
    Set-DefaultEnvironment "TRANSFORMERS_OFFLINE" "1"
    Set-DefaultEnvironment "TOKENIZERS_PARALLELISM" "false"
    Set-DefaultEnvironment "TMPDIR" $TempDir
    Set-DefaultEnvironment "TMP" $TempDir
    Set-DefaultEnvironment "TEMP" $TempDir
}

function Get-RuntimePython {
    param([string]$Component)
    return Join-Path $RuntimeDir "$Component\Scripts\python.exe"
}

function Get-PidPath {
    param([string]$Component)
    return Join-Path $RunDir "$Component.pid"
}

function Get-RawTrackedProcessId {
    param([string]$Component)
    $pidPath = Get-PidPath $Component
    if (-not (Test-Path $pidPath)) { return $null }
    try {
        $trackedProcessId = [int](Get-Content -Path $pidPath -Raw).Trim()
        if ($trackedProcessId -le 0) { throw "Invalid process id" }
        return $trackedProcessId
    } catch {
        Remove-Item $pidPath -Force -ErrorAction SilentlyContinue
        return $null
    }
}

function Test-ProcessIdentity {
    param([string]$Component, [int]$ProcessId)
    $apiPython = Get-RuntimePython "api"
    if (Test-Path $apiPython) {
        & $apiPython $ServiceHelper matches $Component $ProcessId | Out-Null
        return $LASTEXITCODE -eq 0
    }
    try {
        Get-Process -Id $ProcessId -ErrorAction Stop | Out-Null
        $details = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
        if ($null -eq $details -or [string]::IsNullOrWhiteSpace($details.CommandLine)) { return $false }
        $expected = if ($Component -eq "api") { "audio_intel.api:app" } else { "audio_intel.worker $Component" }
        return $details.CommandLine -like "*$expected*"
    } catch {
        return $false
    }
}

function Get-TrackedProcess {
    param([string]$Component)
    $trackedProcessId = Get-RawTrackedProcessId $Component
    if ($null -eq $trackedProcessId) { return $null }
    if (-not (Test-ProcessIdentity $Component $trackedProcessId)) {
        Remove-Item (Get-PidPath $Component) -Force -ErrorAction SilentlyContinue
        return $null
    }
    try {
        return Get-Process -Id $trackedProcessId -ErrorAction Stop
    } catch {
        Remove-Item (Get-PidPath $Component) -Force -ErrorAction SilentlyContinue
        return $null
    }
}

function Test-Running {
    param([string]$Component)
    return $null -ne (Get-TrackedProcess $Component)
}

function Ensure-Ready {
    param([string]$Component)
    if ((Get-ProcessEnvironment "AUDIO_INTEL_MOCK_MODE") -eq "1" -and $Component -ne "api") {
        Ensure-Ready "api"
        return
    }
    $python = Get-RuntimePython $Component
    if (-not (Test-Path $python)) {
        & (Join-Path $RootDir "scripts\bootstrap.ps1") $Component
    }
    $frontendReady = (Test-Path (Join-Path $FrontendDir "index.html")) -and
        (Test-Path (Join-Path $FrontendDir "docs-assets\swagger-ui-bundle.js")) -and
        (Test-Path (Join-Path $FrontendDir "docs-assets\swagger-ui.css"))
    if ($Component -eq "api" -and -not $frontendReady) {
        & (Join-Path $RootDir "scripts\bootstrap.ps1") "api"
        $frontendReady = (Test-Path (Join-Path $FrontendDir "index.html")) -and
            (Test-Path (Join-Path $FrontendDir "docs-assets\swagger-ui-bundle.js")) -and
            (Test-Path (Join-Path $FrontendDir "docs-assets\swagger-ui.css"))
        if (-not $frontendReady) { throw "Configured frontend directory is incomplete: $FrontendDir" }
    }
    if ((Get-ProcessEnvironment "AUDIO_INTEL_MOCK_MODE") -ne "1" -and ($Component -eq "asr" -or $Component -eq "tts")) {
        & (Get-RuntimePython $Component) -c "from audio_intel.config import settings; from audio_intel.model_registry import target_ready; raise SystemExit(0 if target_ready(settings.models_dir, '$Component') else 1)"
        $modelsReady = $LASTEXITCODE -eq 0
        $alignerReady = $Component -ne "tts" -or (Test-Path (Get-RuntimePython "aligner"))
        if (-not $modelsReady -or -not $alignerReady) {
            & (Join-Path $RootDir "scripts\bootstrap.ps1") $Component
        }
    }
}

function Write-RecentErrors {
    param([string]$Component)
    foreach ($path in @((Join-Path $LogDir "$Component.log"), (Join-Path $LogDir "$Component.error.log"))) {
        if (Test-Path $path) {
            Write-Host "--- $path"
            Get-Content $path -Tail 30 -ErrorAction SilentlyContinue
        }
    }
}

function Wait-ComponentReady {
    param([string]$Component, [int]$ProcessId)
    $apiPython = Get-RuntimePython "api"
    if ($Component -eq "api") {
        & $apiPython $ServiceHelper wait-api $ProcessId (Get-ProcessEnvironment "AUDIO_INTEL_HOST") (Get-ProcessEnvironment "AUDIO_INTEL_PORT") $StartTimeoutSeconds (Get-ProcessEnvironment "AUDIO_INTEL_PROTOCOL")
    } else {
        & $apiPython $ServiceHelper wait-worker $Component $ProcessId $StartTimeoutSeconds
    }
    if ($LASTEXITCODE -ne 0) { throw "$Component did not become ready" }
}

function Invoke-ProcessCleanup {
    param([string]$Component, [int]$ProcessId)
    $apiPython = Get-RuntimePython "api"
    if (Test-Path $apiPython) {
        & $apiPython $ServiceHelper cleanup $Component $ProcessId
        return $LASTEXITCODE -eq 0
    }
    if (-not (Test-ProcessIdentity $Component $ProcessId)) { return $true }
    & taskkill.exe /PID $ProcessId /T /F | Out-Null
    return $LASTEXITCODE -eq 0 -or -not (Test-ProcessIdentity $Component $ProcessId)
}

function Start-One {
    param([string]$Component)
    $existing = Get-TrackedProcess $Component
    if ($null -ne $existing) {
        Write-Host "$Component already running (pid $($existing.Id))"
        return $false
    }

    $python = if ((Get-ProcessEnvironment "AUDIO_INTEL_MOCK_MODE") -eq "1" -and $Component -ne "api") { Get-RuntimePython "api" } else { Get-RuntimePython $Component }
    $arguments = if ($Component -eq "api") {
        $apiArguments = @("-m", "uvicorn", "audio_intel.api:app", "--host", (Get-ProcessEnvironment "AUDIO_INTEL_HOST"), "--port", (Get-ProcessEnvironment "AUDIO_INTEL_PORT"))
        if ((Get-ProcessEnvironment "AUDIO_INTEL_PROTOCOL") -eq "https") {
            $certFile = '"{0}"' -f (Get-ProcessEnvironment "AUDIO_INTEL_TLS_CERT_FILE")
            $keyFile = '"{0}"' -f (Get-ProcessEnvironment "AUDIO_INTEL_TLS_KEY_FILE")
            $apiArguments += @("--ssl-certfile", $certFile, "--ssl-keyfile", $keyFile)
        }
        $apiArguments
    } else {
        @("-m", "audio_intel.worker", $Component)
    }
    $outputLog = Join-Path $LogDir "$Component.log"
    $errorLog = Join-Path $LogDir "$Component.error.log"
    $process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $RootDir -RedirectStandardOutput $outputLog -RedirectStandardError $errorLog -WindowStyle Hidden -PassThru
    Set-Content -Path (Get-PidPath $Component) -Value $process.Id -Encoding ASCII
    try {
        Wait-ComponentReady $Component $process.Id
    } catch {
        $cleanupSucceeded = Invoke-ProcessCleanup $Component $process.Id
        if ($cleanupSucceeded) { Remove-Item (Get-PidPath $Component) -Force -ErrorAction SilentlyContinue }
        Write-RecentErrors $Component
        if (-not $cleanupSucceeded) { throw "$Component failed to start and its process tree could not be cleaned completely; see $errorLog" }
        throw "$Component failed to start; see $errorLog"
    }
    Write-Host "started $Component (pid $($process.Id))"
    return $true
}

function Stop-One {
    param([string]$Component)
    $pidPath = Get-PidPath $Component
    $trackedProcessId = Get-RawTrackedProcessId $Component
    if ($null -eq $trackedProcessId) {
        if ($Component -eq "asr" -or $Component -eq "tts") {
            if (-not (Invoke-ProcessCleanup $Component 0)) { throw "failed to clean stale $Component executor" }
        }
        Remove-Item $pidPath -Force -ErrorAction SilentlyContinue
        Write-Host "$Component is not running"
        return
    }

    $wasRunning = Test-ProcessIdentity $Component $trackedProcessId
    if (-not (Invoke-ProcessCleanup $Component $trackedProcessId)) {
        throw "failed to stop $Component completely"
    }
    if (Test-ProcessIdentity $Component $trackedProcessId) {
        throw "failed to stop $Component completely"
    }
    Remove-Item $pidPath -Force -ErrorAction SilentlyContinue
    if ($wasRunning) { Write-Host "stopped $Component" } else { Write-Host "$Component is not running" }
}

function Get-StartTargets {
    if ($Target -eq "all") { return @("api", "asr", "tts") }
    if ($Target -eq "api") { return @("api") }
    return @("api", $Target)
}

function Get-StopTargets {
    if ($Target -eq "all") { return @("tts", "asr", "api") }
    return @($Target)
}

function Set-EnabledServices {
    if (-not [string]::IsNullOrWhiteSpace((Get-ProcessEnvironment "AUDIO_INTEL_SERVICES"))) { return }
    if ($Target -eq "asr") {
        Set-ProcessEnvironment "AUDIO_INTEL_SERVICES" $(if (Test-Running "tts") { "asr,tts" } else { "asr" })
    } elseif ($Target -eq "tts") {
        Set-ProcessEnvironment "AUDIO_INTEL_SERVICES" $(if (Test-Running "asr") { "asr,tts" } else { "tts" })
    } else {
        Set-ProcessEnvironment "AUDIO_INTEL_SERVICES" "asr,tts"
    }
}

function Invoke-Preflight {
    foreach ($component in (Get-StartTargets)) { Ensure-Ready $component }
    $apiPython = Get-RuntimePython "api"
    & $apiPython (Join-Path $RootDir "scripts\setup_local_tls.py") validate-config
    if ($LASTEXITCODE -ne 0) { throw "TLS configuration validation failed" }
}

function Invoke-StopTargets {
    $failures = @()
    foreach ($component in (Get-StopTargets)) {
        try {
            Stop-One $component
        } catch {
            $failures += "$component`: $($_.Exception.Message)"
            Write-Error $failures[-1] -ErrorAction Continue
        }
    }
    if ($failures.Count -gt 0) { throw "One or more services failed to stop: $($failures -join '; ')" }
}

function Invoke-StartTargets {
    param([bool]$RunPreflight = $true)
    Set-EnabledServices
    if ($RunPreflight) { Invoke-Preflight }
    $started = @()
    $preserveApi = ($Target -eq "asr" -or $Target -eq "tts") -and (Test-Running "api")
    if ($preserveApi) { Stop-One "api" }
    try {
        foreach ($component in (Get-StartTargets)) {
            if (Start-One $component) { $started += $component }
        }
    } catch {
        $startError = $_
        [array]::Reverse($started)
        foreach ($component in $started) {
            if ($component -eq "api" -and $preserveApi) { continue }
            try { Stop-One $component } catch { Write-Error $_ -ErrorAction Continue }
        }
        throw $startError
    }
    Write-Host "Sandevistan-Audio: $((Get-ProcessEnvironment 'AUDIO_INTEL_PROTOCOL'))://127.0.0.1:$((Get-ProcessEnvironment 'AUDIO_INTEL_PORT'))"
}

Initialize-Environment

if ($Action -eq "tls") {
    if ($Target -notin @("create", "renew", "fingerprint")) { throw "Usage: service.cmd tls {create|renew|fingerprint} [--host HOST ...]" }
    $python = Get-RuntimePython "api"
    if (-not (Test-Path $python)) {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($null -eq $pythonCommand) { throw "Python 3 is required to run the TLS helper." }
        $python = $pythonCommand.Source
    }
    & $python (Join-Path $RootDir "scripts\setup_local_tls.py") $Target @ExtraArgs
    exit $LASTEXITCODE
}

if ($Target -notin @("all", "api", "asr", "tts")) { throw "Target must be all, api, asr, or tts" }

switch ($Action) {
    "start" { Invoke-StartTargets }
    "stop" { Invoke-StopTargets }
    "restart" {
        Invoke-Preflight
        Invoke-StopTargets
        Invoke-StartTargets $false
    }
    "status" {
        foreach ($component in @("api", "asr", "tts")) {
            $process = Get-TrackedProcess $component
            if ($null -eq $process) {
                Write-Host "$component`: stopped"
            } elseif ($component -eq "api") {
                $endpoint = & (Get-RuntimePython "api") $ServiceHelper endpoint $process.Id
                if ($LASTEXITCODE -ne 0) { $endpoint = "protocol unknown" }
                Write-Host "$component`: running (pid $($process.Id), $endpoint)"
            } else {
                Write-Host "$component`: running (pid $($process.Id))"
            }
        }
    }
    "logs" {
        $components = if ($Target -eq "all") { @("api", "asr", "tts") } else { @($Target) }
        $paths = @()
        foreach ($component in $components) {
            foreach ($suffix in @(".log", ".error.log")) {
                $path = Join-Path $LogDir "$component$suffix"
                if (-not (Test-Path $path)) { New-Item -ItemType File -Path $path -Force | Out-Null }
                $paths += $path
            }
        }
        Get-Content -Path $paths -Tail 120 -Wait
    }
    "setup" { & (Join-Path $RootDir "scripts\bootstrap.ps1") $Target }
    "doctor" { & (Join-Path $RootDir "scripts\doctor.ps1") }
}
