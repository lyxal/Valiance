# Install script for Valiance (Windows).
# Usage: irm https://github.com/lyxal/Valiance/releases/latest/download/install.ps1 | iex

$ErrorActionPreference = "Stop"

$Repo = "lyxal/Valiance"
$InstallDir = if ($env:VALIANCE_INSTALL_DIR) { $env:VALIANCE_INSTALL_DIR } else { "$env:LOCALAPPDATA\Valiance\bin" }
$Asset = "valiance-windows.exe"
$Url = "https://github.com/$Repo/releases/latest/download/$Asset"

Write-Host "Downloading $Asset..."
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

$tmp = New-TemporaryFile
Invoke-WebRequest -Uri $Url -OutFile $tmp -UseBasicParsing

Copy-Item $tmp "$InstallDir\valiance.exe" -Force
Copy-Item $tmp "$InstallDir\vln.exe" -Force
Remove-Item $tmp -Force

Write-Host "Installed 'valiance' and 'vln' to $InstallDir"

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$InstallDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$InstallDir", "User")
    Write-Host ""
    Write-Host "Added $InstallDir to your user PATH."
    Write-Host "Restart your terminal for this to take effect."
} else {
    Write-Host "$InstallDir is already on your PATH."
}