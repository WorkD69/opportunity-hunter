# Telegram only. OAuth is deliberately started separately by the operator.
$ErrorActionPreference = 'Stop'
try {
    $entry = Join-Path $PSScriptRoot 'telegram_setup.py'
    & python $entry
    $stageExit = $LASTEXITCODE
    if ($stageExit -ne 0) { Write-Host 'Telegram setup window failed. Safe diagnostics are in reports/setup-events.jsonl.' }
    exit $stageExit
} catch {
    Write-Host 'Telegram setup launcher failed. Ask the agent to inspect the safe stage log.'
    exit 1
}
