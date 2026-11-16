# 生成 sky-with-you 同步包（家里<->学校 之间带代码/模型/记忆）
# 用法：在项目根目录运行  pwsh make_sync_package.ps1
# 产物：桌面 sky-with-you_同步包_yyyyMMdd.zip
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
if (-not $root) { $root = Split-Path -Parent $MyInvocation.MyCommand.Path }
$date = Get-Date -Format 'yyyyMMdd'
$stage = Join-Path $env:TEMP "sky_sync_stage_$date"
$zip = Join-Path ([Environment]::GetFolderPath('Desktop')) "sky-with-you_同步包_$date.zip"

try {
    Write-Host "root = $root"
    if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
    New-Item -ItemType Directory -Path $stage | Out-Null
    if (Test-Path $zip) { Remove-Item $zip -Force }

    # 1) 顶层文件：默认全收，排除大文件/调试产物/临时输出
    Write-Host "[1/5] 复制顶层文件..."
    $excludeTop = @('*.exe', 'debug*.png', 'debug_chat.png', '*.bak',
                    '*_result.txt', '*_log.txt', 'mem_diag*.txt',
                    'key.txt', 'vision_key.txt')
    Get-ChildItem -LiteralPath $root -File -Force | ForEach-Object {
        $f = $_; $skip = $false
        foreach ($pat in $excludeTop) { if ($f.Name -like $pat) { $skip = $true; break } }
        if (-not $skip) { Copy-Item -LiteralPath $f.FullName -Destination (Join-Path $stage $f.Name) }
    }

    # 2) 整目录（白名单），复制后清掉 __pycache__
    Write-Host "[2/5] 复制目录..."
    $dirs = @('firmware', 'songs', 'docs', 'models', 'core', 'knowledge', 'webui', 'tools', 'templates')
    foreach ($d in $dirs) {
        $src = Join-Path $root $d
        if (Test-Path $src) {
            $dst = Join-Path $stage $d
            Copy-Item -LiteralPath $src -Destination $dst -Recurse -Force
            Get-ChildItem -LiteralPath $dst -Recurse -Directory -Filter '__pycache__' -Force -ErrorAction SilentlyContinue |
                Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    # 3) user_data：核心记忆，排除 .bak 和历史 backup_ 目录
    Write-Host "[3/5] 复制 user_data..."
    $ud = Join-Path $root 'user_data'
    if (Test-Path $ud) {
        Get-ChildItem -LiteralPath $ud -Recurse -File -Force |
            Where-Object { $_.FullName -notmatch '\\backup_' -and $_.Extension -ne '.bak' } |
            ForEach-Object {
                $rel = $_.FullName.Substring($ud.Length + 1)
                $dst = Join-Path (Join-Path $stage 'user_data') $rel
                New-Item -ItemType Directory -Path (Split-Path $dst) -Force | Out-Null
                Copy-Item -LiteralPath $_.FullName -Destination $dst
            }
    }

    # 4) runs：只带训练权重 *.pt
    Write-Host "[4/5] 复制 runs 权重..."
    $runs = Join-Path $root 'runs'
    if (Test-Path $runs) {
        Get-ChildItem -LiteralPath $runs -Recurse -File -Filter '*.pt' -Force | ForEach-Object {
            $rel = $_.FullName.Substring($runs.Length + 1)
            $dst = Join-Path (Join-Path $stage 'runs') $rel
            New-Item -ItemType Directory -Path (Split-Path $dst) -Force | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $dst
        }
    }

    # 5) 打包
    Write-Host "[5/5] 压缩..."
    Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -CompressionLevel Optimal

    $nFiles = (Get-ChildItem -LiteralPath $stage -Recurse -File).Count
    $mb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
    Write-Host "同步包完成: $zip"
    Write-Host "文件数: $nFiles ; 压缩后: $mb MB"
}
catch {
    Write-Host "打包失败: $($_.Exception.Message)" -ForegroundColor Red
    throw
}
finally {
    if (Test-Path $stage) { Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue }
}
