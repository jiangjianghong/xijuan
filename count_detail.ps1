# 让我详细统计每个文件的路由数
$files = Get-ChildItem blue_print/*.py -Exclude __init__.py
$total = 0

foreach ($file in $files) {
    $content = Get-Content $file.FullName -Raw -Encoding UTF8
    $matches = [regex]::Matches($content, '@router\.(get|post|put|delete|patch)\(')
    Write-Host "$($file.Name): $($matches.Count)"
    $total += $matches.Count
}

Write-Host "`nTotal: $total"
