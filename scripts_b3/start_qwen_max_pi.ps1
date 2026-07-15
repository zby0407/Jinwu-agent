[CmdletBinding()]
param(
    [Parameter()]
    [ValidateSet(
        "qwen3.7-max-2026-06-08",
        "qwen3.7-plus-2026-05-26",
        "qwen3.6-flash-2026-04-16"
    )]
    [string]$ModelId = "qwen3.7-max-2026-06-08",

    [Parameter()]
    [string]$BaseUrl = "https://dashscope.aliyuncs.com/compatible-mode/v1"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$modelRoute = "dashscope/$ModelId"
$canonicalPath = "/compatible-mode/v1"
$sharedHosts = @(
    "dashscope.aliyuncs.com",
    "dashscope-us.aliyuncs.com",
    "dashscope-intl.aliyuncs.com"
)

try {
    $endpoint = [System.Uri]::new($BaseUrl, [System.UriKind]::Absolute)
}
catch {
    throw "BaseUrl is not a valid absolute URL."
}

$officialHost =
    $sharedHosts -contains $endpoint.DnsSafeHost -or
    $endpoint.DnsSafeHost.EndsWith(".maas.aliyuncs.com", [System.StringComparison]::OrdinalIgnoreCase)
$validPath = $endpoint.AbsolutePath -eq $canonicalPath -or $endpoint.AbsolutePath -eq "$canonicalPath/"
if (
    $endpoint.Scheme -ne "https" -or
    -not $officialHost -or
    -not $validPath -or
    -not [string]::IsNullOrEmpty($endpoint.UserInfo) -or
    -not [string]::IsNullOrEmpty($endpoint.Query) -or
    -not [string]::IsNullOrEmpty($endpoint.Fragment) -or
    (-not $endpoint.IsDefaultPort -and $endpoint.Port -ne 443)
) {
    throw "BaseUrl must be an official Alibaba Cloud HTTPS endpoint with path /compatible-mode/v1 and no credentials, query, or fragment."
}

$canonicalBaseUrl = "https://$($endpoint.DnsSafeHost)$canonicalPath"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$managedEnvironment = @(
    "DASHSCOPE_API_KEY",
    "QWEN_API_KEY",
    "B3_AGENT_MODEL",
    "B3_QWEN_MODEL",
    "B3_QWEN_BASE_URL",
    "B3_QWEN_TEMPERATURE",
    "B3_QWEN_ENABLED",
    "B3_RUNTIME_ROOT"
)
$originalEnvironment = @{}
foreach ($name in $managedEnvironment) {
    $originalEnvironment[$name] = [System.Environment]::GetEnvironmentVariable($name, "Process")
}

$secureKey = $null
$plainKey = $null
$keyPointer = [System.IntPtr]::Zero
$locationPushed = $false
try {
    $secureKey = Read-Host "Enter the Alibaba Cloud Model Studio API key (input is hidden)" -AsSecureString
    if ($secureKey.Length -eq 0) {
        throw "The API key cannot be empty."
    }
    $keyPointer = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    $plainKey = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)

    [System.Environment]::SetEnvironmentVariable("DASHSCOPE_API_KEY", $plainKey, "Process")
    [System.Environment]::SetEnvironmentVariable("QWEN_API_KEY", $null, "Process")
    [System.Environment]::SetEnvironmentVariable("B3_AGENT_MODEL", $modelRoute, "Process")
    [System.Environment]::SetEnvironmentVariable("B3_QWEN_MODEL", $ModelId, "Process")
    [System.Environment]::SetEnvironmentVariable("B3_QWEN_BASE_URL", $canonicalBaseUrl, "Process")
    [System.Environment]::SetEnvironmentVariable("B3_QWEN_TEMPERATURE", "0.2", "Process")
    [System.Environment]::SetEnvironmentVariable("B3_QWEN_ENABLED", "1", "Process")
    if (Test-Path -LiteralPath (Join-Path $projectRoot "MANIFEST.json")) {
        [System.Environment]::SetEnvironmentVariable(
            "B3_RUNTIME_ROOT",
            (Join-Path $projectRoot "runtime"),
            "Process"
        )
    }

    $piCommand = Get-Command pi -ErrorAction Stop
    Push-Location $projectRoot
    $locationPushed = $true
    & $piCommand.Source --model $modelRoute --thinking high
    if ($LASTEXITCODE -ne 0) {
        throw "Pi exited with code $LASTEXITCODE."
    }
}
finally {
    if ($locationPushed) {
        Pop-Location
    }
    if ($keyPointer -ne [System.IntPtr]::Zero) {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    }
    $plainKey = $null
    $secureKey = $null
    foreach ($name in $managedEnvironment) {
        [System.Environment]::SetEnvironmentVariable($name, $originalEnvironment[$name], "Process")
    }
}
