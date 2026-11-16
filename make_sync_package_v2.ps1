# 生成 sky-with-you 同步包（家里<->学校，带代码/模型/记忆）
# 规则与桌面 sky-with-you_同步包_20261111.zip 对齐，并新增 yolo_backend.py（AMD GPU 后端）
# 用法：在项目根目录运行  pwsh make_sync_package_v2.ps1
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
if (-not $root) { $root = Split-Path -Parent $MyInvocation.MyCommand.Path }
$date = Get-Date -Format 'yyyyMMdd'
$stage = Join-Path $env:TEMP "sky_sync_stage2_$date"
$zip = Join-Path ([Environment]::GetFolderPath('Desktop')) "sky-with-you_同步包_$date.zip"

# 目录内通用排除：缓存/虚拟环境/日志/备份
function Test-Excluded($full) {
    return ($full -match '(?i)(__pycache__|\.pyc$|\.venv[\\/]|[\\/]venv[\\/]|node_modules|\.cache[\\/]|\.log$|\.tmp$)')
}

try {
    Write-Host "root = $root"
    if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
    New-Item -ItemType Directory -Path $stage | Out-Null
    if (Test-Path $zip) { Remove-Item $zip -Force }

    # 1) 顶层文件：默认全收，排除临时/密钥/备份/大文件
    Write-Host "[1/4] 复制顶层文件..."
    $topLike = @('*.exe','*.bak','*.before_stage2','debug*.png','*_result.txt','*_log.txt',
                  'key.txt','vision_key.txt','yolov5su.pt')
    Get-ChildItem -LiteralPath $root -File -Force | ForEach-Object {
        $f = $_; $skip = $f.Name.StartsWith('_')   # 下划线开头的临时脚本一律排除
        if (-not $skip) {
            foreach ($pat in $topLike) { if ($f.Name -like $pat) { $skip = $true; break } }
        }
        if (-not $skip -and $f.Name -eq 'auto') { $skip = $true }
        if (-not $skip) { Copy-Item -LiteralPath $f.FullName -Destination (Join-Path $stage $f.Name) }
    }

    # 2) 白名单目录（user_data 单独处理）
    Write-Host "[2/4] 复制目录..."
    $dirs = @('cloud_train','core','docs','firmware','handoff','knowledge',
              'models','songs','templates','tools','webui')
    foreach ($d in $dirs) {
        $src = Join-Path $root $d
        if (-not (Test-Path $src)) { continue }
        Get-ChildItem -LiteralPath $src -Recurse -File -Force | ForEach-Object {
            if (Test-Excluded $_.FullName) { return }
            $rel = $_.FullName.Substring($src.Length + 1)
            $dst = Join-Path (Join-Path $stage $d) $rel
            New-Item -ItemType Directory -Path (Split-Path $dst) -Force | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $dst
        }
    }

    # 3) user_data：核心记忆，排除 .bak/.lock/token/backup_ 目录
    Write-Host "[3/4] 复制 user_data 记忆..."
    $ud = Join-Path $root 'user_data'
    if (Test-Path $ud) {
        Get-ChildItem -LiteralPath $ud -Recurse -File -Force | ForEach-Object {
            $full = $_.FullName
            if ($full -match '(?i)(\\backup_|\.bak$|\.lock$|mcp_token\.txt)') { return }
            $rel = $full.Substring($ud.Length + 1)
            $dst = Join-Path (Join-Path $stage 'user_data') $rel
            New-Item -ItemType Directory -Path (Split-Path $dst) -Force | Out-Null
            Copy-Item -LiteralPath $full -Destination $dst
        }
    }

    # 4) 压缩
    Write-Host "[4/4] 压缩..."
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
