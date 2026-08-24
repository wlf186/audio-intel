param(
    [ValidateSet("start", "stop", "restart", "status", "logs", "setup", "doctor")]
    [string]$Action = "status",
    [ValidateSet("all", "api", "asr", "tts")]
    [string]$Target = "all"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RootDir = $PSScriptRoot
$RunDir = Join-Path $RootDir "run"
$LogDir = Join-Path $RootDir "logs"
$RuntimeDir = Join-Path $RootDir ".runtime"

function Set-DefaultEnvironment {
    param([string]$Name, [string]$Value)
    $current = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($current)) {
        [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
    }
}

function Initialize-Environment {
    $directories = @($RunDir, $LogDir, (Join-Path $RootDir "data"), (Join-Path $RootDir "tmp"), (Join-Path $RootDir "cache"), (Join-Path $RootDir "models"))
    foreach ($directory in $directories) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }

    Set-DefaultEnvironment "PYTHONPATH" $RootDir
    Set-DefaultEnvironment "AUDIO_INTEL_HOST" "0.0.0.0"
    Set-DefaultEnvironment "AUDIO_INTEL_PORT" "20810"
    Set-DefaultEnvironment "AUDIO_INTEL_MOCK_MODE" "0"
    Set-DefaultEnvironment "HF_HOME" (Join-Path $RootDir "cache\huggingface")
    Set-DefaultEnvironment "HUGGINGFACE_HUB_CACHE" (Join-Path $RootDir "cache\huggingface\hub")
    Set-DefaultEnvironment "MODELSCOPE_CACHE" (Join-Path $RootDir "cache\modelscope")
    Set-DefaultEnvironment "TORCH_HOME" (Join-Path $RootDir "cache\torch")
    Set-DefaultEnvironment "XDG_CACHE_HOME" (Join-Path $RootDir "cache\xdg")
    Set-DefaultEnvironment "HF_HUB_OFFLINE" "1"
    Set-DefaultEnvironment "TRANSFORMERS_OFFLINE" "1"
    Set-DefaultEnvironment "TOKENIZERS_PARALLELISM" "false"
    $localTemp = Join-Path $RootDir "tmp"
    Set-DefaultEnvironment "TMPDIR" $localTemp
    Set-DefaultEnvironment "TMP" $localTemp
    Set-DefaultEnvironment "TEMP" $localTemp
}

function Get-RuntimePython {
    param([string]$Component)
    return Join-Path $RuntimeDir "$Component\Scripts\python.exe"
}

function Get-PidPath {
    param([string]$Component)
    return Join-Path $RunDir "$Component.pid"
}

function Get-TrackedProcess {
    param([string]$Component)
    $pidPath = Get-PidPath $Component
    if (-not (Test-Path $pidPath)) { return $null }
    try {
        $trackedPid = [int](Get-Content -Path $pidPath -Raw).Trim()
        $process = Get-Process -Id $trackedPid -ErrorAction Stop
        $details = Get-CimInstance Win32_Process -Filter "ProcessId = $trackedPid" -ErrorAction SilentlyContinue
        if ($null -ne $details -and -not [string]::IsNullOrWhiteSpace($details.CommandLine)) {
            $expected = if ($Component -eq "api") { "audio_intel.api:app" } else { "audio_intel.worker $Component" }
            if ($details.CommandLine -notlike "*$expected*") {
                Remove-Item $pidPath -Force -ErrorAction SilentlyContinue
                return $null
            }
        }
        return $process
    } catch {
        Remove-Item $pidPath -Force -ErrorAction SilentlyContinue
        return $null
    }
}

function Test-Running {
    param([string]$Component)
    return $null -ne (Get-TrackedProcess $Component)
}

function Ensure-Ready {
    param([string]$Component)
    if ($env:AUDIO_INTEL_MOCK_MODE -eq "1" -and $Component -ne "api") {
        Ensure-Ready "api"
        return
    }
    $python = Get-RuntimePython $Component
    if (-not (Test-Path $python)) {
        & (Join-Path $RootDir "scripts\bootstrap.ps1") $Component
    }
    if ($Component -eq "api" -and -not (Test-Path (Join-Path $RootDir "frontend\dist\index.html"))) {
        & (Join-Path $RootDir "scripts\bootstrap.ps1") "api"
    }
    if ($Component -eq "asr" -and $env:AUDIO_INTEL_MOCK_MODE -ne "1" -and -not (Test-Path (Join-Path $RootDir "models\Qwen3-ASR-0.6B\.complete"))) {
        & (Join-Path $RootDir "scripts\bootstrap.ps1") "asr"
    }
    if ($Component -eq "tts" -and $env:AUDIO_INTEL_MOCK_MODE -ne "1" -and -not (Test-Path (Join-Path $RootDir "models\Qwen3-TTS-12Hz-0.6B-Base\.complete"))) {
        & (Join-Path $RootDir "scripts\bootstrap.ps1") "tts"
    }
}

