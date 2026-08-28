param(
    [string]$CommitMessage,
    [switch]$ValidateOnly
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

function Publish-Repository {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string]$Repository,
        [Parameter(Mandatory = $true)][string]$Message
    )

    Write-Host ""
    Write-Host "[$Label] Staging all changes..." -ForegroundColor Cyan
    Invoke-Git -Repository $Repository -GitArguments @('add', '-A')

    & git -C $Repository diff --cached --quiet
    $DiffExit = $LASTEXITCODE
    if ($DiffExit -eq 1) {
        Write-Host "[$Label] Creating commit..." -ForegroundColor Cyan
        Invoke-Git -Repository $Repository -GitArguments @('commit', '-m', $Message)
    }
    elseif ($DiffExit -eq 0) {
        Write-Host "[$Label] No new changes to commit." -ForegroundColor DarkGray
    }
    else {
        throw "Unable to inspect staged changes in $Repository"
    }

    Write-Host "[$Label] Pushing current branch..." -ForegroundColor Cyan
    Invoke-Git -Repository $Repository -GitArguments @('push', 'origin', 'HEAD')
    Write-Host "[$Label] Complete." -ForegroundColor Green
}

try {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw 'Git was not found. Install Git for Windows first.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot '.git'))) {
        throw "Stock2 Git repository was not found at $ProjectRoot"
    }

    $NestedRepositories = @(
        Get-ChildItem -LiteralPath $ProjectRoot -Directory |
            Where-Object {
                (Test-Path -LiteralPath (Join-Path $_.FullName '.git')) -and
                (Test-Path -LiteralPath (Join-Path $_.FullName 'update_macro_data.py'))
            }
    )
    if ($NestedRepositories.Count -ne 1) {
        throw "Expected exactly one nested macro repository, found $($NestedRepositories.Count)."
    }

    if ($ValidateOnly) {
        Write-Host "Validation passed. Root: $ProjectRoot" -ForegroundColor Green
        Write-Host "Validation passed. Macro: $($NestedRepositories[0].FullName)" -ForegroundColor Green
        exit 0
    }

    if ([string]::IsNullOrWhiteSpace($CommitMessage)) {
        $CommitMessage = Read-Host 'Commit message (press Enter for default)'
    }
    if ([string]::IsNullOrWhiteSpace($CommitMessage)) {
        $CommitMessage = 'Sync project updates'
    }

    Publish-Repository -Label 'Macro' -Repository $NestedRepositories[0].FullName -Message $CommitMessage
    Publish-Repository -Label 'Stock2' -Repository $ProjectRoot -Message $CommitMessage

    Write-Host ""
    Write-Host 'Both repositories were committed and pushed successfully.' -ForegroundColor Green
    exit 0
}
catch {
    Write-Host ""
    Write-Host "FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'If the remote branch is ahead, run the pull-all script first and retry.' -ForegroundColor Yellow
    exit 1
}
