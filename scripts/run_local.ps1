[CmdletBinding()]
param(
    [string]$EnvFile = ".env.local",
    [string]$ConfigFile = "config/bot.local.json",

    [ValidateSet("run", "export", "channel", "admin")]
    [string]$Command = "run",

    [string[]]$CommandArguments = @()
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$envPath = (Resolve-Path -LiteralPath (Join-Path $projectRoot $EnvFile)).Path
$configPath = (Resolve-Path -LiteralPath (Join-Path $projectRoot $ConfigFile)).Path

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Virtuelle Umgebung fehlt. Zuerst: python -m venv .venv"
}

$env:BOT_CONFIG_FILE = $configPath
$env:BOT_DATA_DIR = Join-Path $projectRoot ".local\data"
$env:BOT_PHOTO_DIR = Join-Path $projectRoot ".local\photos"

Write-Host "Lokaler Testbetrieb; Daten bleiben isoliert unter .local/."
if ($Command -eq "run") {
    Write-Host "Bot beenden mit Strg+C."
}
& $python -m telegram_geo_report_bot --env-file $envPath $Command @CommandArguments
exit $LASTEXITCODE
