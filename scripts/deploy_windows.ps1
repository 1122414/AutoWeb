[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$DpCliPath = "",
    [string]$BrowserPath = "",
    [string]$BailianModel = "",
    [string]$BailianApiKey = "",
    [string]$BailianBaseUrl = "",
    [switch]$NoSystemSitePackages,
    [switch]$AllowNetworkDependencyInstall,
    [switch]$UpgradePip,
    [switch]$RecreateVenv,
    [switch]$SkipDependencyInstall,
    [switch]$SkipBrowserCheck
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-Checked {
    param([string]$Description, [scriptblock]$Command)
    Write-Host "`n==> $Description" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE"
    }
}

function Set-EnvEntry {
    param([string]$Path, [string]$Name, [string]$Value)
    $escapedName = [regex]::Escape($Name)
    $text = if (Test-Path -LiteralPath $Path) {
        [System.IO.File]::ReadAllText($Path)
    } else {
        ""
    }
    $line = "$Name=$Value"
    if ($text -match "(?m)^$escapedName=") {
        $text = [regex]::Replace($text, "(?m)^$escapedName=.*$", $line)
    } else {
        if ($text.Length -gt 0 -and -not $text.EndsWith("`n")) {
            $text += [Environment]::NewLine
        }
        $text += "$line$([Environment]::NewLine)"
    }
    [System.IO.File]::WriteAllText($Path, $text, [System.Text.UTF8Encoding]::new($false))
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $repoRoot ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$envFile = Join-Path $repoRoot ".env"
$envTemplate = Join-Path $repoRoot "deploy.env.example"

if ([string]::IsNullOrWhiteSpace($DpCliPath)) {
    $DpCliPath = Join-Path (Split-Path -Parent $repoRoot) "drissionpage-cli"
}
$DpCliPath = [System.IO.Path]::GetFullPath($DpCliPath)

if (-not (Test-Path -LiteralPath (Join-Path $DpCliPath "dp_cli\__main__.py"))) {
    throw "drissionpage-cli was not found at $DpCliPath. Use -DpCliPath to point to its checkout."
}

$providedModelConfig = @(
    @($BailianModel, $BailianApiKey, $BailianBaseUrl) |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
)
if ($providedModelConfig.Count -gt 0 -and $providedModelConfig.Count -ne 3) {
    throw "BailianModel, BailianApiKey, and BailianBaseUrl must be provided together, or all omitted to reuse .env."
}

Invoke-Checked "Check Python version" {
    & $Python -c "import sys; assert sys.version_info >= (3, 11), sys.version; print(sys.version)"
}

if ($RecreateVenv -and (Test-Path -LiteralPath $venvPath)) {
    Write-Host "Removing the existing project virtual environment." -ForegroundColor Yellow
    Remove-Item -LiteralPath $venvPath -Recurse -Force
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    Invoke-Checked "Create virtual environment $venvPath" {
        $venvArguments = @("-m", "venv")
        if (-not $NoSystemSitePackages) {
            # Reuse a known-good local Python installation when the package
            # index is unavailable. Missing project requirements are still
            # installed by the next step.
            $venvArguments += "--system-site-packages"
        }
        $venvArguments += $venvPath
        & $Python @venvArguments
    }
}

if ($NoSystemSitePackages -and -not $AllowNetworkDependencyInstall -and -not $SkipDependencyInstall) {
    throw "-NoSystemSitePackages requires -AllowNetworkDependencyInstall, because a clean environment has no inherited packages."
}

if ($AllowNetworkDependencyInstall -and -not $SkipDependencyInstall) {
    if ($UpgradePip) {
        Invoke-Checked "Upgrade pip" {
            & $venvPython -m pip install --disable-pip-version-check --upgrade pip
        }
    }
    Invoke-Checked "Install pinned AutoWeb dependencies" {
        $installArguments = @("-m", "pip", "install", "--disable-pip-version-check")
        $installArguments += @("-r", (Join-Path $repoRoot "requirements.txt"))
        & $venvPython @installArguments
    }
    Invoke-Checked "Check installed dependency consistency" {
        & $venvPython -m pip check
    }
} elseif (-not $SkipDependencyInstall) {
    Write-Host "Using inherited packages. Add -AllowNetworkDependencyInstall to install every locked development dependency." -ForegroundColor Yellow
}

if (-not (Test-Path -LiteralPath $envFile)) {
    Copy-Item -LiteralPath $envTemplate -Destination $envFile
    Write-Host "Created .env from deploy.env.example." -ForegroundColor Yellow
}

# Bind AutoWeb and dp_cli to the same virtual environment, not ambient PATH.
Set-EnvEntry -Path $envFile -Name "DPCLI_ENABLED" -Value "True"
Set-EnvEntry -Path $envFile -Name "DPCLI_CWD" -Value $DpCliPath
Set-EnvEntry -Path $envFile -Name "DPCLI_PYTHON" -Value $venvPython
Set-EnvEntry -Path $envFile -Name "DPCLI_HEADLESS" -Value "True"

if ($providedModelConfig.Count -eq 3) {
    Set-EnvEntry -Path $envFile -Name "BAILIAN_MODEL_NAME" -Value $BailianModel
    Set-EnvEntry -Path $envFile -Name "BAILIAN_API_KEY" -Value $BailianApiKey
    Set-EnvEntry -Path $envFile -Name "BAILIAN_BASE_URL" -Value $BailianBaseUrl
}

$verifyArguments = @(
    (Join-Path $repoRoot "scripts\verify_deployment.py"),
    "--env-file", $envFile,
    "--dpcli-cwd", $DpCliPath,
    "--require-dpcli",
    "--require-runtime-config"
)
if (-not $SkipBrowserCheck) {
    $verifyArguments += "--check-browser"
    if (-not [string]::IsNullOrWhiteSpace($BrowserPath)) {
        $verifyArguments += @("--browser-path", $BrowserPath)
    }
}

Invoke-Checked "Run deployment verification" {
    & $venvPython @verifyArguments
}

Write-Host "`nDeployment complete. Start with: $venvPython main.py" -ForegroundColor Green
