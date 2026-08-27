$ErrorActionPreference = 'Stop'

$project = $PSScriptRoot
$controller = Get-ChildItem -LiteralPath $project -Filter '*.pyw' -File |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $controller) {
    throw 'No .pyw controller was found in the project folder.'
}

$pythonwCandidates = Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python*\pythonw.exe') -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending

if (-not $pythonwCandidates) {
    throw 'pythonw.exe was not found. Install Python 3, then run the setup batch file again.'
}

$pythonw = $null
$python = $null
foreach ($candidate in $pythonwCandidates) {
    $candidatePython = $candidate.FullName -replace 'pythonw\.exe$', 'python.exe'
    # A missing dependency is expected for some installed Python versions.
    # Do not let its stderr stop the setup before the next candidate is tried.
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $candidatePython -c 'import pystray, PIL' 2>$null
    $ErrorActionPreference = $previousErrorActionPreference
    if ($LASTEXITCODE -eq 0) {
        $pythonw = $candidate.FullName
        $python = $candidatePython
        break
    }
}

if (-not $python) {
    # No installed Python has the dependencies yet. Use the newest one and install them.
    $pythonw = $pythonwCandidates[0].FullName
    $python = $pythonw -replace 'pythonw\.exe$', 'python.exe'
    Write-Host 'Installing pystray and Pillow...'
    & $python -m pip install --user pystray pillow
    if ($LASTEXITCODE -ne 0) {
        throw 'Package installation failed. Check the Internet connection and pip, then try again.'
    }
}

$startup = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
$linkPath = Join-Path $startup 'Stock2 Unified Controller.lnk'
$shell = New-Object -ComObject WScript.Shell

# 1. 建立開機啟動捷徑
$shortcut = $shell.CreateShortcut($linkPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '"' + $controller + '"'
$shortcut.WorkingDirectory = $project
$shortcut.Description = 'Stock analysis background service controller'
$shortcut.IconLocation = $pythonw + ',0'
$shortcut.Save()

# 2. 同步在桌面建立捷徑（方便平時手動點擊開啟）
$desktop = [System.Environment]::GetFolderPath('Desktop')
$desktopLinkPath = Join-Path $desktop 'Stock2 統一控制台.lnk'
$desktopShortcut = $shell.CreateShortcut($desktopLinkPath)
$desktopShortcut.TargetPath = $pythonw
$desktopShortcut.Arguments = '"' + $controller + '"'
$desktopShortcut.WorkingDirectory = $project
$desktopShortcut.Description = 'Stock analysis background service controller'
$desktopShortcut.IconLocation = $pythonw + ',0'
$desktopShortcut.Save()

# 3. 立即在背景啟動控制台常駐
Start-Process -FilePath $pythonw -ArgumentList ('"' + $controller + '"') -WorkingDirectory $project

Write-Host '[OK] 開機自動啟動已設定完成。'
Write-Host "[OK] 桌面已建立「Stock2 統一控制台」捷徑：$desktopLinkPath"
Write-Host '[OK] 統一控制台已在背景啟動並常駐於系統匣中。'
