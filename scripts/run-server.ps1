[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

# Force UTF-8 for Python subprocess/text decoding on Windows.
# This avoids UnicodeDecodeError when runtime reads child process output.
[System.Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "Process")
[System.Environment]::SetEnvironmentVariable("PYTHONIOENCODING", "utf-8", "Process")
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Write-Info {
    param([string]$Message)
    Write-Host $Message
}

function Remove-LinesContaining {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Keyword
    )
    if (-not (Test-Path $Path)) {
        return
    }
    $content = Get-Content -Path $Path -ErrorAction Stop
    $updated = $content | Where-Object { $_ -notlike "*$Keyword*" }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($Path, $updated, $utf8NoBom)
}

function Convert-FileToUtf8NoBom {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path $Path)) {
        return
    }
    $text = [System.IO.File]::ReadAllText($Path)
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $text, $utf8NoBom)
}

function Resolve-DistDir {
    param([Parameter(Mandatory)][string]$ProjectDir)
    $distDir = $env:DIST_DIR
    if ([string]::IsNullOrWhiteSpace($distDir)) {
        return (Join-Path $ProjectDir "dist")
    }
    if ([System.IO.Path]::IsPathRooted($distDir)) {
        return $distDir
    }
    return (Join-Path $ProjectDir $distDir)
}

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter()][string[]]$Arguments = @(),
        [Parameter(Mandatory)][string]$ErrorMessage
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$ErrorMessage (exit code: $LASTEXITCODE)"
    }
}

function Import-DotEnv {
    param([Parameter(Mandatory)][string]$EnvFile)
    if (-not (Test-Path $EnvFile)) {
        throw "Environment file not found: $EnvFile"
    }

    Write-Info "Loading environment variables from $EnvFile"
    foreach ($line in (Get-Content -Path $EnvFile -ErrorAction Stop)) {
        $trimmed = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed.StartsWith("#")) {
            continue
        }
        $eqIndex = $trimmed.IndexOf("=")
        if ($eqIndex -le 0) {
            continue
        }
        $key = $trimmed.Substring(0, $eqIndex).Trim()
        $value = $trimmed.Substring($eqIndex + 1).Trim().Trim('"').Trim("'")
        [System.Environment]::SetEnvironmentVariable($key, $value, "Process")
    }
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$ServerDir = Join-Path $ProjectDir "server"
$EnvFile = Join-Path $ServerDir ".env"

Import-DotEnv -EnvFile $EnvFile

