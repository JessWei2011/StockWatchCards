$ErrorActionPreference = 'Stop'

$project = $PSScriptRoot
$controller = Get-ChildItem -LiteralPath $project -Filter '*.pyw' -File |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $controller) {
    throw 'No .pyw controller was found in the project folder.'
}

$pythonw = Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python*\pythonw.exe') -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending |
    Select-Object -First 1 -ExpandProperty FullName

if (-not $pythonw) {
    throw 'pythonw.exe was not found. Install Python 3, then run the setup batch file again.'
}

$python = $pythonw -replace 'pythonw\.exe$', 'python.exe'
& $python -c 'import pystray, PIL'
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Installing pystray and Pillow...'
    & $python -m pip install --user pystray pillow
    if ($LASTEXITCODE -ne 0) {
        throw 'Package installation failed. Check the Internet connection and pip, then try again.'
    }
}

$startup = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
$linkPath = Join-Path $startup 'Stock2 Unified Controller.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($linkPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '"' + $controller + '"'
$shortcut.WorkingDirectory = $project
$shortcut.Description = 'Stock analysis background service controller'
$shortcut.IconLocation = $pythonw + ',0'
$shortcut.Save()

Write-Host '[OK] Startup configured. The controller will appear in the system tray after the next sign-in.'
