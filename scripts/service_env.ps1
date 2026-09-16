# A data-only dotenv reader. Keep its grammar in sync with service_env.sh.
# No Python runtime is needed, including before the first service.cmd setup.

function ConvertFrom-ServiceEnvValue {
    param([string]$Raw, [System.Collections.IDictionary]$Values)
    $quote = ''
    $closed = $false
    $position = 0
    $value = New-Object System.Text.StringBuilder
    if ($Raw.StartsWith('"') -or $Raw.StartsWith("'")) {
        $quote = $Raw.Substring(0, 1)
        $position = 1
    } else {
        $Raw = $Raw.TrimEnd([char[]]" `t")
    }
    while ($position -lt $Raw.Length) {
        $character = $Raw.Substring($position, 1)
        $next = if ($position + 1 -lt $Raw.Length) { $Raw.Substring($position + 1, 1) } else { '' }
        if ($quote -ne '' -and $character -eq $quote) {
            if ($Raw.Substring($position + 1) -notmatch '^[ \t]*(#.*)?$') { throw 'text after closing quote' }
            $closed = $true
            break
        }
        if ($quote -eq '') {
            if ($character -eq '#' -and ($position -eq 0 -or $Raw.Substring($position - 1, 1) -match '[ \t]')) {
                $spaces = [regex]::Match($Raw.Substring(0, $position), '[ \t]+$').Length
                $value.Length -= $spaces
                break
            }
            if ($character -eq '"' -or $character -eq "'") { throw 'quote must enclose the whole value' }
        }
        if ($quote -eq '"' -and $character -eq '\' -and $next -in @('"', '$', '\')) {
            [void]$value.Append($next)
            $position += 2
            continue
        }
        if ($quote -ne "'") {
            if ($character -eq '`' -or ($character -eq '$' -and $next -eq '(')) { throw 'command substitution is not supported' }
            if ($character -eq '$') {
                $tail = $Raw.Substring($position + 1)
                if ($next -eq '{') {
                    if ($tail -notmatch '^\{([a-zA-Z_][a-zA-Z0-9_]*)\}') { throw 'invalid variable reference' }
                } elseif ($tail -notmatch '^([a-zA-Z_][a-zA-Z0-9_]*)') {
                    [void]$value.Append('$')
                    $position++
                    continue
                }
                $reference = $Matches[1]
                $position += 1 + $Matches[0].Length
                if ($Values.Contains($reference)) { [void]$value.Append([string]$Values[$reference]) }
                continue
            }
        }
        [void]$value.Append($character)
        $position++
    }
    if ($quote -ne '' -and -not $closed) { throw 'unclosed quote' }
    return $value.ToString()
}

function Import-ServiceEnvironment {
    param([string]$Path)
    if ([Environment]::GetEnvironmentVariable('AUDIO_INTEL_LOAD_ENV', 'Process') -eq '0') {
        return 'disabled (AUDIO_INTEL_LOAD_ENV=0)'
    }
    if (-not (Test-Path -LiteralPath $Path)) { return 'default (no .env)' }
    try {
        $encoding = New-Object System.Text.UTF8Encoding($false, $true)
        $lines = [IO.File]::ReadAllLines($Path, $encoding)
    } catch {
        throw "Invalid environment file ${Path}:0 (not a readable UTF-8 file)"
    }
    $inherited = @{}
    foreach ($key in [Environment]::GetEnvironmentVariables('Process').Keys) {
        $inherited[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
    }
    $values = @{}
    foreach ($key in $inherited.Keys) { $values[$key] = $inherited[$key] }
    $assignments = @{}
    $assignmentLines = @{}
    $lineNumber = 0
    foreach ($line in $lines) {
        $lineNumber++
        $entry = $line.TrimStart([char[]]" `t")
        if ($entry -eq '' -or $entry.StartsWith('#')) { continue }
        try {
            $entry = $entry -creplace '^export[ \t]+', ''
            if ($entry -cnotmatch '^([a-zA-Z_][a-zA-Z0-9_]*)[ \t]*=[ \t]*(.*)$') { throw 'expected KEY=value' }
            $key = $Matches[1]
            $raw = $Matches[2]
            if ($key -like '_ai_*' -or $key -eq 'AUDIO_INTEL_LOAD_ENV') { throw 'reserved variable' }
            $value = ConvertFrom-ServiceEnvValue $raw $values
            if (-not $inherited.Contains($key)) {
                $values[$key] = $value
                $assignments[$key] = $value
                $assignmentLines[$key] = $lineNumber
            }
        } catch {
            # Never include the input line or value: either may contain a credential.
            throw "Invalid environment file ${Path}:$lineNumber (invalid assignment, quoting or variable reference)"
        }
    }
    foreach ($key in $assignments.Keys) {
        try { [Environment]::SetEnvironmentVariable($key, $assignments[$key], 'Process') } catch {
            throw "Invalid environment file ${Path}:$($assignmentLines[$key]) (cannot export variable)"
        }
    }
    return "$Path (external environment takes precedence)"
}
