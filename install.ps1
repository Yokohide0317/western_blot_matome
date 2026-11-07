# Install Western Blot Matome (wbm) on Windows.
#
# Needs only PowerShell: no Python, git or Node.js. uv is installed if missing
# and brings its own Python.
#
#   powershell -ExecutionPolicy ByPass -c "irm https://github.com/Yokohide0317/western_blot_matome/releases/latest/download/install.ps1 | iex"
#   $env:WBM_VERSION = 'v0.2.0'   # before the line above, for a specific version
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$Repo = 'Yokohide0317/western_blot_matome'
$Tag = if ($env:WBM_VERSION) { $env:WBM_VERSION } else { 'latest' }

$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
    $uv = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
    if (-not (Test-Path $uv)) {
        Write-Host 'uv をインストールしています…'
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    }
}

if ($Tag -eq 'latest') {
    $release = Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest"
    $Tag = $release.tag_name
}
if ($Tag -notmatch '^v\d') { throw "リリースが見つかりませんでした: $Tag" }
$Version = $Tag.Substring(1)
$Wheel = "https://github.com/$Repo/releases/download/$Tag/wbm-$Version-py3-none-any.whl"

Write-Host "wbm $Version をインストールしています…"
& $uv tool install --force --python 3.12 $Wheel
if ($LASTEXITCODE) { throw 'インストールに失敗しました' }
$wbm = Join-Path (& $uv tool dir --bin) 'wbm.exe'
& $wbm install-skill
& $wbm --version
Write-Host ''
Write-Host "インストールしました: $wbm"
Write-Host 'Claude Code / Codex に Skill「wblot」を入れました。ChemiDocのZIPやフォルダを渡して「まとめて」と頼んでください。'
