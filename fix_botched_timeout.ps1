$files = Get-ChildItem -Path lib, scripts, backend/scripts -Include "*.sh" -Recurse
$files += Get-Item -Path install.sh

$count = 0
foreach ($file in $files) {
    if (-not (Test-Path $file.FullName)) { continue }
    $content = Get-Content $file.FullName -Raw
    
    $newContent = [regex]::Replace($content, '-timeout -k 5 (\d+)', '-timeout $1')
    
    if ($content -ne $newContent) {
        $newContent | Set-Content -Path $file.FullName -NoNewline
        $count++
        Write-Host "Updated $($file.Name)"
    }
}
Write-Host "Total files updated: $count"
