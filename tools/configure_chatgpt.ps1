param(
    [string]$Database = 'partie.db',
    [switch]$Register
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython -PathType Leaf)) {
    throw 'Créez .venv et installez le paquet avec son option MCP avant la configuration.'
}
if (-not [System.IO.Path]::IsPathRooted($Database)) {
    $Database = Join-Path $projectRoot $Database
}
$savePath = (Resolve-Path -LiteralPath $Database).Path
$keyPath = Join-Path (Split-Path -Parent $savePath) '.rpg-bridge\signing.key'
$backupPath = Join-Path (Split-Path -Parent $savePath) '.rpg-bridge\backups'

$checkOutput = & $projectPython -m rpg.mcp_server --db $savePath --secret-file $keyPath --check
if ($LASTEXITCODE -ne 0) { throw 'La vérification MCP a échoué ; aucune inscription effectuée.' }
$ready = $checkOutput | ConvertFrom-Json
$boundActor = $ready.actor
Write-Output $checkOutput

if (-not $Register) {
    & $projectPython -m rpg.mcp_server --db $savePath --actor $boundActor --secret-file $keyPath --backup-dir $backupPath --print-config
    if ($LASTEXITCODE -ne 0) { throw 'Impossible de produire la configuration.' }
    Write-Output 'Configuration prête. Utilisez -Register pour ajouter le serveur au client local.'
    exit 0
}

$clientCommand = Get-Command codex -ErrorAction Stop
$registeredOutput = & $clientCommand.Source mcp list --json
if ($LASTEXITCODE -ne 0) { throw 'Impossible de lire la liste des serveurs du client.' }
$registeredNames = @($registeredOutput | ConvertFrom-Json | ForEach-Object { $_.name })
if ($registeredNames -contains 'rpg-engine') {
    throw 'rpg-engine existe déjà : vérifiez sa configuration avant de le remplacer.'
}
& $clientCommand.Source mcp add rpg-engine -- $projectPython -m rpg.mcp_server --db $savePath --actor $boundActor --secret-file $keyPath --backup-dir $backupPath
if ($LASTEXITCODE -ne 0) { throw 'Le client a refusé la configuration MCP.' }
Write-Output 'Serveur ajouté. Redémarrez le client local, puis vérifiez /mcp et demandez rpg_get_state.'
