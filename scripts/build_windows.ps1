param(
    [string]$Python = "py",
    [string]$Name = "SpecimenPhotoWorkbench",
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Repo

if ([string]::IsNullOrWhiteSpace($Version)) {
    $Version = (& $Python -c "from app.config.version import APP_VERSION; print(APP_VERSION)").Trim()
}
if ([string]::IsNullOrWhiteSpace($Version)) {
    throw "Could not resolve application version"
}

& $Python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python -m pip install pyinstaller
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$distDir = Join-Path $Repo "dist\$Name"
$buildDir = Join-Path $Repo "build\$Name"
$packageDataDir = Join-Path $Repo "build\package-data"
$zipPath = Join-Path $Repo "dist\$Name-$Version-win64.zip"

if (Test-Path $distDir) { Remove-Item $distDir -Recurse -Force }
if (Test-Path $buildDir) { Remove-Item $buildDir -Recurse -Force }
if (Test-Path $packageDataDir) { Remove-Item $packageDataDir -Recurse -Force }
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
if (Test-Path "$Name.spec") { Remove-Item "$Name.spec" -Force }

New-Item -ItemType Directory -Path $packageDataDir | Out-Null
foreach ($fileName in @("taxonomy_seed.json", "user_taxonomy.json")) {
    $src = Join-Path $Repo "data\$fileName"
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $packageDataDir $fileName) -Force
    }
}
$recentProjects = Join-Path $packageDataDir "user_projects.json"
'{"version":1,"projects":[]}' | Set-Content -Path $recentProjects -Encoding UTF8

# The contrib pyproj hook assumes a Conda layout on Windows.  Pip installs keep
# PROJ's database inside the pyproj package, so include it at the runtime-hook
# location explicitly; otherwise CRS transforms fail in the packaged app.
$projDataDir = (& $Python -c "import pyproj.datadir; print(pyproj.datadir.get_data_dir())").Trim()
if (-not (Test-Path (Join-Path $projDataDir "proj.db"))) {
    throw "pyproj data directory is invalid: $projDataDir"
}

$pyinstallerArgs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onedir",
    "--windowed",
    "--name", $Name,
    "--icon", "resources\branding\app.ico",
    "--add-data", "resources;resources",
    "--add-data", "$packageDataDir;data",
    "--add-data", "app\db\schema.sql;app\db",
    "--add-data", "app\utils\windows_print_dialog.ps1;app\utils",
    "--hidden-import", "PyQt6.QtSvg",
    "--hidden-import", "PyQt6.QtPrintSupport",
    "--hidden-import", "serial",
    "--hidden-import", "serial.tools.list_ports",
    "--hidden-import", "qtawesome",
    "--collect-submodules", "serial",
    "--collect-submodules", "app.views",
    "--collect-all", "qtawesome",
    "--collect-all", "pyproj",
    "--add-data", "$projDataDir;Library\share\proj",
    "--exclude-module", "matplotlib.tests",
    "--exclude-module", "matplotlib.testing",
    "--exclude-module", "pandas",
    "--exclude-module", "pytest",
    "--exclude-module", "tkinter",
    "--exclude-module", "_tkinter"
)

$viewModules = & $Python -c "from app.views.registry import ALL_VIEW_SPECS; print('\n'.join(spec.module for spec in ALL_VIEW_SPECS))"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
foreach ($module in $viewModules) {
    $module = "$module".Trim()
    if (-not [string]::IsNullOrWhiteSpace($module)) {
        $pyinstallerArgs += @("--hidden-import", $module)
    }
}

$mapDir = Get-ChildItem -Directory | Where-Object { $_.Name -eq "地图" } | Select-Object -First 1
if ($mapDir) {
    $pyinstallerArgs += @("--add-data", "$($mapDir.FullName);地图")
}

$pyinstallerArgs += "main.py"

& $Python @pyinstallerArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$exePath = Join-Path $distDir "$Name.exe"
if (-not (Test-Path $exePath)) {
    throw "Packaged executable not found: $exePath"
}

