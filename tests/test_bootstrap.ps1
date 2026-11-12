# Offline flow tests, compatible with Windows PowerShell 5.1 and PowerShell 7.
# All installation, process, discovery, and download operations are mocked.
param([string]$BootstrapPath = (Join-Path $PSScriptRoot '..\plugins\ru-connectors\scripts\bootstrap_windows.ps1'))
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile($BootstrapPath, [ref]$tokens, [ref]$errors) | Out-Null
if ($errors.Count -gt 0) { throw ($errors | Out-String) }
. $BootstrapPath
$testCount = 0
$previousOs = $env:OS
$testDirectory = Join-Path ([IO.Path]::GetTempPath()) ('connector-bootstrap-tests-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $testDirectory | Out-Null

function Assert-Equal($Actual, $Expected, [string]$Message) {
    if ($Actual -ne $Expected) { throw "$Message : expected $Expected; got $Actual" }
}
function Read-Result {
    $items = @(Invoke-ConnectorBootstrap)
    return [pscustomobject]@{ code = [int]$items[-1]; data = ($items[0] | ConvertFrom-Json) }
}
try {
    $env:OS = 'Windows_NT'
    $CheckOnly = $false
    $script:installs = 0
    $script:probes = 0
    $script:mode = 'ready'
    function Test-PythonRuntimes {
        $script:probes++
        $ready = $script:mode -eq 'ready' -or ($script:mode -eq 'install_then_ready' -and $script:installs -eq 1)
        return [pscustomobject]@{ready=$ready; python_executable='C:\Users\Test User\Python\python.exe'; checks=@{tk_window=$ready}}
    }
    # Retain the real install function for transport tests later.
    $realInstall = ${function:Install-FullPython}
    function Install-FullPython([string]$LogDirectory) {
        $script:installs++
        if ($script:mode -eq 'install_throws') { throw 'corporate policy rejected installation' }
        return [pscustomobject]@{method='mock'; exit_code=0}
    }
    $result = Read-Result
    Assert-Equal $result.code 0 'ready runtime succeeds'
    Assert-Equal $script:installs 0 'ready runtime is reused'
    Assert-Equal $result.data.installation $null 'no installation reported'
    $testCount++

    $script:mode = 'missing'
    $CheckOnly = $true
    $result = Read-Result
    Assert-Equal $result.code 10 'check-only reports missing dependencies'
    Assert-Equal $script:installs 0 'check-only never installs'
    $testCount++

    $CheckOnly = $false
    $script:mode = 'install_then_ready'
    $result = Read-Result
    Assert-Equal $result.code 0 'successful installation is verified'
    Assert-Equal $script:installs 1 'exactly one installation'
    Assert-Equal $result.data.ready $true 'readiness confirmed'
    $testCount++

    $script:installs = 0
    $script:mode = 'missing'
    $result = Read-Result
    Assert-Equal $result.code 20 'zero installer exit cannot mask missing dependencies'
    Assert-Equal $result.data.ready $false 'missing runtime never reported ready'
    Assert-Equal $script:installs 1 'no unbounded installation retries'
    $testCount++

    $script:installs = 0
    $script:mode = 'install_throws'
    $result = Read-Result
    Assert-Equal $result.code 20 'installation failure is surfaced'
    Assert-Equal ($result.data.reason -match 'corporate policy') $true 'failure reason preserved'
    $testCount++

    $env:OS = 'other'
    $script:probes = 0
    $script:installs = 0
    $result = Read-Result
    Assert-Equal $result.code 20 'non-Windows rejected'
    Assert-Equal $script:probes 0 'non-Windows never probes native dependencies'
    Assert-Equal $script:installs 0 'non-Windows never installs'
    $testCount++

    # Check real install-function command construction with mocked executables.
    ${function:Install-FullPython} = $realInstall
    function Get-Command { param($Name, $CommandType, $ErrorAction)
        if ($script:hasWinget) { return [pscustomobject]@{Source='Fake-Winget'} }
        return $null
    }
    function Fake-Winget {
        $script:wingetArguments = $args
        $global:LASTEXITCODE = 0
    }
    $script:hasWinget = $true
    $result = Install-FullPython $testDirectory
    Assert-Equal $result.method 'winget' 'winget selected'
    Assert-Equal ($script:wingetArguments -contains '--scope') $true 'scope specified'
    Assert-Equal ($script:wingetArguments -contains 'user') $true 'per-user install'
    Assert-Equal (($script:wingetArguments -join ' ') -match 'Include_tcltk=1') $true 'Tcl/Tk explicitly included'
    Assert-Equal (($script:wingetArguments -join ' ') -match 'PrependPath=0') $true 'PATH preserved'
    $testCount++

    $script:hasWinget = $false
    $script:signatureStatus = 'Valid'
    $script:publisher = 'CN=Python Software Foundation, O=Python Software Foundation, C=US'
    $script:started = 0
    function Invoke-WebRequest { param($Uri, $OutFile, [switch]$UseBasicParsing)
        if ($OutFile) {
            Set-Content -LiteralPath $OutFile -Value 'mock installer'
            $script:downloadUrl = $Uri
            return
        }
        return [pscustomobject]@{Content='<a href="3.14.9/">3.14.9</a><a href="3.14.10/">3.14.10</a><a href="3.15.1/">3.15.1</a><a href="3.14.11rc1/">prerelease</a>'}
    }
    function Get-AuthenticodeSignature { param($LiteralPath)
        return [pscustomobject]@{Status=$script:signatureStatus; SignerCertificate=[pscustomobject]@{Subject=$script:publisher}}
    }
    function Start-Process { param($FilePath, $ArgumentList, [switch]$Wait, [switch]$PassThru)
        $script:started++
        $script:installerArguments = $ArgumentList
        return [pscustomobject]@{ExitCode=0}
    }
    $result = Install-FullPython $testDirectory
    Assert-Equal $result.method 'python.org' 'official fallback selected'
    Assert-Equal ($script:downloadUrl -match '/3\.14\.10/python-3\.14\.10') $true 'latest stable patch selected numerically'
    Assert-Equal $script:started 1 'verified installer launched'
    $testCount++

    $script:signatureStatus = 'HashMismatch'
    $script:started = 0
    $thrown = $false
    try { Install-FullPython $testDirectory | Out-Null } catch { $thrown = $true }
    Assert-Equal $thrown $true 'invalid signature rejected'
    Assert-Equal $script:started 0 'invalid installer never executed'
    $testCount++

    $script:signatureStatus = 'Valid'
    $script:publisher = 'CN=Other publisher, O=Other publisher, C=US'
    $thrown = $false
    try { Install-FullPython $testDirectory | Out-Null } catch { $thrown = $true }
    Assert-Equal $thrown $true 'unexpected publisher rejected'
    Assert-Equal $script:started 0 'unexpected publisher never executed'
    $testCount++

    Write-Output "PASS: $testCount bootstrap flow and installer transport tests; no real installations or downloads"
} finally {
    $env:OS = $previousOs
    Remove-Item -LiteralPath $testDirectory -Recurse -Force
}
