$ErrorActionPreference = "Stop"

$jwSource = if ([string]::IsNullOrWhiteSpace($env:JW_INSTALL_SOURCE)) {
    "https://github.com/zby0407/Jinwu-agent/archive/refs/heads/main.zip"
} else {
    $env:JW_INSTALL_SOURCE
}
$webUiReady = $false
$managedSource = $false
$installComplete = $false
$previousSourceDir = $null
$sourceDir = $null

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

function Install-JwWebUi([string]$SourceDir) {
    if ($env:JW_SKIP_WEBUI_BUILD -eq "1") {
        return
    }

    $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
    $npmCommand = Get-Command npm -ErrorAction SilentlyContinue
    if ($null -eq $nodeCommand -or $null -eq $npmCommand) {
        Write-JwStatus "Node.js 20+ and npm were not found; installing CLI/TUI without WebUI."
        return
    }

    $nodeMajor = [int](& node -p "process.versions.node.split('.')[0]")
    if ($LASTEXITCODE -ne 0 -or $nodeMajor -lt 20) {
        Write-JwStatus "Node.js 20+ is required for WebUI; found $(& node --version)."
        return
    }

    $webUiDir = Join-Path $SourceDir "webui"
    Write-JwStatus "building the JW WebUI..."
    & npm --prefix $webUiDir ci
    if ($LASTEXITCODE -ne 0) {
        throw "WebUI dependency installation failed with exit code $LASTEXITCODE."
    }
    & npm --prefix $webUiDir run build
    if ($LASTEXITCODE -ne 0) {
        throw "WebUI build failed with exit code $LASTEXITCODE."
    }
    if ($script:managedSource) {
        foreach ($buildOnlyDir in @("node_modules", ".next")) {
            $buildOnlyPath = Join-Path $webUiDir $buildOnlyDir
            if (Test-Path -LiteralPath $buildOnlyPath) {
                Remove-Item -LiteralPath $buildOnlyPath -Recurse -Force
            }
        }
    }
    $script:webUiReady = $true
}

function Restore-JwSource {
    if (-not $script:managedSource -or $script:installComplete) {
        return
    }
    if (Test-Path -LiteralPath $script:sourceDir) {
        Remove-Item -LiteralPath $script:sourceDir -Recurse -Force
    }
    if (
        -not [string]::IsNullOrWhiteSpace($script:previousSourceDir) -and
        (Test-Path -LiteralPath $script:previousSourceDir)
    ) {
        Move-Item -LiteralPath $script:previousSourceDir -Destination $script:sourceDir
    }
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

if (Test-Path -LiteralPath $jwSource -PathType Container) {
    $sourceDir = (Resolve-Path -LiteralPath $jwSource).Path
    Install-JwWebUi $sourceDir
} else {
    $installRoot = if (-not [string]::IsNullOrWhiteSpace($env:JW_INSTALL_DIR)) {
        $env:JW_INSTALL_DIR
    } elseif (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        Join-Path $env:LOCALAPPDATA "jw-agent"
    } else {
        Join-Path $env:USERPROFILE ".local\share\jw-agent"
    }

    New-Item -ItemType Directory -Force -Path $installRoot | Out-Null
    $stageDir = Join-Path $installRoot ("download-" + [guid]::NewGuid().ToString("N"))
    $archivePath = Join-Path $stageDir "jw.zip"
    $unpackDir = Join-Path $stageDir "unpacked"
    New-Item -ItemType Directory -Force -Path $unpackDir | Out-Null

    try {
        Write-JwStatus "downloading JW from $jwSource..."
        Invoke-WebRequest -Uri $jwSource -OutFile $archivePath
        Expand-Archive -LiteralPath $archivePath -DestinationPath $unpackDir
        $extractedSource = Get-ChildItem -LiteralPath $unpackDir -Directory |
            Select-Object -First 1
        if ($null -eq $extractedSource) {
            throw "The downloaded JW archive did not contain a source directory."
        }

        $sourceDir = Join-Path $installRoot "source"
        $previousSourceDir = Join-Path $installRoot "source.previous"
        if (Test-Path -LiteralPath $previousSourceDir) {
            Remove-Item -LiteralPath $previousSourceDir -Recurse -Force
        }
        if (Test-Path -LiteralPath $sourceDir) {
            Move-Item -LiteralPath $sourceDir -Destination $previousSourceDir
        }
        Move-Item -LiteralPath $extractedSource.FullName -Destination $sourceDir
        $managedSource = $true

        # Keep the complete repository because research contracts and WebUI
        # files are runtime resources. Restore the previous source on failure.
        Install-JwWebUi $sourceDir
    } catch {
        Restore-JwSource
        throw
    } finally {
        if (Test-Path -LiteralPath $stageDir) {
            Remove-Item -LiteralPath $stageDir -Recurse -Force
        }
    }
}

Write-JwStatus "installing JW from $sourceDir..."
try {
    & $uvPath tool install --reinstall --editable $sourceDir
    if ($LASTEXITCODE -ne 0) {
        throw "JW installation failed with exit code $LASTEXITCODE."
    }
    $installComplete = $true
} catch {
    Restore-JwSource
    throw
}
if (
    -not [string]::IsNullOrWhiteSpace($previousSourceDir) -and
    (Test-Path -LiteralPath $previousSourceDir)
) {
    Remove-Item -LiteralPath $previousSourceDir -Recurse -Force
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
if ($webUiReady) {
    Write-JwStatus "WebUI is ready."
}
if ($pathEntries -contains $jwBinDir) {
    Write-JwStatus "run: jw onboard"
} else {
    Write-JwStatus "open a new terminal, then run: jw onboard"
    Write-JwStatus "for this PowerShell session only, run: `$env:PATH = `"$jwBinDir;`$env:PATH`""
}
