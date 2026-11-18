$ErrorActionPreference = 'Stop'
$sourcePath = Join-Path $PSScriptRoot 'Start-OpportunitySetup.ps1'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($sourcePath, [ref]$tokens, [ref]$parseErrors)
$report = [ordered]@{ powershell_major=$PSVersionTable.PSVersion.Major; parse_error_count=@($parseErrors).Count; parse_errors=@($parseErrors | ForEach-Object { @{id=$_.ErrorId; line=$_.Extent.StartLineNumber} }) }
try {
    . (Join-Path $PSScriptRoot '..\..\hermes-platform\scripts\local.settings.ps1')
    $report.settings_loaded = $true
    # Private SSH only (see CLAUDE.md): the public $HermesServer address is never used.
    $report.server_configured = [bool]$HermesPrivateServer
    if (-not $HermesPrivateServer) { throw 'PRIVATE_SSH_NOT_CONFIGURED' }
    $report.identity_exists = Test-Path -LiteralPath $HermesIdentityFile
    $sshOptions = @('-i',$HermesIdentityFile,'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15')
    if ($HermesHostKeyAlias) { $sshOptions += @('-o',"HostKeyAlias=$HermesHostKeyAlias") }
    $null = & ssh @sshOptions $HermesPrivateServer 'echo OH_PRIVATE_SSH_OK'
    $report.private_ssh_ok = ($LASTEXITCODE -eq 0)
    if (-not $report.private_ssh_ok) { throw 'PRIVATE_SSH_FAILED' }
    $reply = 'transport-probe-no-secret' | & ssh @sshOptions $HermesPrivateServer 'docker exec -i -u hermes -w /opt/hermes hermes-agent /opt/hermes/.venv/bin/python /opt/data/profiles/opportunity-hunter/operator/configure_telegram.py'
    $report.ssh_exit_code=$LASTEXITCODE
    $report.stdin_verified=($LASTEXITCODE -eq 1 -and ($reply | ConvertFrom-Json).error -eq 'Invalid token format')
} catch {
    $report.failed=$true
    $report.error_type=$_.Exception.GetType().Name
    $report.error_id=$_.FullyQualifiedErrorId
}
$report | ConvertTo-Json -Depth 4
