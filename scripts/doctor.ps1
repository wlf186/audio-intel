Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $PSScriptRoot

function Get-CommandVersion {
    param([string]$Name)
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) { return $null }
    try {
        $line = (& $command.Source --version 2>$null | Select-Object -First 1)
        if ([string]::IsNullOrWhiteSpace($line)) { return "installed" }
        return $line.Trim()
    } catch {
        return "installed (version query failed)"
    }
}

function Test-PortAvailable {
    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Any, 20810)
    try {
        $listener.Start()
        return "available"
    } catch {
        return "in use (expected if the service is running)"
    } finally {
        try { $listener.Stop() } catch { }
    }
}

$computer = Get-CimInstance Win32_ComputerSystem
$os = Get-CimInstance Win32_OperatingSystem
$rootPath = [System.IO.Path]::GetPathRoot($RootDir)
$driveName = $rootPath.TrimEnd("\").TrimEnd(":")
$drive = Get-PSDrive -Name $driveName -ErrorAction SilentlyContinue
$volume = Get-Volume -DriveLetter $driveName -ErrorAction SilentlyContinue
$longPaths = Get-ItemPropertyValue -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name "LongPathsEnabled" -ErrorAction SilentlyContinue

$gpu = $null
$nvidia = Get-Command "nvidia-smi.exe" -ErrorAction SilentlyContinue
if ($null -ne $nvidia) {
    try {
        $gpu = (& $nvidia.Source --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits 2>$null) -join "`n"
    } catch {
        $gpu = "unavailable: $($_.Exception.Message)"
    }
}

$runtimeStatus = [ordered]@{}
foreach ($name in @("api", "asr", "tts")) {
    $runtimeStatus[$name] = Test-Path (Join-Path $RootDir ".runtime\$name\Scripts\python.exe")
}
$modelStatus = [ordered]@{}
foreach ($name in @("Qwen3-ASR-0.6B", "Qwen3-ForcedAligner-0.6B", "FSMN-VAD", "CAM++", "Qwen3-TTS-12Hz-0.6B-Base", "Qwen3-TTS-12Hz-0.6B-CustomVoice")) {
    $modelStatus[$name] = Test-Path (Join-Path $RootDir "models\$name\.complete")
}

$report = [ordered]@{
    root = $RootDir
    platform = [ordered]@{
        caption = $os.Caption
        version = $os.Version
        architecture = $os.OSArchitecture
        powershell = $PSVersionTable.PSVersion.ToString()
    }
    supported_platform = [Environment]::Is64BitOperatingSystem -and $env:PROCESSOR_ARCHITECTURE -eq "AMD64"
    tools = [ordered]@{
        git = Get-CommandVersion "git.exe"
        node = Get-CommandVersion "node.exe"
        npm = Get-CommandVersion "npm.cmd"
        corepack = Get-CommandVersion "corepack.cmd"
    }
    memory_total_bytes = [int64]$computer.TotalPhysicalMemory
    disk_free_bytes = if ($null -eq $drive) { $null } else { [int64]$drive.Free }
    filesystem = if ($null -eq $volume) { $null } else { $volume.FileSystem }
    path_length = $RootDir.Length
    path_contains_onedrive = $RootDir -like "*OneDrive*"
    long_paths_enabled = $longPaths -eq 1
    download_proxy_configured = -not [string]::IsNullOrWhiteSpace($env:HTTP_PROXY) -or -not [string]::IsNullOrWhiteSpace($env:HTTPS_PROXY)
    port_20810 = Test-PortAvailable
    nvidia_smi = $null -ne $nvidia
    gpu = $gpu
    cuda_13_minimum_driver = "580"
    runtime_environments = $runtimeStatus
    frontend = Test-Path (Join-Path $RootDir "frontend\dist\index.html")
    models = $modelStatus
}

$report | ConvertTo-Json -Depth 6
