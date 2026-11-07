param(
    [string]$ConfigPath = '',
    [string]$FromStage = 'prepare',
    [string]$ThroughStage = 'export'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $ConfigPath) {
    $ConfigPath = Join-Path $projectRoot 'output\benchmarks\v42_75_105\run.yaml'
}
$resolvedConfig = [IO.Path]::GetFullPath($ConfigPath)
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
& $pythonExe -u (Join-Path $PSScriptRoot 'run_stages.py') --config $resolvedConfig --from-stage $FromStage --through-stage $ThroughStage
exit $LASTEXITCODE
