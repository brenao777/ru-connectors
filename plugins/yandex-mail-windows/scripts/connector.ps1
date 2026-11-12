param(
    [ValidateSet('Serve', 'Setup', 'Check', 'ReadAttachment')]
    [string]$Mode = 'Serve',
    [string]$AttachmentPath,
    [switch]$Reconnect
)

$ErrorActionPreference = 'Stop'
$state = Join-Path $env:LOCALAPPDATA 'Codex\yandex-mail-safe'
$runtimeFile = Join-Path $state 'runtime-python.txt'
$server = Join-Path $PSScriptRoot '..\server\yandex_mail_mcp.py'
$check = Join-Path $PSScriptRoot 'check_connection.py'

if ($Mode -eq 'Setup') {
    $bootstrap = Join-Path $PSScriptRoot 'bootstrap_windows.ps1'
    $report = & (Join-Path $PSHOME 'powershell.exe') -NoProfile -ExecutionPolicy Bypass -File $bootstrap
    if ($LASTEXITCODE -ne 0) {
        $report | Write-Output
        exit 20
    }
    $runtime = ($report -join "`n") | ConvertFrom-Json
    if (-not $runtime.ready -or -not $runtime.python_executable) {
        throw 'Python runtime did not pass verification.'
    }
    $python = [string]$runtime.python_executable
    New-Item -ItemType Directory -Force -Path $state | Out-Null
    $staged = Join-Path $state ([Guid]::NewGuid().ToString('N') + '.tmp')
    try {
        [IO.File]::WriteAllText($staged, $python)
        if ([IO.File]::Exists($runtimeFile)) {
            [IO.File]::Replace($staged, $runtimeFile, $null)
        } else {
            [IO.File]::Move($staged, $runtimeFile)
        }
    } finally {
        if ([IO.File]::Exists($staged)) { [IO.File]::Delete($staged) }
    }

    if (-not $Reconnect) {
        $status = & $python -I $check
        if ($LASTEXITCODE -ne 3) {
            $status | Write-Output
            exit $LASTEXITCODE
        }
    }
    & $python -I (Join-Path $PSScriptRoot 'setup_credentials.py')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $python -I $check
    exit $LASTEXITCODE
}

if (-not [IO.File]::Exists($runtimeFile)) {
    [Console]::Error.WriteLine('Yandex Mail setup is required. Run the connect-yandex-mail skill and start a new session.')
    exit 20
}
$python = [IO.File]::ReadAllText($runtimeFile)
if (-not [IO.Path]::IsPathRooted($python) -or -not [IO.File]::Exists($python)) {
    [Console]::Error.WriteLine('Yandex Mail Python runtime is unavailable. Run the connect-yandex-mail skill.')
    exit 20
}

if ($Mode -eq 'Check') {
    & $python -I $check
    exit $LASTEXITCODE
}
if ($Mode -eq 'ReadAttachment') {
    if (-not $AttachmentPath) { throw 'AttachmentPath is required.' }
    & $python -I (Join-Path $PSScriptRoot 'read_attachment_file.py') -- $AttachmentPath
    exit $LASTEXITCODE
}

# The Python child inherits all three stdio handles directly. PowerShell must
# not pipe or transcode the JSON-RPC traffic.
$processInfo = New-Object System.Diagnostics.ProcessStartInfo
$processInfo.FileName = $python
$processInfo.Arguments = '-E -s -B "' + [IO.Path]::GetFullPath($server) + '"'
$processInfo.UseShellExecute = $false
$process = [Diagnostics.Process]::Start($processInfo)
try {
    $process.WaitForExit()
    exit $process.ExitCode
} finally {
    $process.Dispose()
}
