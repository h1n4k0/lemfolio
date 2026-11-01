# Start the real GPUI window against a running local service, exercise it through
# UI Automation (what assistive technology uses) and save a screenshot plus a receipt.
param(
    [Parameter(Mandatory)][string]$Executable,
    [int]$Port = 8791,
    [string]$Output = 'reports\frontend',
    [string]$Lang = 'zh'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
Add-Type -AssemblyName System.Drawing, UIAutomationClient, UIAutomationTypes
Add-Type @'
using System; using System.Runtime.InteropServices;
public static class NativeCapture {
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hwnd, IntPtr hdc, uint flags);
  [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr hwnd, int attribute, out RECT rect, int size);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hwnd);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool InvalidateRect(IntPtr hwnd, IntPtr rect, bool erase);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}
'@
[void][NativeCapture]::SetProcessDPIAware()
# Copied from the screen rather than PrintWindow, which cannot see the Mica backdrop.
function Save-Window([IntPtr]$Handle, [string]$Path) {
    [void][NativeCapture]::SetForegroundWindow($Handle)
    Start-Sleep -Milliseconds 400
    $rect = New-Object NativeCapture+RECT
    # The visible frame only: the wider window rectangle also holds whatever is behind it.
    [void][NativeCapture]::DwmGetWindowAttribute($Handle, 9, [ref]$rect, 16)
    $bitmap = New-Object System.Drawing.Bitmap ($rect.Right - $rect.Left), ($rect.Bottom - $rect.Top)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $bitmap.Size)
    $graphics.Dispose()
    $bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png); $bitmap.Dispose()
}
function Find-Named($Root, [string]$Name) {
    $condition = New-Object System.Windows.Automation.PropertyCondition ([System.Windows.Automation.AutomationElement]::NameProperty), $Name
    # GPUI activates its accessibility tree on the first query and fills it on the next frame.
    for ($try = 0; $try -lt 30; $try++) {
        $found = $Root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $condition)
        if ($found) { return $found }
        [void][NativeCapture]::InvalidateRect($process.MainWindowHandle, [IntPtr]::Zero, $false)
        Start-Sleep -Milliseconds 300
    }
    $null
}
function Invoke-Named($Root, [string]$Name) {
    $element = Find-Named $Root $Name
    if (-not $element) { throw "UI Automation found no element named $Name" }
    $element.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
}
$process = Start-Process -FilePath $Executable -ArgumentList @('--root', $projectRoot, '--port', $Port, '--lang', $Lang) -PassThru
$text = if ($Lang -eq 'en') {
    @{ min = 'Minimize'; max = 'Maximize'; close = 'Close window'; create = 'New project'; open = 'Open paper workbench'; refresh = 'Refresh and start service'; quit = 'Quit'; suffix = '-en' }
} else {
    @{ min = '最小化'; max = '最大化'; close = '关闭窗口'; create = '新建项目'; open = '打开论文工作台'; refresh = '刷新并启动服务'; quit = '退出'; suffix = '' }
}
$receipt = [ordered]@{ language = $Lang; port = $Port; at = (Get-Date).ToString('o'); checks = [ordered]@{} }
try {
    $deadline = (Get-Date).AddSeconds(20)
    while ($process.MainWindowHandle -eq 0 -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 200; $process.Refresh() }
    if ($process.MainWindowHandle -eq 0) { throw 'The native window did not open' }
    Start-Sleep -Seconds 4
    [void][NativeCapture]::SetForegroundWindow($process.MainWindowHandle)
    Start-Sleep -Milliseconds 500
    $process.Refresh()
    Save-Window $process.MainWindowHandle (Join-Path $Output "native-connected$($text.suffix).png")
    $owner = New-Object System.Windows.Automation.PropertyCondition ([System.Windows.Automation.AutomationElement]::ProcessIdProperty), $process.Id
    $root = $null
    for ($try = 0; $try -lt 20 -and -not $root; $try++) {
        $root = [System.Windows.Automation.AutomationElement]::RootElement.FindFirst([System.Windows.Automation.TreeScope]::Children, $owner)
        if (-not $root) { Start-Sleep -Milliseconds 300 }
    }
    if (-not $root) { throw 'UI Automation cannot see the native window' }
    foreach ($name in $text.min, $text.max, $text.close, $text.create, $text.open, $text.refresh, $text.quit) { $receipt.checks["button: $name"] = [bool](Find-Named $root $name) }
    Invoke-Named $root $text.create
    Start-Sleep -Seconds 3
    $receipt.checks['created project through UI Automation'] = $true
    Invoke-Named $root $text.refresh
    Start-Sleep -Seconds 2
    Save-Window $process.MainWindowHandle (Join-Path $Output "native-after-create$($text.suffix).png")
    Invoke-Named $root $text.quit
    $receipt.checks['quit button closed the window'] = $process.WaitForExit(8000)
    $receipt.checks['existing service still healthy after quit'] = ((Invoke-RestMethod "http://127.0.0.1:$Port/api/health").status -eq 'ok')
} finally {
    if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force }
}
$receipt | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $Output "native-check$($text.suffix).json") -Encoding utf8
$receipt | ConvertTo-Json -Depth 4
