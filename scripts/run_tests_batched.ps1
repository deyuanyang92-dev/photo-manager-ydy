[CmdletBinding()]
param(
    [int]$TimeoutSeconds = 240,
    [int]$WorkbenchTimeoutSeconds = 600,
    [switch]$IncludePackaging,
    [string]$ResultPath = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$testsRoot = Join-Path $repoRoot "tests"
$repoPrefix = $repoRoot.TrimEnd("\", "/") + [System.IO.Path]::DirectorySeparatorChar
Set-Location $repoRoot
$env:QT_QPA_PLATFORM = "offscreen"

$files = Get-ChildItem -LiteralPath $testsRoot -Recurse -File -Filter "test_*.py" |
    Sort-Object FullName
if (-not $IncludePackaging) {
    $files = $files | Where-Object { $_.Name -ne "test_packaging_spec.py" }
}

$results = [System.Collections.Generic.List[object]]::new()
$failed = 0

foreach ($file in $files) {
    $relativePath = $file.FullName.Substring($repoPrefix.Length)
    $timeout = if ($file.Name -eq "test_workbench_view.py") {
        $WorkbenchTimeoutSeconds
    } else {
        $TimeoutSeconds
    }

    $processInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $processInfo.FileName = "python"
    $processInfo.Arguments = "-m pytest `"$($file.FullName)`" -q --tb=short"
    $processInfo.WorkingDirectory = $repoRoot
    $processInfo.UseShellExecute = $false
    $processInfo.CreateNoWindow = $true
    $processInfo.RedirectStandardOutput = $true
    $processInfo.RedirectStandardError = $true

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $processInfo
    [void]$process.Start()
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()

    $timedOut = -not $process.WaitForExit($timeout * 1000)
    if ($timedOut) {
        try {
            $process.Kill($true)
            $process.WaitForExit()
        } catch {
            # The process may have exited between the timeout and Kill().
        }
    }

    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()
    $exitCode = if ($timedOut) { -1 } else { $process.ExitCode }

    if ($timedOut) {
        $status = "TIMEOUT after ${timeout}s"
    } elseif ($exitCode -eq 0) {
        $summary = $stdout -split "`r?`n" |
            Where-Object { $_ -match "passed|skipped|deselected" } |
            Select-Object -Last 1
        $status = if ($summary) { $summary.Trim() } else { "passed" }
    } else {
        $summary = ($stdout + "`n" + $stderr) -split "`r?`n" |
            Where-Object { $_ -match "failed|error|fatal exception|access violation" } |
            Select-Object -Last 1
        $status = if ($summary) { $summary.Trim() } else { "exit=$exitCode" }
    }

    if ($exitCode -ne 0) {
        $failed += 1
    }

    $result = [pscustomobject]@{
        Test = $relativePath
        Status = $status
        ExitCode = $exitCode
        TimedOut = $timedOut
    }
    $results.Add($result)
    Write-Output ("{0}: {1}" -f $relativePath, $status)
    if ($exitCode -ne 0) {
        if (-not [string]::IsNullOrWhiteSpace($stdout)) {
            Write-Output ("--- {0} stdout ---" -f $relativePath)
            Write-Output $stdout.TrimEnd()
        }
        if (-not [string]::IsNullOrWhiteSpace($stderr)) {
            Write-Output ("--- {0} stderr ---" -f $relativePath)
            Write-Output $stderr.TrimEnd()
        }
    }
}

if ($ResultPath) {
    $resolvedResultPath = if ([System.IO.Path]::IsPathRooted($ResultPath)) {
        $ResultPath
    } else {
        Join-Path $repoRoot $ResultPath
    }
    $results | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $resolvedResultPath -Encoding utf8
}

Write-Output ("Completed {0} files; failures: {1}" -f $results.Count, $failed)
if ($failed -gt 0) {
    exit 1
}
