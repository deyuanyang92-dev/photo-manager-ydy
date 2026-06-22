param(
    [string]$Python = "py",
    [string]$Name = "SpecimenPhotoWorkbench",
    [string]$Version = "v0.01"
)

$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Repo

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
    "--hidden-import", "qtawesome",
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

$mapDir = Get-ChildItem -Directory | Where-Object { $_.Name -eq "地图" } | Select-Object -First 1
if ($mapDir) {
    $pyinstallerArgs += @("--add-data", "$($mapDir.FullName);地图")
}

$pyinstallerArgs += "main.py"

& $Python @pyinstallerArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# --collect-all pyproj also copies the same PROJ database inside the package.
# The Windows runtime hook uses Library/share/proj, so remove the duplicate to
# keep the portable ZIP below GitHub's 100 MB per-file limit.
$duplicateProjData = Join-Path $distDir "_internal\pyproj\proj_dir\share\proj"
if (Test-Path $duplicateProjData) {
    Remove-Item $duplicateProjData -Recurse -Force
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

Write-Host ""
Write-Host "Built: $distDir"
Write-Host "Zip:   $zipPath"
