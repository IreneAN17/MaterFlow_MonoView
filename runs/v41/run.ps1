param([Parameter(Mandatory=$true)][string]$ConfigPath,[ValidateSet('prepare','caption','ground','consolidate','derive')][string]$FromStage='prepare')
$ErrorActionPreference='Stop'
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
Set-Location -LiteralPath $repo
$python=Join-Path $repo '.venv/Scripts/python.exe'
if([IO.Path]::IsPathRooted($ConfigPath)){$resolved=[IO.Path]::GetFullPath($ConfigPath)}else{$resolved=[IO.Path]::GetFullPath((Join-Path $repo $ConfigPath))}
$env:PYTHONUTF8='1'
& $python -u runs/v41/run_stages.py --config $resolved --from-stage $FromStage
if($LASTEXITCODE -ne 0){throw 'Pipeline stopped; inspect the configured output folder and resume the failed stage.'}
