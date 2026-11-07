$ErrorActionPreference='Stop'
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
Set-Location -LiteralPath $repo
if(-not (Test-Path -LiteralPath '.venv-ocr/Scripts/python.exe')){& '.venv/Scripts/python.exe' -m venv .venv-ocr; if($LASTEXITCODE -ne 0){throw 'OCR environment creation failed'}}
$requirements=if(Test-Path -LiteralPath 'requirements-ocr.lock.txt'){'requirements-ocr.lock.txt'}else{'requirements-ocr.txt'}
& '.venv-ocr/Scripts/python.exe' -m pip install -r $requirements
if($LASTEXITCODE -ne 0){throw 'OCR installation failed'}
