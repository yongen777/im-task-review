param(
    [ValidateRange(1024,65535)][int]$BackendPort = 9021,
    [ValidateRange(1024,65535)][int]$VitePort = 5173,
    [switch]$NoOpen
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv/Scripts/python.exe'
$node = Join-Path ([Environment]::GetFolderPath('UserProfile')) 'npm-global/node.exe'
if (-not (Test-Path -LiteralPath $python)) { throw "Missing project Python: $python" }
if (-not (Test-Path -LiteralPath $node)) { throw "Missing project Node 22: $node" }
$nodeVersion = & $node --version
if ($LASTEXITCODE -ne 0 -or [version]$nodeVersion.TrimStart('v') -lt [version]'22.12') {
    throw 'Vite preview requires Node 22.12 or later.'
}

$previewArgs = @('--backend-port', $BackendPort, '--vite-port', $VitePort, '--node', $node)
if ($NoOpen) { $previewArgs += '--no-open' }
& $python -X utf8 -u (Join-Path $PSScriptRoot 'dev_preview.py') @previewArgs
exit $LASTEXITCODE
