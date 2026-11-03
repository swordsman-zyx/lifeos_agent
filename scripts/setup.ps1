param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
New-Item -ItemType Directory -Force .tmp | Out-Null
$env:TEMP = (Resolve-Path .tmp).Path
$env:TMP = $env:TEMP
if (-not (Test-Path '.venv/Scripts/python.exe')) {
    & $Python -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11+ required'"
    if ($LASTEXITCODE -ne 0) { throw '请使用 -Python 指定 Python 3.11 或更高版本' }
    & $Python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '虚拟环境创建失败' }
}
& ./.venv/Scripts/python.exe -m pip install -r requirements.lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Python 依赖安装失败' }
if (Get-Command npm.cmd -ErrorAction SilentlyContinue) {
    Push-Location frontend
    try { npm.cmd ci; if ($LASTEXITCODE -ne 0) { throw '前端依赖安装失败' } } finally { Pop-Location }
} else {
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw '请先安装 Node.js 22.12+（或 20.19+）' }
    if (-not (Test-Path '.tools/package/bin/npm-cli.js')) {
        New-Item -ItemType Directory -Force .tools | Out-Null
        Invoke-WebRequest 'https://registry.npmjs.org/npm/-/npm-10.9.2.tgz' -OutFile .tools/npm.tgz
        tar -xzf .tools/npm.tgz -C .tools
        if ($LASTEXITCODE -ne 0) { throw 'npm 解压失败' }
    }
    Push-Location frontend
    try { & ../npm.cmd ci --cache ../.tools/npm-cache; if ($LASTEXITCODE -ne 0) { throw '前端依赖安装失败' } } finally { Pop-Location }
}
& ./.venv/Scripts/python.exe -m delivery_mcp.seed
if ($LASTEXITCODE -ne 0) { throw '模拟数据初始化失败' }
Write-Output '安装完成。先启动 scripts/start-mcp.ps1，再启动 scripts/start-backend.ps1 和 scripts/start-frontend.ps1。'
