#requires -Version 5.1
<#
.SYNOPSIS
    Build the Maxima OCR Windows installer.

.DESCRIPTION
    Self-bootstrapping build:
      1. Compiles modbus_server.exe and tools/modbus_client.exe via PyInstaller.
      2. Downloads NSSM and Inno Setup into build/tools/ on first run, cached after.
      3. Runs ISCC to produce dist/MaximaOCR-Setup-<VERSION>.exe.

    Designed to run identically on a developer PC and a GitHub Actions Windows
    runner -- no pre-installed system tools required beyond Python + venv deps.

.PARAMETER Version
    Version string baked into the installer filename and Add/Remove Programs.
    Expected to be supplied by the caller (in CI, this is the git tag). When
    omitted, falls back to `git describe --tags` against the current checkout
    so local builds still work; fails if neither is available.

.PARAMETER SkipPyInstaller
    Skip the PyInstaller stage (use the modbus_server.exe / modbus_client.exe
    already in dist/). Useful for iterating on the .iss without recompiling.

.PARAMETER SkipModelFetch
    Skip fetching the latest models bundle from GitHub. Use whatever is already
    in ./models/. Useful for offline iteration on the installer .iss.
#>
[CmdletBinding()]
param(
    [string]$Version,
    [switch]$SkipPyInstaller,
    [switch]$SkipModelFetch
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # 10x faster Invoke-WebRequest

# $PSScriptRoot is reliable in the script body but can be empty in param()
# defaults under some invocation paths; resolve defensively here.
$RepoRoot = $PSScriptRoot
if (-not $RepoRoot) { $RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $RepoRoot) { $RepoRoot = (Get-Location).Path }

if (-not $Version) {
    # Fallback for local builds: derive from the git tag. CI is expected to
    # pass -Version explicitly (e.g. from $env:GITHUB_REF_NAME on tag pushes).
    try {
        $described = (& git -C $RepoRoot describe --tags --always --dirty 2>$null)
        if ($LASTEXITCODE -eq 0 -and $described) {
            $Version = $described.Trim()
        }
    } catch {
        # git not on PATH or not a repo -- fall through to the throw below.
    }
    if (-not $Version) {
        throw "Version not supplied and 'git describe --tags' failed. Pass a version explicitly: compile.bat 0.4 (or build.ps1 -Version 0.4)."
    }
}

# Strip any leading 'v' so 'v0.4' and '0.4' produce the same installer filename.
$Version = $Version -replace '^v', ''

# Validate: must be safe to embed in a filename and in the Inno AppVersion field.
if ($Version -notmatch '^[0-9]+(\.[0-9]+){1,2}(-[A-Za-z0-9.]+)?$') {
    throw "Version '$Version' is not a recognized version string (expected x.y or x.y.z, optionally with a pre-release suffix)."
}
$BuildToolDir = Join-Path $RepoRoot 'build\tools'
$DistDir      = Join-Path $RepoRoot 'dist'
$ModelsDir    = Join-Path $RepoRoot 'models'

$NssmVersion = '2.24'
$NssmUrl     = "https://nssm.cc/release/nssm-$NssmVersion.zip"
$NssmDir     = Join-Path $BuildToolDir 'nssm'
$NssmExe     = Join-Path $NssmDir 'nssm.exe'

# Pinned Inno Setup release. Bump deliberately; canonical URL is the GitHub
# release for the chosen version. The CDN at files.jrsoftware.org rotates and
# removes older versions, so we hit the immutable GitHub release asset.
$InnoVersion   = '6.7.1'
$InnoUrl       = "https://github.com/jrsoftware/issrc/releases/download/is-$($InnoVersion -replace '\.','_')/innosetup-$InnoVersion.exe"
$InnoDir       = Join-Path $BuildToolDir 'innosetup'
$InnoIsccExe   = Join-Path $InnoDir 'ISCC.exe'

# The OCR models are released as bundle-YYYYMMDD tags on the private
# maxima-ocr-learning repo. The build always pulls the latest one and prunes
# any stale dated subfolders so the installer only ships the current bundle.
$ModelsRepo = 'dennispo/maxima-ocr-learning'

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Invoke-Native {
    # Don't name this $Args -- it's an automatic variable in PowerShell and
    # taking it as a parameter silently drops the caller's value under strict mode.
    param(
        [Parameter(Mandatory)][string]$Exe,
        [Parameter(Mandatory)][string[]]$Arguments
    )
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed ($LASTEXITCODE): $Exe $($Arguments -join ' ')"
    }
}