function Write-RecentErrors {
    param([string]$Component)
    $errorLog = Join-Path $LogDir "$Component.error.log"
    if (Test-Path $errorLog) { Get-Content $errorLog -Tail 30 -ErrorAction SilentlyContinue }
}

function Start-One {
    param([string]$Component)
    $existing = Get-TrackedProcess $Component
    if ($null -ne $existing) {
        Write-Host "$Component already running (pid $($existing.Id))"
        return
    }
    Ensure-Ready $Component
    $python = if ($env:AUDIO_INTEL_MOCK_MODE -eq "1" -and $Component -ne "api") { Get-RuntimePython "api" } else { Get-RuntimePython $Component }
    $arguments = if ($Component -eq "api") {
        @("-m", "uvicorn", "audio_intel.api:app", "--host", $env:AUDIO_INTEL_HOST, "--port", $env:AUDIO_INTEL_PORT)
    } else {
        @("-m", "audio_intel.worker", $Component)
    }
    $outputLog = Join-Path $LogDir "$Component.log"
    $errorLog = Join-Path $LogDir "$Component.error.log"
    $process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $RootDir -RedirectStandardOutput $outputLog -RedirectStandardError $errorLog -WindowStyle Hidden -PassThru
    Set-Content -Path (Get-PidPath $Component) -Value $process.Id -Encoding ASCII
    Start-Sleep -Milliseconds 700
    if ($process.HasExited) {
        Remove-Item (Get-PidPath $Component) -Force -ErrorAction SilentlyContinue
        Write-RecentErrors $Component
        throw "$Component failed to start; see $errorLog"
    }
    Write-Host "started $Component (pid $($process.Id))"
}

function Stop-One {
    param([string]$Component)
    $process = Get-TrackedProcess $Component
    $pidPath = Get-PidPath $Component
    if ($null -eq $process) {
        Remove-Item $pidPath -Force -ErrorAction SilentlyContinue
        Write-Host "$Component is not running"
        return
    }
    Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
    try { Wait-Process -Id $process.Id -Timeout 6 -ErrorAction Stop } catch { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
    Remove-Item $pidPath -Force -ErrorAction SilentlyContinue
    Write-Host "stopped $Component"
}

function Wait-ForApi {
    $url = "http://127.0.0.1:$($env:AUDIO_INTEL_PORT)/api/v1/health"
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 2 | Out-Null
            return
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    Write-RecentErrors "api"
    throw "API did not become ready at $url"
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

Initialize-Environment

switch ($Action) {
    "start" {
        if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable("AUDIO_INTEL_SERVICES", "Process"))) {
            if ($Target -eq "asr") {
                $env:AUDIO_INTEL_SERVICES = if (Test-Running "tts") { "asr,tts" } else { "asr" }
            } elseif ($Target -eq "tts") {
                $env:AUDIO_INTEL_SERVICES = if (Test-Running "asr") { "asr,tts" } else { "tts" }
            } else {
                $env:AUDIO_INTEL_SERVICES = "asr,tts"
            }
        }
        if (($Target -eq "asr" -or $Target -eq "tts") -and (Test-Running "api")) { Stop-One "api" }
        foreach ($component in (Get-StartTargets)) { Start-One $component }
        Wait-ForApi
        Write-Host "Sandevistan-Audio: http://127.0.0.1:$($env:AUDIO_INTEL_PORT)"
    }
    "stop" { foreach ($component in (Get-StopTargets)) { Stop-One $component } }
    "restart" {
        foreach ($component in (Get-StopTargets)) { Stop-One $component }
        if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable("AUDIO_INTEL_SERVICES", "Process"))) {
            if ($Target -eq "asr") {
                $env:AUDIO_INTEL_SERVICES = if (Test-Running "tts") { "asr,tts" } else { "asr" }
            } elseif ($Target -eq "tts") {
                $env:AUDIO_INTEL_SERVICES = if (Test-Running "asr") { "asr,tts" } else { "tts" }
            } else {
                $env:AUDIO_INTEL_SERVICES = "asr,tts"
            }
        }
        if (($Target -eq "asr" -or $Target -eq "tts") -and (Test-Running "api")) { Stop-One "api" }
        foreach ($component in (Get-StartTargets)) { Start-One $component }
        Wait-ForApi
    }
    "status" {
        foreach ($component in @("api", "asr", "tts")) {
            $process = Get-TrackedProcess $component
            if ($null -eq $process) { Write-Host "$component`: stopped" } else { Write-Host "$component`: running (pid $($process.Id))" }
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
    "setup" {
        & (Join-Path $RootDir "scripts\bootstrap.ps1") $Target
    }
    "doctor" {
        & (Join-Path $RootDir "scripts\doctor.ps1")
    }
}
