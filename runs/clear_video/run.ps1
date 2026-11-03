param(
    [string]$ConfigPath = 'output/clear_video/run.yaml',
    [ValidateSet('prepare','caption','ground','consolidate','derive')]
    [string]$FromStage = 'prepare'
)
$ErrorActionPreference = 'Stop'
$repo = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$python = Join-Path $repo '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Install the project GPU environment first.' }
Set-Location -LiteralPath $repo
$env:HF_DEACTIVATE_ASYNC_LOAD = '1'
$env:TRITON_CACHE_DIR = Join-Path $repo 'models/triton-cache'
$logs = Join-Path $repo 'output/clear_video/logs'
New-Item -ItemType Directory -Path $logs -Force | Out-Null
$stages = @('prepare','caption','ground','consolidate','derive')
foreach ($stage in $stages[[Array]::IndexOf($stages,$FromStage)..($stages.Count-1)]) {
    & $python -u run.py --config $ConfigPath --stage $stage 2>&1 | Tee-Object -FilePath (Join-Path $logs ($stage+'.log'))
    if ($LASTEXITCODE -ne 0) { throw "Stage $stage failed; raw model outputs and logs are preserved." }
}
