param(
    [switch]$Confirm
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

function Invoke-Git {
    param(
        [Parameter(Mandatory = $true)][string]$Repository,
        [Parameter(Mandatory = $true)][string[]]$GitArguments
    )

    & git -C $Repository @GitArguments
    if ($LASTEXITCODE -ne 0) {
        throw "git $($GitArguments -join ' ') failed in $Repository"
    }
}

function Get-Repositories {
    param([Parameter(Mandatory = $true)][string]$Root)

    @($Root) + @(
        Get-ChildItem -LiteralPath $Root -Directory |
            Where-Object {
                (Test-Path -LiteralPath (Join-Path $_.FullName '.git')) -and
                (Test-Path -LiteralPath (Join-Path $_.FullName 'update_macro_data.py'))
            } |
            Select-Object -ExpandProperty FullName
    )
}

try {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw 'Git was not found. Install Git for Windows first.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot '.git'))) {
        throw "StockCenter Git repository was not found at $ProjectRoot"
    }

    $Repositories = Get-Repositories -Root $ProjectRoot
    foreach ($Repository in $Repositories) {
        $Label = Split-Path -Leaf $Repository
        if ([string]::IsNullOrWhiteSpace($Label)) { $Label = 'StockCenter' }

        Write-Host "[$Label] Reading origin/main..." -ForegroundColor Cyan
        Invoke-Git -Repository $Repository -GitArguments @('fetch', 'origin', '--prune')
        Invoke-Git -Repository $Repository -GitArguments @('rev-parse', '--verify', 'origin/main')
    }

    if (-not $Confirm) {
        Write-Host ''
        Write-Host 'Preview complete. No files were changed.' -ForegroundColor Yellow
        Write-Host 'Run from 同步至主版本.bat and enter YES to replace this computer with origin/main.' -ForegroundColor Yellow
        exit 0
    }

    foreach ($Repository in $Repositories) {
        $Label = Split-Path -Leaf $Repository
        if ([string]::IsNullOrWhiteSpace($Label)) { $Label = 'StockCenter' }

        Write-Host "[$Label] Replacing tracked files with origin/main..." -ForegroundColor Cyan
        Invoke-Git -Repository $Repository -GitArguments @('reset', '--hard', 'origin/main')

        if ($Repository -eq $ProjectRoot) {
            # Only clear untracked report files. Ignored charts/cache and all files outside reports are preserved.
            Invoke-Git -Repository $Repository -GitArguments @('clean', '-fd', '--', 'reports')
        }

        Write-Host "[$Label] Complete." -ForegroundColor Green
    }

    Write-Host ''
    Write-Host 'This computer now uses the canonical origin/main version. Restart 報表檔案管理.bat before using the UI.' -ForegroundColor Green
    exit 0
}
catch {
    Write-Host ''
    Write-Host "FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'No reset was started for repositories that did not pass the remote check.' -ForegroundColor Yellow
    exit 1
}
