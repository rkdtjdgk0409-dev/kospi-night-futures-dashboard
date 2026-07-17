@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$here=(Get-Location).Path; $out=Join-Path (Split-Path $here -Parent) 'kospi-night-futures-dashboard-v2.zip'; if(Test-Path -LiteralPath $out){Remove-Item -LiteralPath $out -Force}; Get-ChildItem -Force | Where-Object {$_.Name -ne 'kospi-night-futures-dashboard-v2.zip'} | Compress-Archive -DestinationPath $out -Force"
echo.
echo ZIP file created next to this folder.
pause