Push-Location $ProjectDir
try {
    Invoke-CheckedCommand -FilePath "git" -Arguments @("submodule", "update", "--init", "--recursive") -ErrorMessage "git submodule update --init failed"
    Invoke-CheckedCommand -FilePath "git" -Arguments @("submodule", "update", "--remote", "--recursive") -ErrorMessage "git submodule update --remote failed"

    Remove-LinesContaining -Path (Join-Path $ProjectDir "applications\lowcode_agent\pyproject.toml") -Keyword "openjiuwen-runtime-service"
    Remove-LinesContaining -Path (Join-Path $ProjectDir "management\pyproject.toml") -Keyword "openjiuwen-runtime-foundation"
    Remove-LinesContaining -Path (Join-Path $ProjectDir "server\pyproject.toml") -Keyword "openjiuwen-runtime-management"
    Convert-FileToUtf8NoBom -Path (Join-Path $ProjectDir "applications\lowcode_agent\pyproject.toml")
    Convert-FileToUtf8NoBom -Path (Join-Path $ProjectDir "management\pyproject.toml")
    Convert-FileToUtf8NoBom -Path (Join-Path $ProjectDir "server\pyproject.toml")
    $StudioBackendDir = Join-Path $ProjectDir "agent-studio\backend"
    if (Test-Path $StudioBackendDir) {
        Convert-FileToUtf8NoBom -Path (Join-Path $StudioBackendDir "pyproject.toml")
    }

    $FinalDistDir = Resolve-DistDir -ProjectDir $ProjectDir
    Write-Info "Final dist dir: $FinalDistDir"
    if (Test-Path $FinalDistDir) {
        Remove-Item -Path $FinalDistDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    New-Item -Path $FinalDistDir -ItemType Directory -Force | Out-Null

    $UvExtraArgs = @()
    if (-not [string]::IsNullOrWhiteSpace($env:UV_EXTRA_ARGS)) {
        $UvExtraArgs = $env:UV_EXTRA_ARGS -split "\s+"
    }

    if (Test-Path $StudioBackendDir) {
        Push-Location $StudioBackendDir
        try {
            Invoke-CheckedCommand -FilePath "uv" -Arguments (@("sync") + $UvExtraArgs) -ErrorMessage "uv sync failed in agent-studio/backend"
            if (Test-Path "dist") { Remove-Item "dist" -Recurse -Force -ErrorAction SilentlyContinue }
            Invoke-CheckedCommand -FilePath "uv" -Arguments (@("build", "--out-dir", $FinalDistDir) + $UvExtraArgs) -ErrorMessage "uv build failed in agent-studio/backend"
        } finally { Pop-Location }
    } else {
        Write-Info "agent-studio/backend not found, use openjiuwen_studio from package index."
    }

    $AgentCoreDir = Join-Path $ProjectDir "..\agent-core"
    if (Test-Path $AgentCoreDir) {
        Push-Location $AgentCoreDir
        try {
            Invoke-CheckedCommand -FilePath "uv" -Arguments (@("sync") + $UvExtraArgs) -ErrorMessage "uv sync failed in agent-core"
            if (Test-Path "dist") { Remove-Item "dist" -Recurse -Force -ErrorAction SilentlyContinue }
            Invoke-CheckedCommand -FilePath "uv" -Arguments (@("build", "--out-dir", $FinalDistDir) + $UvExtraArgs) -ErrorMessage "uv build failed in agent-core"
        } finally { Pop-Location }
    }

    Push-Location (Join-Path $ProjectDir "applications\lowcode_agent")
    try {
        Invoke-CheckedCommand -FilePath "uv" -Arguments (@("sync") + $UvExtraArgs) -ErrorMessage "uv sync failed in applications/lowcode_agent"
        if (Test-Path "dist") { Remove-Item "dist" -Recurse -Force -ErrorAction SilentlyContinue }
        Invoke-CheckedCommand -FilePath "uv" -Arguments (@("build", "--out-dir", $FinalDistDir) + $UvExtraArgs) -ErrorMessage "uv build failed in applications/lowcode_agent"
    } finally { Pop-Location }

    Push-Location (Join-Path $ProjectDir "service")
    try {
        Invoke-CheckedCommand -FilePath "uv" -Arguments (@("sync") + $UvExtraArgs) -ErrorMessage "uv sync failed in service"
        if (Test-Path "dist") { Remove-Item "dist" -Recurse -Force -ErrorAction SilentlyContinue }
        Invoke-CheckedCommand -FilePath "uv" -Arguments (@("build", "--out-dir", $FinalDistDir) + $UvExtraArgs) -ErrorMessage "uv build failed in service"
    } finally { Pop-Location }

    Push-Location $ServerDir
    try {
        Invoke-CheckedCommand -FilePath "uv" -Arguments (@("sync") + $UvExtraArgs) -ErrorMessage "uv sync failed in server"
        $VenvPython = Join-Path $ServerDir ".venv\Scripts\python.exe"
        if (-not (Test-Path $VenvPython)) {
            throw "Runtime python not found: $VenvPython"
        }
        Invoke-CheckedCommand -FilePath "uv" -Arguments (@("pip", "install", "-e", "..\management") + $UvExtraArgs) -ErrorMessage "uv pip install management failed"
        Invoke-CheckedCommand -FilePath "uv" -Arguments (@("pip", "install", "-e", "..\foundation") + $UvExtraArgs) -ErrorMessage "uv pip install foundation failed"

        & $VenvPython -m openjiuwen_runtime.server.main
        if ($LASTEXITCODE -ne 0) {
            throw "Runtime server exited with non-zero code: $LASTEXITCODE"
        }
    } finally { Pop-Location }
} finally {
    Pop-Location
}