function Ensure-Directory([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Get-PyInstaller {
    # Prefer the project venv so the build matches local `python modbus_server.py`.
    $venvPy = Join-Path $RepoRoot 'venv\Scripts\pyinstaller.exe'
    if (Test-Path -LiteralPath $venvPy) { return $venvPy }
    $onPath = Get-Command pyinstaller -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    throw "pyinstaller not found. Run 'pip install -r requirements.txt' in the project venv."
}

function Get-InferenceHooksDir {
    # PyInstaller needs the maxima_inference hooks dir from the installed wheel.
    $hooks = Join-Path $RepoRoot 'venv\Lib\site-packages\maxima_inference\_pyinstaller'
    if (Test-Path -LiteralPath $hooks) { return $hooks }
    throw "maxima_inference hooks dir not found at $hooks. Reinstall requirements.txt."
}

function Ensure-Nssm {
    if (Test-Path -LiteralPath $NssmExe) { return }
    Write-Step "Downloading NSSM $NssmVersion"
    Ensure-Directory $BuildToolDir
    $zip = Join-Path $BuildToolDir "nssm-$NssmVersion.zip"
    Invoke-WebRequest -Uri $NssmUrl -OutFile $zip -UseBasicParsing
    $extractDir = Join-Path $BuildToolDir "nssm-$NssmVersion-extract"
    if (Test-Path -LiteralPath $extractDir) {
        Remove-Item -LiteralPath $extractDir -Recurse -Force
    }
    Expand-Archive -LiteralPath $zip -DestinationPath $extractDir -Force
    # Layout inside the zip: nssm-2.24/win32/nssm.exe and nssm-2.24/win64/nssm.exe
    $src = Join-Path $extractDir "nssm-$NssmVersion\win64\nssm.exe"
    if (-not (Test-Path -LiteralPath $src)) {
        throw "NSSM zip layout unexpected; nssm.exe not found at $src"
    }
    Ensure-Directory $NssmDir
    Copy-Item -LiteralPath $src -Destination $NssmExe -Force
    Remove-Item -LiteralPath $extractDir -Recurse -Force
    Remove-Item -LiteralPath $zip -Force
}

function Ensure-InnoSetup {
    if (Test-Path -LiteralPath $InnoIsccExe) { return }
    Write-Step "Downloading Inno Setup $InnoVersion"
    Ensure-Directory $BuildToolDir
    $installer = Join-Path $BuildToolDir "innosetup-$InnoVersion.exe"
    Invoke-WebRequest -Uri $InnoUrl -OutFile $installer -UseBasicParsing

    Write-Step "Installing Inno Setup into $InnoDir"
    Ensure-Directory $InnoDir
    # /CURRENTUSER avoids needing admin; /SP-, /VERYSILENT, /SUPPRESSMSGBOXES = headless.
    & $installer /VERYSILENT /SUPPRESSMSGBOXES /SP- /NORESTART /NOICONS /CURRENTUSER "/DIR=$InnoDir" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup silent install failed (exit $LASTEXITCODE). Try running as admin once, or install Inno Setup manually and copy ISCC.exe to $InnoDir."
    }
    if (-not (Test-Path -LiteralPath $InnoIsccExe)) {
        throw "Inno Setup install completed but ISCC.exe not found at $InnoIsccExe."
    }
    Remove-Item -LiteralPath $installer -Force
}

function Invoke-PyInstaller {
    $pyi = Get-PyInstaller
    $hooks = Get-InferenceHooksDir

    Write-Step "Building dist\modbus_server.exe"
    Invoke-Native -Exe $pyi -Arguments @(
        '--noconfirm', '--onefile',
        '--additional-hooks-dir', $hooks,
        (Join-Path $RepoRoot 'modbus_server.py')
    )

    Write-Step "Building dist\tools\modbus_client.exe"
    $toolsOut = Join-Path $DistDir 'tools'
    Ensure-Directory $toolsOut
    Invoke-Native -Exe $pyi -Arguments @(
        '--noconfirm', '--onefile',
        '--distpath', $toolsOut,
        '--workpath', (Join-Path $RepoRoot 'build\modbus_client'),
        '--specpath', (Join-Path $RepoRoot 'build\modbus_client'),
        (Join-Path $RepoRoot 'tools\modbus_client.py')
    )

    Write-Step "Staging config.yaml next to modbus_server.exe"
    Copy-Item -LiteralPath (Join-Path $RepoRoot 'config.yaml') `
              -Destination (Join-Path $DistDir 'config.yaml') -Force
}

function Ensure-Gh {
    $cmd = Get-Command gh -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw "gh (GitHub CLI) is required to fetch the models bundle from a private repo. Install it from https://cli.github.com/ (or in CI: gh is preinstalled on Windows runners; the build just needs GH_TOKEN/GITHUB_TOKEN with read access to $ModelsRepo)."
    }
}

function Get-LatestBundleTag {
    # Latest bundle-* release by publishedAt. --json keeps us off scraping HTML.
    $raw = & gh release list --repo $ModelsRepo --limit 50 --json tagName,publishedAt 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "gh release list failed for $ModelsRepo. If running in CI, set GH_TOKEN (or GITHUB_TOKEN) to a PAT with read access. Output:`n$raw"
    }
    $releases = ($raw -join "`n") | ConvertFrom-Json
    $bundles = @($releases | Where-Object { $_.tagName -like 'bundle-*' })
    if ($bundles.Count -eq 0) {
        throw "No bundle-* releases found in $ModelsRepo."
    }
    $latest = $bundles | Sort-Object publishedAt -Descending | Select-Object -First 1
    return $latest.tagName
}

