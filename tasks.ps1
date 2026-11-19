[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('setup', 'env', 'up', 'down', 'migrate', 'migrate-roundtrip', 'test', 'lint', 'typecheck', 'audit', 'check', 'run-api', 'run-worker', 'smoke', 'hooks', 'make-admin')]
    [string]$Task = 'check',
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest = @()
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Root = $PSScriptRoot
Set-Location $Root

function Invoke-Native {
    param([string]$Command, [string[]]$Arguments)
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "'$Command $($Arguments -join ' ')' failed with exit code $LASTEXITCODE"
    }
}

function Invoke-Uv {
    param([string[]]$Arguments)
    Invoke-Native 'uv' (@('run', '--locked') + $Arguments)
}

function New-Secret {
    $bytes = New-Object byte[] 24
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $generator.GetBytes($bytes)
    $generator.Dispose()
    return -join ($bytes | ForEach-Object { $_.ToString('x2') })
}

function Initialize-EnvFile {
    $target = Join-Path $Root '.env'
    if (Test-Path $target) {
        return
    }
    $lines = Get-Content (Join-Path $Root '.env.example') | ForEach-Object {
        if ($_ -match '^(\w+_PASSWORD)=$') { "$($Matches[1])=$(New-Secret)" } else { $_ }
    }
    [System.IO.File]::WriteAllText($target, (($lines -join "`n") + "`n"))
    Write-Host 'Created .env with fresh random secrets.'
}

function Invoke-Setup {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw 'uv is missing. Install it with: winget install --id astral-sh.uv -e'
    }
    Invoke-Native 'uv' @('python', 'install', '3.12')
    Invoke-Native 'uv' @('sync', '--locked')
    Initialize-EnvFile
    Invoke-Hooks
}

function Invoke-Hooks {
    Invoke-Uv @('pre-commit', 'install', '--install-hooks')
}

function Invoke-Up {
    Invoke-Native 'docker' @('compose', 'up', '--detach', '--wait', 'postgres', 'tigerbeetle', 'caddy')
}

function Invoke-Down {
    Invoke-Native 'docker' (@('compose', '--profile', 'app', 'down') + $Rest)
}

function Invoke-Migrate {
    Invoke-Uv @('alembic', 'upgrade', 'head')
}

function Invoke-MigrateRoundtrip {
    Invoke-Uv @('alembic', 'upgrade', 'head')
    Invoke-Uv @('alembic', 'downgrade', 'base')
    Invoke-Uv @('alembic', 'upgrade', 'head')
    Invoke-Uv @('alembic', 'check')
}

function Invoke-Test {
    Invoke-Uv (@('pytest', '--cov', '--cov-report=term-missing', '--cov-report=xml', '--cov-report=json') + $Rest)
    Invoke-Uv @('python', 'tools/check_package_coverage.py', 'coverage.json')
}

function Invoke-Lint {
    Invoke-Uv @('python', 'tools/check_no_comments.py')
    Invoke-Uv @('ruff', 'check', '.')
    Invoke-Uv @('ruff', 'format', '--check', '.')
    Invoke-Uv @('lint-imports')
}

function Invoke-Typecheck {
    Invoke-Uv @('mypy')
}

function Invoke-Audit {
    Invoke-Uv @('pip-audit', '--skip-editable')
    if (Get-Command gitleaks -ErrorAction SilentlyContinue) {
        Invoke-Native 'gitleaks' @('git', '--redact', '--no-banner', '.')
    } else {
        Write-Warning 'gitleaks is not installed locally; CI runs it on every pull request.'
    }
}

function Invoke-Check {
    Invoke-Lint
    Invoke-Typecheck
    Invoke-MigrateRoundtrip
    Invoke-Test
    Invoke-Audit
}

function Invoke-RunApi {
    Invoke-Uv @('uvicorn', '--factory', 'haatly.main:create_app', '--reload', '--host', '127.0.0.1', '--port', '8000')
}

function Invoke-RunWorker {
    Invoke-Uv @('haatly-worker')
}

function Invoke-MakeAdmin {
    if ($Rest.Count -ne 1) {
        throw 'usage: ./tasks.ps1 make-admin <email>'
    }
    Invoke-Uv @('python', '-m', 'haatly.identity.adapters.make_admin', $Rest[0])
}

function Invoke-Smoke {
    $env:API_UPSTREAM = 'api:8000'
    try {
        Invoke-Native 'docker' @('compose', '--profile', 'app', 'up', '--detach', '--build', '--wait')
        $response = Invoke-WebRequest -Uri 'https://localhost:8443/ready' -SkipCertificateCheck -UseBasicParsing
        Write-Host "Smoke: GET https://localhost:8443/ready -> $($response.StatusCode) $($response.Content)"
        Start-Sleep -Seconds 10
        $workerState = (docker compose --profile app ps --all --format '{{.State}}' worker).Trim()
        if ($workerState -ne 'running') {
            throw "Smoke: worker is '$workerState' instead of running"
        }
        Write-Host 'Smoke: worker still running after 10 s'
    } finally {
        Remove-Item Env:API_UPSTREAM
    }
}

switch ($Task) {
    'setup' { Invoke-Setup }
    'env' { Initialize-EnvFile }
    'up' { Invoke-Up }
    'down' { Invoke-Down }
    'migrate' { Invoke-Migrate }
    'migrate-roundtrip' { Invoke-MigrateRoundtrip }
    'test' { Invoke-Test }
    'lint' { Invoke-Lint }
    'typecheck' { Invoke-Typecheck }
    'audit' { Invoke-Audit }
    'check' { Invoke-Check }
    'run-api' { Invoke-RunApi }
    'run-worker' { Invoke-RunWorker }
    'smoke' { Invoke-Smoke }
    'hooks' { Invoke-Hooks }
    'make-admin' { Invoke-MakeAdmin }
}
