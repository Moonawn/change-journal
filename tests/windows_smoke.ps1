$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('cj-windows-' + [guid]::NewGuid().ToString())
$journalDir = Join-Path $testRoot ([string][char]0x4E2D + ' space')
$journalScript = Join-Path $PSScriptRoot '../scripts/change_journal.py'
$hermesScript = Join-Path $PSScriptRoot '../scripts/hermes_adapter.py'
$summary = [string][char]0x4E2D + [char]0x6587

try {
    $record = python "$journalScript" --root "$journalDir" record `
        --actor test --source semantic --phase applied --summary "$summary" `
        --primary-path 'C:/Projects/demo' --changed-path 'C:/Projects/demo/file.txt' `
        --work-id smoke --work-title "$summary" --idempotency-key smoke --render | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $record.views_updated) { throw 'PowerShell record failed' }
    $query = python "$journalScript" --root "$journalDir" query --work-id smoke | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $query.events[0].summary -ne $summary) { throw 'Unicode roundtrip failed' }
    $payload = @{ profile_id = 'local'; external_event_id = 'smoke'; summary = $summary } | ConvertTo-Json
    $hermes = $payload | python "$hermesScript" --root "$journalDir" | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $hermes.event_id) { throw 'UTF-8 stdin failed' }
    $health = python "$journalScript" --root "$journalDir" doctor | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $health.ok) { throw 'Journal validation failed' }
    Write-Output ('PowerShell ' + $PSVersionTable.PSVersion + ': record, query, render, UTF-8 stdin and doctor passed')
} finally {
    if (Test-Path -LiteralPath $testRoot) { Remove-Item -LiteralPath $testRoot -Recurse -Force }
}
