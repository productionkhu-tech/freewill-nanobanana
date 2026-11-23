# UI Automation helper for the EXE tests (Windows PowerShell 5.1, no extra modules).
# usage: powershell -NoProfile -File uia.ps1 -Hwnd <n> -Mode text            -> the window's visible text, one name per line
#        powershell -NoProfile -File uia.ps1 -Hwnd <n> -Mode invoke -Name x  -> invoke the first element whose name starts with x
param([Int64]$Hwnd, [string]$Mode = "text", [string]$Name = "")
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$Hwnd)
if (-not $root) { Write-Output "NO_WINDOW"; exit 2 }
$all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
if ($Mode -eq "text") {
    foreach ($e in $all) {
        $n = $e.Current.Name
        if ($n) { Write-Output $n }
    }
    exit 0
}
if ($Mode -eq "invoke") {
    foreach ($e in $all) {
        $n = $e.Current.Name
        if ($n -and $n.StartsWith($Name)) {
            $p = $null
            if ($e.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$p)) {
                $p.Invoke(); Write-Output "INVOKED"; exit 0
            }
        }
    }
    Write-Output "NOT_FOUND"; exit 3
}
if ($Mode -eq "rect") {
    foreach ($e in $all) {
        $n = $e.Current.Name
        if ($n -and $n.StartsWith($Name)) {
            $r = $e.Current.BoundingRectangle
            Write-Output ("{0} {1} {2} {3}" -f [int]$r.X, [int]$r.Y, [int]$r.Width, [int]$r.Height); exit 0
        }
    }
    Write-Output "NOT_FOUND"; exit 3
}
