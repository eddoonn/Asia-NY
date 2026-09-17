$RepoRoot = 'C:\Users\Test Edon\Desktop\MatchForecast codes\asia-gold-reversal'
Set-Location $RepoRoot
$stamp = Get-Date -Format 'yyyyMMdd'
$log = Join-Path $RepoRoot ("logs\notify_" + $stamp + ".log")
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
('--- ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ---') | Add-Content $log
& python notify.py --trigger 2>&1 | ForEach-Object { ($_ | Out-String).Trim() | Add-Content $log }
& python notify.py 2>&1 | ForEach-Object { ($_ | Out-String).Trim() | Add-Content $log }
