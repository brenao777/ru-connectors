# Bootstrap must run before Python scripts, including on a machine without Python.
# Windows PowerShell 5.1 compatible. No credentials or mailbox/calendar operations.
param([switch]$CheckOnly)

$ErrorActionPreference = 'Stop'
$Script:ProbePath = Join-Path $PSScriptRoot 'runtime_probe.py'

function Get-PythonCandidates {
    $paths = New-Object 'System.Collections.Generic.List[string]'
    foreach ($command in @('python.exe', 'python3.exe', 'python3.14.exe', 'python3.13.exe')) {
        foreach ($item in @(Get-Command $command -CommandType Application -ErrorAction SilentlyContinue)) {
            # Skip App Execution Alias stubs, which can open Microsoft Store.
            if ($item.Source -notmatch '\\Microsoft\\WindowsApps\\') { $paths.Add($item.Source) }
        }
    }
    foreach ($registryRoot in @('HKCU:\Software\Python\PythonCore', 'HKLM:\Software\Python\PythonCore', 'HKLM:\Software\WOW6432Node\Python\PythonCore')) {
        foreach ($version in @(Get-ChildItem $registryRoot -ErrorAction SilentlyContinue)) {
            $key = Get-Item (Join-Path $version.PSPath 'InstallPath') -ErrorAction SilentlyContinue
            if ($null -ne $key) {
                $executable = $key.GetValue('ExecutablePath')
                if ($executable) { $paths.Add([string]$executable) }
                $directory = $key.GetValue('')
                if ($directory) { $paths.Add((Join-Path $directory 'python.exe')) }
            }
        }
    }
    $roots = @((Join-Path $env:LOCALAPPDATA 'Programs\Python'), (Join-Path $env:LOCALAPPDATA 'Python'))
    foreach ($programFiles in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if ($programFiles) { $roots += $programFiles }
    }
    foreach ($root in $roots) {
        foreach ($candidate in @(Get-ChildItem (Join-Path $root 'Python*\python.exe') -ErrorAction SilentlyContinue)) {
            $paths.Add($candidate.FullName)
        }
    }
    $launcher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($launcher) {
        # Listing runtimes does not request automatic installation by the launcher.
        $listing = @()
        try { $listing = & $launcher.Source -0p 2>$null } catch { $listing = @() }
        foreach ($line in $listing) {
            if ($line -match '([A-Za-z]:\\.+?python[0-9.]*\.exe)\s*$') { $paths.Add($Matches[1]) }
        }
    }
    return @($paths | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -Unique)
}

function Test-PythonRuntimes {
    $ErrorActionPreference = 'Continue'
    $reports = @()
    foreach ($path in @(Get-PythonCandidates)) {
        # Ignore project/site customizations; never read secrets during a probe.
        $output = & $path -I $Script:ProbePath 2>$null
        try {
            $report = ($output -join "`n") | ConvertFrom-Json
            if ($report.python_executable -and $report.checks) { $reports += $report }
        } catch {
            $reports += [pscustomobject]@{ ready = $false; python_executable = $path; error = 'probe_failed' }
        }
    }
    return $reports
}

function Get-ReadyRuntime($Reports) {
    return $Reports | Where-Object { $_.ready -eq $true } | Select-Object -First 1
}

