$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '无法创建 Python 虚拟环境' }
}
& $taskPython -c "import musicdl, fugashi, unidic_lite" 2>$null
if ($LASTEXITCODE -ne 0) {
    & $taskPython -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) { throw '无法安装 pip' }
    & $taskPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw '运行依赖安装失败' }
}
& $taskPython -X utf8 server.py @args