function Invoke-PackagedSmoke {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ExePath,
        [int]$TimeoutSeconds = 30
    )

    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $ExePath
    $psi.Arguments = "--smoke"
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true

    $proc = [System.Diagnostics.Process]::new()
    $proc.StartInfo = $psi
    [void]$proc.Start()
    if (-not $proc.WaitForExit($TimeoutSeconds * 1000)) {
        try {
            $proc.Kill($true)
        } catch {
            $proc.Kill()
        }
        throw "Packaged smoke test timed out after $TimeoutSeconds seconds"
    }

    $stdout = $proc.StandardOutput.ReadToEnd()
    $stderr = $proc.StandardError.ReadToEnd()
    if (-not [string]::IsNullOrWhiteSpace($stdout)) {
        Write-Host $stdout.TrimEnd()
    }
    if (-not [string]::IsNullOrWhiteSpace($stderr)) {
        Write-Host $stderr.TrimEnd()
    }
    $combined = "$stdout`n$stderr"
    if (
        $proc.ExitCode -ne 0 -or
        $combined -match "Traceback|PermissionError|ModuleNotFoundError|ImportError"
    ) {
        throw "Packaged smoke test failed with exit code $($proc.ExitCode)"
    }
}

# --collect-all pyproj also copies the same PROJ database inside the package.
# The Windows runtime hook uses Library/share/proj, so remove the duplicate to
# keep the portable ZIP below GitHub's 100 MB per-file limit. Do this before the
# smoke check so the uploaded ZIP matches the tested package layout.
$duplicateProjData = Join-Path $distDir "_internal\pyproj\proj_dir\share\proj"
if (Test-Path $duplicateProjData) {
    Remove-Item $duplicateProjData -Recurse -Force
}

$oldQtPlatform = $env:QT_QPA_PLATFORM
$oldAllowMulti = $env:SPECIMEN_WORKBENCH_ALLOW_MULTI
try {
    $env:QT_QPA_PLATFORM = "offscreen"
    $env:SPECIMEN_WORKBENCH_ALLOW_MULTI = "1"
    for ($smokeAttempt = 1; $smokeAttempt -le 3; $smokeAttempt++) {
        try {
            Start-Sleep -Milliseconds (500 * $smokeAttempt)
            Invoke-PackagedSmoke -ExePath $exePath
            break
        } catch {
            if ($smokeAttempt -eq 3) {
                throw
            }
            Write-Host "Packaged smoke attempt $smokeAttempt failed; retrying: $($_.Exception.Message)"
            Start-Sleep -Seconds 2
        }
    }
} finally {
    if ($null -eq $oldQtPlatform) {
        Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue
    } else {
        $env:QT_QPA_PLATFORM = $oldQtPlatform
    }
    if ($null -eq $oldAllowMulti) {
        Remove-Item Env:\SPECIMEN_WORKBENCH_ALLOW_MULTI -ErrorAction SilentlyContinue
    } else {
        $env:SPECIMEN_WORKBENCH_ALLOW_MULTI = $oldAllowMulti
    }
}

for ($i = 1; $i -le 5; $i++) {
    try {
        Start-Sleep -Seconds 2
        Compress-Archive -Path (Join-Path $distDir "*") -DestinationPath $zipPath -Force
        break
    } catch {
        if ($i -eq 5) { throw }
        Write-Host "Zip attempt $i failed; retrying..."
    }
}

# Sign the update package with Ed25519 when a private key is available.
# The key comes from SPECIMEN_UPDATE_PRIVATE_KEY or secrets\update_private_key.pem.
# Upload the generated <zip>.sig beside the ZIP when signature checks are enabled.
$sigPath = "$zipPath.sig"
$privKey = $env:SPECIMEN_UPDATE_PRIVATE_KEY
if ([string]::IsNullOrWhiteSpace($privKey)) {
    $defaultKey = Join-Path $Repo "secrets\update_private_key.pem"
    if (Test-Path $defaultKey) { $privKey = $defaultKey }
}
if (-not [string]::IsNullOrWhiteSpace($privKey) -and (Test-Path $privKey)) {
    & $Python (Join-Path $Repo "scripts\sign_release.py") $zipPath --key $privKey --out $sigPath
    if ($LASTEXITCODE -ne 0) { throw "Failed to sign release zip" }
    Write-Host "Signed: $sigPath"
} else {
    Write-Host "WARNING: Update signing key not found; skipping Ed25519 signature."
    Write-Host "         Run scripts/gen_update_keys.py before enabling signed updates."
}

Write-Host ""
Write-Host "Built: $distDir"
Write-Host "Zip:   $zipPath"
if (-not [string]::IsNullOrWhiteSpace($sigPath) -and (Test-Path -LiteralPath $sigPath)) {
    Write-Host "Sig:   $sigPath"
}