function Install-FullPython([string]$LogDirectory) {
    $options = '/quiet InstallAllUsers=0 Include_exe=1 Include_lib=1 Include_tcltk=1 Include_pip=0 Include_test=0 Include_launcher=0 PrependPath=0 AssociateFiles=0 Shortcuts=0'
    $winget = Get-Command winget.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($winget) {
        $log = Join-Path $LogDirectory 'winget-python.log'
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            & $winget.Source install --id Python.Python.3.14 --exact --source winget --scope user --silent --accept-package-agreements --accept-source-agreements --disable-interactivity --force --override $options *> $log
            $installerExitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousPreference
        }
        # Installation exit codes alone do not establish readiness: re-probe below.
        return [pscustomobject]@{ method = 'winget'; exit_code = $installerExitCode; log = $log }
    }
    # No winget: download a full, signed installer directly from Python.org.
    # Select a stable 3.14 patch release, never a prerelease or embeddable package.
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $index = Invoke-WebRequest 'https://www.python.org/ftp/python/' -UseBasicParsing
    $versions = @([regex]::Matches($index.Content, 'href="(3\.14\.\d+)/"') | ForEach-Object { $_.Groups[1].Value } | Select-Object -Unique | Sort-Object { [version]$_ } -Descending)
    if ($versions.Count -eq 0) { throw 'No stable Python 3.14 release was found on Python.org.' }
    $architecture = $env:PROCESSOR_ARCHITEW6432
    if (-not $architecture) { $architecture = $env:PROCESSOR_ARCHITECTURE }
    $suffix = if ($architecture -eq 'ARM64') { '-arm64' } elseif ($architecture -eq 'AMD64') { '-amd64' } else { '' }
    $installer = Join-Path $LogDirectory 'python-installer.exe'
    $downloaded = $false
    foreach ($version in $versions) {
        $url = "https://www.python.org/ftp/python/$version/python-$version$suffix.exe"
        try {
            Invoke-WebRequest $url -OutFile $installer -UseBasicParsing
            $downloaded = $true
            break
        } catch {
            # Only 404 means this release has no standalone installer. Do not
            # silently downgrade for TLS, proxy, authentication, or server errors.
            $response = $_.Exception.Response
            if ($null -eq $response -or [int]$response.StatusCode -ne 404) { throw }
        }
    }
    if (-not $downloaded) { throw 'No full Python 3.14 installer was available for this architecture.' }
    $signature = Get-AuthenticodeSignature -LiteralPath $installer
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(^|,\s*)O=Python Software Foundation(,|$)') {
        throw 'The Python installer signature is invalid or has an unexpected publisher.'
    }
    $log = Join-Path $LogDirectory 'python-installer.log'
    $process = Start-Process -FilePath $installer -ArgumentList ($options + ' /log "' + $log + '"') -Wait -PassThru
    return [pscustomobject]@{ method = 'python.org'; exit_code = $process.ExitCode; log = $log }
}

function Invoke-ConnectorBootstrap {
    if ($env:OS -ne 'Windows_NT') {
        Write-Output (@{ready = $false; reason = 'native_windows_required'} | ConvertTo-Json -Compress)
        return 20
    }
    $reports = @(Test-PythonRuntimes)
    $runtime = Get-ReadyRuntime $reports
    $installation = $null
    if (-not $runtime -and -not $CheckOnly) {
        $logDirectory = Join-Path ([IO.Path]::GetTempPath()) ('codex-connectors-python-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $logDirectory | Out-Null
        try {
            $installation = Install-FullPython $logDirectory
        } catch {
            Write-Output (@{ready = $false; reason = $_.Exception.Message; log_directory = $logDirectory; probes = $reports} | ConvertTo-Json -Depth 8 -Compress)
            return 20
        }
        $reports = @(Test-PythonRuntimes)
        $runtime = Get-ReadyRuntime $reports
    }
    if (-not $runtime) {
        Write-Output (@{ready = $false; reason = 'No complete Python runtime passed verification. Do not install the connector or request an app password yet.'; probes = $reports; installation = $installation} | ConvertTo-Json -Depth 8 -Compress)
        if ($CheckOnly) { return 10 }
        return 20
    }
    $runtime | Add-Member -NotePropertyName installation -NotePropertyValue $installation -Force
    $runtime | ConvertTo-Json -Depth 8 -Compress | Write-Output
    return 0
}

# Dot sourcing loads functions for isolated tests without triggering installation.
if ($MyInvocation.InvocationName -ne '.') {
    try {
        $result = @(Invoke-ConnectorBootstrap)
        $code = [int]$result[-1]
        $result | Select-Object -SkipLast 1 | Write-Output
        exit $code
    } catch {
        Write-Output (@{ready = $false; reason = $_.Exception.Message} | ConvertTo-Json -Compress)
        exit 20
    }
}
