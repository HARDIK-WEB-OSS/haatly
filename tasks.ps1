[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('setup', 'env', 'up', 'down', 'migrate', 'migrate-roundtrip', 'test', 'lint', 'typecheck', 'audit', 'check', 'run-api', 'run-worker', 'smoke', 'hooks', 'make-admin', 'import-pins')]
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

function Start-ThrowawayDatabase {
    param([hashtable]$Passwords)
    $name = "haatly-roundtrip-$(New-Secret)"
    $roles = Join-Path $Root 'docker/postgres/initdb/01-roles.sql'
    $arguments = @('run', '--detach', '--rm', '--name', $name, '--publish', '127.0.0.1::5432',
        '--env', 'POSTGRES_DB=haatly_roundtrip',
        '--volume', "${roles}:/docker-entrypoint-initdb.d/20-haatly-roles.sql:ro")
    foreach ($entry in $Passwords.GetEnumerator()) {
        $arguments += @('--env', "$($entry.Key)=$($entry.Value)")
    }
    Invoke-Native 'docker' ($arguments + @('postgis/postgis:16-3.5')) | Out-Null
    return $name
}

function Wait-ThrowawayDatabase {
    param([string]$Name)
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        docker exec $Name pg_isready -h 127.0.0.1 -U postgres -d haatly_roundtrip *> $null
        if ($LASTEXITCODE -eq 0) {
            return ((docker port $Name 5432) | Select-Object -First 1).Split(':')[-1].Trim()
        }
        Start-Sleep -Seconds 1
    }
    throw "the throwaway database $Name did not become ready"
}

function Invoke-MigrateRoundtrip {
    $passwords = @{
        POSTGRES_PASSWORD = New-Secret
        HAATLY_DB_OWNER_PASSWORD = New-Secret
        HAATLY_DB_APP_PASSWORD = New-Secret
        HAATLY_DB_OPS_PASSWORD = New-Secret
    }
    $name = Start-ThrowawayDatabase $passwords
    $saved = @{}
    try {
        $overrides = @{
            HAATLY_DB_HOST = '127.0.0.1'
            HAATLY_DB_PORT = Wait-ThrowawayDatabase $name
            HAATLY_DB_NAME = 'haatly_roundtrip'
            HAATLY_DB_OWNER_PASSWORD = $passwords.HAATLY_DB_OWNER_PASSWORD
            HAATLY_DB_APP_PASSWORD = $passwords.HAATLY_DB_APP_PASSWORD
        }
        foreach ($entry in $overrides.GetEnumerator()) {
            $saved[$entry.Key] = [Environment]::GetEnvironmentVariable($entry.Key)
            [Environment]::SetEnvironmentVariable($entry.Key, [string]$entry.Value)
        }
        Write-Host "Migration round trip on throwaway database $name (port $($overrides.HAATLY_DB_PORT))"
        Invoke-Uv @('alembic', 'upgrade', 'head')
        Invoke-Uv @('alembic', 'downgrade', 'base')
        Invoke-Uv @('alembic', 'upgrade', 'head')
        Invoke-Uv @('alembic', 'check')
    } finally {
        foreach ($entry in $saved.GetEnumerator()) {
            [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value)
        }
        docker rm --force $name *> $null
    }
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
    Invoke-Uv @('uvicorn', '--factory', 'haatly.main:create_app', '--reload', '--host', '127.0.0.1', '--port', '8000', '--no-proxy-headers')
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

function Send-LoginBody {
    param([int]$Size)
    $body = '{"email":"' + ('a' * $Size) + '","password":"x"}'
    $arguments = @{
        Uri = 'https://localhost:8443/auth/login'
        Method = 'Post'
        Body = $body
        ContentType = 'application/json'
        Headers = @{ Origin = 'https://localhost:8443' }
        SkipCertificateCheck = $true
        SkipHttpErrorCheck = $true
        UseBasicParsing = $true
    }
    return (Invoke-WebRequest @arguments).StatusCode
}

function Test-BodyLimit {
    $oversized = Send-LoginBody (2 * 1024 * 1024)
    if ($oversized -ne 413) {
        throw "Smoke: a 2 MB body got $oversized instead of 413"
    }
    $allowed = Send-LoginBody (900 * 1024)
    if ($allowed -ne 422) {
        throw "Smoke: a 900 KB body got $allowed instead of reaching the API (422)"
    }
    Write-Host "Smoke: 2 MB body -> $oversized from Caddy; 900 KB body -> $allowed from the API"
}

function Invoke-ImportPins {
    if ($Rest.Count -ne 1) {
        throw 'usage: ./tasks.ps1 import-pins <path to the data.gov.in pincode CSV>'
    }
    Invoke-Uv @('python', '-m', 'haatly.discovery.adapters.pin_import', $Rest[0])
}

function Invoke-Smoke {
    $env:API_UPSTREAM = 'api:8000'
    try {
        Invoke-Native 'docker' @('compose', '--profile', 'app', 'up', '--detach', '--build', '--wait')
        $response = Invoke-WebRequest -Uri 'https://localhost:8443/ready' -SkipCertificateCheck -UseBasicParsing
        Write-Host "Smoke: GET https://localhost:8443/ready -> $($response.StatusCode) $($response.Content)"
        Test-BodyLimit
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
    'import-pins' { Invoke-ImportPins }
}
