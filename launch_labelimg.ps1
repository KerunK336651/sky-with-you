param([int]$cat = 0)
$map = @{
  1 = "open_tree"
  2 = "follow"
  3 = "wing_light"
  4 = "light"
  5 = "focus"
  6 = "seat"
}
$root = "D:\光遇互动采集"
if ($map.ContainsKey($cat)) {
    $d = Join-Path $root $map[$cat]
    $cls = Join-Path $d "classes.txt"
    # 第三个参数为保存目录：标签 txt 与图片同目录；避免 autosave 下因无保存目录弹出"选择文件夹"导致空白
    Start-Process -FilePath "labelImg" -ArgumentList "`"$d`"","`"$cls`"","`"$d`""
}