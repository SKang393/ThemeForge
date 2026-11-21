# Exercise the extracted GUI without importing the source or the build environment.
param(
    [Parameter(Mandatory)][string]$Executable,
    [Parameter(Mandatory)][string]$Version
)

$ErrorActionPreference = "Stop"
Add-Type @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

public static class PortableWindow {
    [StructLayout(LayoutKind.Sequential)]
    public struct Rect {
        public int Left, Top, Right, Bottom;
        public int Width { get { return Right - Left; } }
        public int Height { get { return Bottom - Top; } }
    }
    [DllImport("user32.dll", SetLastError=true)]
    private static extern bool GetClientRect(IntPtr hwnd, out Rect rect);
    [DllImport("user32.dll", SetLastError=true)]
    private static extern bool GetWindowRect(IntPtr hwnd, out Rect rect);
    [DllImport("user32.dll", SetLastError=true)]
    private static extern bool SetWindowPos(IntPtr hwnd, IntPtr after, int x, int y, int w, int h, uint flags);
    [DllImport("user32.dll")]
    public static extern int GetSystemMetrics(int index);

    public static Rect Client(IntPtr hwnd) {
        Rect value;
        if (!GetClientRect(hwnd, out value)) throw new Win32Exception();
        return value;
    }
    public static void ResizeClient(IntPtr hwnd, int width, int height) {
        Rect outer;
        if (!GetWindowRect(hwnd, out outer)) throw new Win32Exception();
        Rect client = Client(hwnd);
        // Preserve position, Z order and activation. SetWindowPos takes the outer size.
        if (!SetWindowPos(hwnd, IntPtr.Zero, 0, 0, width + outer.Width - client.Width,
                          height + outer.Height - client.Height, 0x16)) throw new Win32Exception();
    }
}
'@

$start = [System.Diagnostics.ProcessStartInfo]::new()
$start.FileName = (Get-Item -LiteralPath $Executable).FullName
$start.WorkingDirectory = Split-Path -Parent $start.FileName
$start.UseShellExecute = $false
$start.Environment["PATH"] = "$env:SystemRoot\System32;$env:SystemRoot"
foreach ($name in "PYTHONPATH", "PYTHONHOME", "TK_LIBRARY", "TCL_LIBRARY") {
    $null = $start.Environment.Remove($name)
}
$start.Environment["HF_HUB_OFFLINE"] = "1"
$app = [System.Diagnostics.Process]::Start($start)
try {
    $deadline = [DateTime]::UtcNow.AddSeconds(40)
    do {
        Start-Sleep -Milliseconds 150
        $app.Refresh()
        if ($app.HasExited) { throw "Portable app exited during launch (code $($app.ExitCode))." }
        $hwnd = $app.MainWindowHandle
        if ($hwnd -ne [IntPtr]::Zero -and $app.MainWindowTitle -ceq "ThemeForge $Version") { break }
        if ([DateTime]::UtcNow -ge $deadline) { throw "Timed out waiting for the titled portable window." }
    } while ($true)

    $screenWidth = [PortableWindow]::GetSystemMetrics(0)
    $screenHeight = [PortableWindow]::GetSystemMetrics(1)
    $initial = [PortableWindow]::Client($hwnd)
    if ($initial.Width -le 0 -or $initial.Width -gt [Math]::Min(1440, $screenWidth) -or
        $initial.Height -le 0 -or $initial.Height -gt [Math]::Min(900, $screenHeight)) {
        throw "Initial client $($initial.Width)x$($initial.Height) exceeds the launch bounds on ${screenWidth}x${screenHeight}."
    }
    $width = [Math]::Min($initial.Width, 1280)
    $height = [Math]::Min($initial.Height, 760)
    [PortableWindow]::ResizeClient($hwnd, $width, $height)
    $compact = [PortableWindow]::Client($hwnd)
    if ($compact.Width -ne $width -or $compact.Height -ne $height) {
        throw "Requested ${width}x${height}, got $($compact.Width)x$($compact.Height)."
    }
    if (!$app.CloseMainWindow() -or !$app.WaitForExit(15000)) { throw "Portable window did not close normally." }
    if ($app.ExitCode -ne 0) { throw "Portable app exited with code $($app.ExitCode)." }

    $layout = "desktop ${screenWidth}x${screenHeight}, launch $($initial.Width)x$($initial.Height), compact ${width}x${height}"
    Write-Host $layout
    if ($env:GITHUB_OUTPUT) { "layout=$layout" >> $env:GITHUB_OUTPUT }
} finally {
    if (!$app.HasExited) { $app.Kill() }
    $app.Dispose()
}
