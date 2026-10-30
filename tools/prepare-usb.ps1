<#
Claude IT Technician - main-PC helper (Windows PowerShell 5.1+). UNTESTED ON WINDOWS until you run it: read it first.

What it does, in order:
  1. Shows THIS computer's Wi-Fi/LAN configuration (read-only).
  2. Lists removable/USB drives (read-only) and lets YOU choose one.
  3. Copies the portable agent folder to <drive>:\CITAgent after you type YES. It never formats, never deletes, never overwrites.
Usage:  powershell -ExecutionPolicy Bypass -File tools\prepare-usb.ps1 -Source "C:\Users\you\Downloads\cit-agent-portable"
  (-Source = the folder or .zip of the 'cit-agent-portable' artifact from the GitHub Actions run)
#>
param([Parameter(Mandatory = $true)][string]$Source)
$ErrorActionPreference = 'Stop'

Write-Host "`n=== 1. This computer's network (read-only) ===" -ForegroundColor Cyan
Get-NetIPConfiguration | Where-Object { $_.IPv4Address -and $_.NetAdapter.Status -eq 'Up' } | ForEach-Object {
    $ip = $_.IPv4Address[0]
    Write-Host ("Adapter : {0}" -f $_.InterfaceAlias)
    Write-Host ("IPv4    : {0}/{1}" -f $ip.IPAddress, $ip.PrefixLength)
    Write-Host ("Gateway : {0}" -f ($_.IPv4DefaultGateway.NextHop -join ', '))
    Write-Host ("DNS     : {0}" -f ($_.DNSServer.ServerAddresses -join ', '))
}
Get-NetConnectionProfile | ForEach-Object { Write-Host ("Network '{0}' category: {1}  (use Private on BOTH PCs; Public blocks incoming connections)" -f $_.Name, $_.NetworkCategory) }
$ssid = (netsh wlan show interfaces) -match '^\s*SSID\s*:' | Select-Object -First 1
if ($ssid) { Write-Host ("Wi-Fi   : {0}" -f ($ssid.Trim())) }

Write-Host "`n=== 2. Removable drives (read-only listing) ===" -ForegroundColor Cyan
$vols = @(Get-Volume | Where-Object { $_.DriveLetter -and $_.DriveType -eq 'Removable' })
if (-not $vols) { Write-Host "No removable drive found. Re-plug the USB drive and run again."; exit 1 }
$vols | ForEach-Object { Write-Host ("{0}:  label='{1}'  fs={2}  size={3:N1} GB  free={4:N1} GB" -f $_.DriveLetter, $_.FileSystemLabel, $_.FileSystem, ($_.Size / 1GB), ($_.SizeRemaining / 1GB)) }
$letter = Read-Host "Type the drive letter to use (nothing else on it is touched)"
$vol = $vols | Where-Object { $_.DriveLetter -eq $letter.Trim().TrimEnd(':').ToUpper() }
if (-not $vol) { Write-Host "That is not one of the removable drives listed. Nothing was changed."; exit 1 }

Write-Host "`n=== 3. Copy the portable agent ===" -ForegroundColor Cyan
$work = $Source
if ($Source -like '*.zip') { $work = Join-Path $env:TEMP ('cit-agent-' + [guid]::NewGuid().ToString('N').Substring(0, 8)); Expand-Archive -LiteralPath $Source -DestinationPath $work }
$exe = Get-ChildItem -LiteralPath $work -Recurse -Filter 'cit-agent.exe' | Select-Object -First 1
if (-not $exe) { Write-Host "cit-agent.exe not found in $Source"; exit 1 }
$srcDir = $exe.DirectoryName
$dest = "$($vol.DriveLetter):\CITAgent"
if (Test-Path -LiteralPath $dest) { Write-Host "$dest already exists - I will not overwrite or delete anything. Rename it or choose another drive."; exit 1 }
Write-Host "Will COPY the files below to $dest (no format, no delete, no overwrite):"
Get-ChildItem -LiteralPath $srcDir -File | ForEach-Object { Write-Host ("  {0}  ({1:N0} KB)" -f $_.Name, ($_.Length / 1KB)) }
if ((Read-Host "Type YES to copy") -ne 'YES') { Write-Host "Cancelled. Nothing was changed."; exit 0 }
New-Item -ItemType Directory -Path $dest | Out-Null
Get-ChildItem -LiteralPath $srcDir -File | Copy-Item -Destination $dest
foreach ($f in Get-ChildItem -LiteralPath $srcDir -File) {
    $a = (Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash
    $b = (Get-FileHash -LiteralPath (Join-Path $dest $f.Name) -Algorithm SHA256).Hash
    Write-Host ("{0,-22} {1}" -f $f.Name, $(if ($a -eq $b) { 'copied, SHA-256 verified' } else { 'MISMATCH - do not use' }))
}
Write-Host "`nDone. On the SECOND PC: plug the drive in, open $dest, double-click start-agent.bat." -ForegroundColor Green
