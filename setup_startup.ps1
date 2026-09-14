$ErrorActionPreference = 'Stop'

$project = $PSScriptRoot
$controller = Get-ChildItem -LiteralPath $project -Filter '*.pyw' -File |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $controller) {
    throw 'No .pyw controller was found in the project folder.'
}

$python = $null
$pythonw = $null

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCmd -and $pythonCmd.Source) {
    $candidateDir = Split-Path -Parent $pythonCmd.Source
    $candPythonw = Join-Path $candidateDir 'pythonw.exe'
    if (Test-Path -LiteralPath $candPythonw) {
        $python = $pythonCmd.Source
        $pythonw = $candPythonw
    }
}

if (-not $python -or -not $pythonw) {
    $pythonDir = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311'
    $candidatePythonw = Join-Path $pythonDir 'pythonw.exe'
    $candidatePython = Join-Path $pythonDir 'python.exe'
    if ((Test-Path -LiteralPath $candidatePythonw) -and (Test-Path -LiteralPath $candidatePython)) {
        $python = $candidatePython
        $pythonw = $candidatePythonw
    }
}

if (-not $python -or -not $pythonw) {
    throw "Python 3.10+ (python.exe & pythonw.exe) was not found. Install Python 3.10+ and add it to PATH, then run this file again."
}

$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $python -c 'import pystray, PIL' 2>$null
$ErrorActionPreference = $previousErrorActionPreference
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Installing pystray and Pillow for Python 3.11...'
    & $python -m pip install --user pystray pillow
    if ($LASTEXITCODE -ne 0) {
        throw 'Package installation failed. Check the Internet connection and pip, then run this file again.'
    }
}

$startup = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
$desktop = [System.Environment]::GetFolderPath('Desktop')

# 移除舊的 Stock2 捷徑（若存在）
$oldLinkPath = Join-Path $startup 'Stock2 Unified Controller.lnk'
if (Test-Path -LiteralPath $oldLinkPath) { Remove-Item -LiteralPath $oldLinkPath -Force }
$oldDesktopPath = Join-Path $desktop 'Stock2 統一控制台.lnk'
if (Test-Path -LiteralPath $oldDesktopPath) { Remove-Item -LiteralPath $oldDesktopPath -Force }

$linkPath = Join-Path $startup 'StockCenter Unified Controller.lnk'
$shell = New-Object -ComObject WScript.Shell

# 1. 建立開機啟動捷徑
$shortcut = $shell.CreateShortcut($linkPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '"' + $controller + '"'
$shortcut.WorkingDirectory = $project
$shortcut.Description = 'StockCenter analysis background service controller'
$shortcut.IconLocation = $pythonw + ',0'
$shortcut.Save()

# 2. 同步在桌面建立捷徑（方便平時手動點擊開啟）
$desktopLinkPath = Join-Path $desktop 'StockCenter 統一控制台.lnk'
$desktopShortcut = $shell.CreateShortcut($desktopLinkPath)
$desktopShortcut.TargetPath = $pythonw
$desktopShortcut.Arguments = '"' + $controller + '"'
$desktopShortcut.WorkingDirectory = $project
$desktopShortcut.Description = 'StockCenter analysis background service controller'
$desktopShortcut.IconLocation = $pythonw + ',0'
$desktopShortcut.Save()

# 3. 立即在背景啟動控制台常駐
Start-Process -FilePath $pythonw -ArgumentList ('"' + $controller + '"') -WorkingDirectory $project

Write-Host '[OK] 開機自動啟動已設定完成。'
Write-Host "[OK] 桌面已建立「StockCenter 統一控制台」捷徑：$desktopLinkPath"
Write-Host '[OK] 統一控制台已在背景啟動並常駐於系統匣中。'