function Ensure-ModelsBundle {
    if ($SkipModelFetch) {
        Write-Step "Skipping models fetch (--SkipModelFetch); using ./models/ as-is"
        return
    }

    Ensure-Gh
    Write-Step "Resolving latest bundle from $ModelsRepo"
    $tag = Get-LatestBundleTag
    $bundleDate = $tag -replace '^bundle-',''
    Write-Host "Latest bundle release: $tag"

    Ensure-Directory $ModelsDir
    $bundleDir   = Join-Path $ModelsDir $bundleDate
    $manifestPath = Join-Path $bundleDir 'manifest.yaml'

    if (Test-Path -LiteralPath $manifestPath) {
        Write-Host "Bundle $bundleDate already present locally; skipping download."
    } else {
        Write-Step "Downloading $tag from $ModelsRepo"
        $dlDir = Join-Path $BuildToolDir 'models-download'
        if (Test-Path -LiteralPath $dlDir) { Remove-Item -LiteralPath $dlDir -Recurse -Force }
        Ensure-Directory $dlDir
        & gh release download $tag --repo $ModelsRepo --pattern '*.zip' --dir $dlDir --clobber
        if ($LASTEXITCODE -ne 0) { throw "gh release download $tag failed." }
        $zip = Get-ChildItem -LiteralPath $dlDir -Filter '*.zip' | Select-Object -First 1
        if (-not $zip) { throw "No .zip asset found on release $tag." }
        # The zip extracts to <bundleDate>/... so the destination is ModelsDir.
        Expand-Archive -LiteralPath $zip.FullName -DestinationPath $ModelsDir -Force
        Remove-Item -LiteralPath $dlDir -Recurse -Force
        if (-not (Test-Path -LiteralPath $manifestPath)) {
            throw "Extracted $zip but $manifestPath not found -- zip layout changed."
        }
    }

    # active.txt selects which bundle the runtime loads; written without a trailing newline.
    Set-Content -LiteralPath (Join-Path $ModelsDir 'active.txt') -Value $bundleDate -NoNewline

    # Prune any older bundles so the installer doesn't ship multiple copies.
    Get-ChildItem -LiteralPath $ModelsDir -Directory |
        Where-Object { $_.Name -ne $bundleDate } |
        ForEach-Object {
            Write-Host "Removing stale bundle: $($_.Name)"
            Remove-Item -LiteralPath $_.FullName -Recurse -Force
        }
}

function Test-ModelsBundle {
    if (-not (Test-Path -LiteralPath $ModelsDir)) {
        throw "models/ directory missing. The installer must bundle an OCR models bundle; download one from maxima-ocr-learning releases and place it under .\models\."
    }
    $activeTxt = Join-Path $ModelsDir 'active.txt'
    if (-not (Test-Path -LiteralPath $activeTxt)) {
        throw "models/active.txt missing. The runtime selects the active bundle from this file."
    }
    $active = (Get-Content -LiteralPath $activeTxt -Raw).Trim()
    if (-not $active) {
        throw "models/active.txt is empty."
    }
    $bundleDir = Join-Path $ModelsDir $active
    if (-not (Test-Path -LiteralPath (Join-Path $bundleDir 'manifest.yaml'))) {
        throw "models/$active/manifest.yaml not found. Bundle '$active' is incomplete or active.txt points at a non-existent bundle."
    }
}

function Invoke-Iscc {
    Write-Step "Compiling installer with ISCC (version $Version)"
    $iss = Join-Path $RepoRoot 'installer\maxima-ocr.iss'
    Invoke-Native -Exe $InnoIsccExe -Arguments @("/DMyAppVersion=$Version", $iss)
    $expected = Join-Path $DistDir "MaximaOCR-Setup-$Version.exe"
    if (-not (Test-Path -LiteralPath $expected)) {
        throw "ISCC reported success but $expected not found."
    }
    Write-Host ""
    Write-Host "Installer ready: $expected" -ForegroundColor Green
}

# --- main ---------------------------------------------------------------------

Push-Location $RepoRoot
try {
    Write-Host "Maxima OCR installer build -- version $Version"

    Ensure-ModelsBundle
    Test-ModelsBundle

    if (-not $SkipPyInstaller) {
        Invoke-PyInstaller
    } else {
        Write-Step "Skipping PyInstaller (--SkipPyInstaller)"
        if (-not (Test-Path -LiteralPath (Join-Path $DistDir 'modbus_server.exe'))) {
            throw "--SkipPyInstaller given but dist\modbus_server.exe missing."
        }
    }

    Ensure-Nssm
    Ensure-InnoSetup
    Invoke-Iscc
}
finally {
    Pop-Location
}
