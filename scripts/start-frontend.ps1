$ErrorActionPreference = 'Stop'
Set-Location (Join-Path (Split-Path $PSScriptRoot -Parent) 'frontend')
if (Get-Command npm.cmd -ErrorAction SilentlyContinue) { npm.cmd run dev } else { & ../npm.cmd run dev }
