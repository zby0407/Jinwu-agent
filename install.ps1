$ErrorActionPreference = "Stop"

$jwSource = if ([string]::IsNullOrWhiteSpace($env:JW_INSTALL_SOURCE)) {
    "https://github.com/zby0407/Jinwu-agent/archive/refs/heads/main.tar.gz"
} else {
    $env:JW_INSTALL_SOURCE
}

function Write-JwStatus([string]$Message) {
    Write-Host "[jw] $Message"
}

function Find-Uv {
    $command = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }

    $candidates = @()
    if (-not [string]::IsNullOrWhiteSpace($env:UV_INSTALL_DIR)) {
        $candidates += Join-Path $env:UV_INSTALL_DIR "uv.exe"
    }
    if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
        $candidates += Join-Path $env:USERPROFILE ".local\bin\uv.exe"
        $candidates += Join-Path $env:USERPROFILE ".cargo\bin\uv.exe"
    }

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }

    return $null
}

$uvPath = Find-Uv
if ([string]::IsNullOrWhiteSpace($uvPath)) {
    Write-JwStatus "uv was not found; installing it with the official Astral installer..."
    Invoke-RestMethod "https://astral.sh/uv/install.ps1" | Invoke-Expression
    $uvPath = Find-Uv
}

if ([string]::IsNullOrWhiteSpace($uvPath)) {
    throw "uv installation completed, but uv.exe could not be found."
}

Write-JwStatus "installing JW from $jwSource..."
& $uvPath tool install --reinstall $jwSource
if ($LASTEXITCODE -ne 0) {
    throw "JW installation failed with exit code $LASTEXITCODE."
}

# Persist uv's tool directory on PATH when possible. This is non-fatal because
# JW has already been installed successfully.
& $uvPath tool update-shell *> $null
$toolPathExitCode = $LASTEXITCODE
if ($toolPathExitCode -ne 0) {
    Write-Warning "JW was installed, but the tool directory could not be added to PATH automatically."
}

$jwBinDir = (& $uvPath tool dir --bin | Select-Object -Last 1).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "JW was installed, but its executable directory could not be determined."
}

$pathEntries = $env:PATH -split [IO.Path]::PathSeparator
Write-JwStatus "installation complete."
if ($pathEntries -contains $jwBinDir) {
    Write-JwStatus "run: jw onboard"
} else {
    Write-JwStatus "open a new terminal, then run: jw onboard"
    Write-JwStatus "for this PowerShell session only, run: `$env:PATH = `"$jwBinDir;`$env:PATH`""
}
