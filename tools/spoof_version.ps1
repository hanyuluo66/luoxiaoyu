# WeChat version spoof via direct process memory patch.
#
# Why: Tencent's server rejects 3.9 clients on login ("当前微信版本过低"). The client
# reports its version from a 32-bit integer inside WeChatWin.dll. Rewriting that
# integer in the running process makes the server see a supported version while the
# UI stays 3.9.x (which is what the automation layer needs).
#
# Written in PowerShell because the Python interpreter in this environment only sees
# a filtered process list, while PowerShell sees every process.
#
# Usage:
#   pwsh -File tools\spoof_version.ps1 -Current 3.9.12.17 -Target 4.1.15.13 -DryRun
#   pwsh -File tools\spoof_version.ps1 -Current 3.9.12.17 -Target 4.1.15.13 -Apply

param(
    [Parameter(Mandatory = $true)][string]$Current,
    [Parameter(Mandatory = $true)][string]$Target,
    [switch]$Apply,
    [switch]$DryRun,
    [int]$PidOverride = 0
)

$ErrorActionPreference = 'Stop'

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

public static class WxMem
{
    const uint TH32CS_SNAPPROCESS = 0x00000002;
    const uint TH32CS_SNAPMODULE = 0x00000008;
    const uint TH32CS_SNAPMODULE32 = 0x00000010;
    const uint PROCESS_VM_READ = 0x0010;
    const uint PROCESS_VM_WRITE = 0x0020;
    const uint PROCESS_VM_OPERATION = 0x0008;
    const uint PROCESS_QUERY_INFORMATION = 0x0400;

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct PROCESSENTRY32W
    {
        public uint dwSize;
        public uint cntUsage;
        public uint th32ProcessID;
        public IntPtr th32DefaultHeapID;
        public uint th32ModuleID;
        public uint cntThreads;
        public uint th32ParentProcessID;
        public int pcPriClassBase;
        public uint dwFlags;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)] public string szExeFile;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct MODULEENTRY32W
    {
        public uint dwSize;
        public uint th32ModuleID;
        public uint th32ProcessID;
        public uint GlblcntUsage;
        public uint ProccntUsage;
        public IntPtr modBaseAddr;
        public uint modBaseSize;
        public IntPtr hModule;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 256)] public string szModule;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)] public string szExePath;
    }

    [DllImport("kernel32", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr CreateToolhelp32Snapshot(uint flags, uint pid);
    [DllImport("kernel32", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool Process32FirstW(IntPtr snap, ref PROCESSENTRY32W entry);
    [DllImport("kernel32", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool Process32NextW(IntPtr snap, ref PROCESSENTRY32W entry);
    [DllImport("kernel32", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool Module32FirstW(IntPtr snap, ref MODULEENTRY32W entry);
    [DllImport("kernel32", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool Module32NextW(IntPtr snap, ref MODULEENTRY32W entry);
    [DllImport("kernel32", SetLastError = true)]
    static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
    [DllImport("kernel32", SetLastError = true)]
    static extern bool ReadProcessMemory(IntPtr h, IntPtr addr, byte[] buf, IntPtr size, out IntPtr read);
    [DllImport("kernel32", SetLastError = true)]
    static extern bool WriteProcessMemory(IntPtr h, IntPtr addr, byte[] buf, IntPtr size, out IntPtr written);
    [DllImport("kernel32", SetLastError = true)]
    static extern bool CloseHandle(IntPtr h);

    public static List<uint> PidsOf(string exe)
    {
        var result = new List<uint>();
        IntPtr snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if (snap == (IntPtr)(-1)) return result;
        var e = new PROCESSENTRY32W();
        e.dwSize = (uint)Marshal.SizeOf(typeof(PROCESSENTRY32W));
        if (Process32FirstW(snap, ref e))
        {
            do
            {
                if (string.Equals(e.szExeFile, exe, StringComparison.OrdinalIgnoreCase))
                    result.Add(e.th32ProcessID);
            } while (Process32NextW(snap, ref e));
        }
        CloseHandle(snap);
        return result;
    }

    public static bool FindModule(uint pid, string module, out long baseAddr, out int size)
    {
        baseAddr = 0; size = 0;
        IntPtr snap = CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid);
        if (snap == (IntPtr)(-1)) return false;
        var m = new MODULEENTRY32W();
        m.dwSize = (uint)Marshal.SizeOf(typeof(MODULEENTRY32W));
        if (Module32FirstW(snap, ref m))
        {
            do
            {
                if (string.Equals(m.szModule, module, StringComparison.OrdinalIgnoreCase))
                {
                    baseAddr = m.modBaseAddr.ToInt64();
                    size = (int)m.modBaseSize;
                    CloseHandle(snap);
                    return true;
                }
            } while (Module32NextW(snap, ref m));
        }
        CloseHandle(snap);
        return false;
    }

    public static IntPtr Open(uint pid)
    {
        return OpenProcess(PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION | PROCESS_QUERY_INFORMATION, false, pid);
    }

    public static byte[] Read(IntPtr h, long addr, int size)
    {
        var buf = new byte[size];
        IntPtr read;
        if (!ReadProcessMemory(h, new IntPtr(addr), buf, new IntPtr(size), out read)) return null;
        return read.ToInt64() == size ? buf : null;
    }

    public static bool Write(IntPtr h, long addr, byte[] data)
    {
        IntPtr written;
        return WriteProcessMemory(h, new IntPtr(addr), data, new IntPtr(data.Length), out written)
               && written.ToInt64() == data.Length;
    }

    public static void Close(IntPtr h) { CloseHandle(h); }
}
'@

function Convert-VersionToHex([string]$version) {
    $parts = $version.Split('.')
    if ($parts.Count -ne 4) { throw "版本号必须是 x.y.z.w 四段，收到: $version" }
    $text = '6' + ('{0:x}' -f [int]$parts[0])
    foreach ($p in $parts[1..3]) { $text += ('{0:x2}' -f [int]$p) }
    return [Convert]::ToInt32($text, 16)
}

function Count-Pattern([byte[]]$data, [byte[]]$pattern) {
    if ($null -eq $data) { return 0 }
    $n = 0
    for ($i = 0; $i -le $data.Length - 4; $i++) {
        if ($data[$i] -eq $pattern[0] -and $data[$i + 1] -eq $pattern[1] -and
            $data[$i + 2] -eq $pattern[2] -and $data[$i + 3] -eq $pattern[3]) { $n++ }
    }
    return $n
}

function Invoke-Spoof {
    $oldValue = Convert-VersionToHex $Current
    $newValue = Convert-VersionToHex $Target
    $oldBytes = [BitConverter]::GetBytes([int]$oldValue)
    $newBytes = [BitConverter]::GetBytes([int]$newValue)
    Write-Host ("当前版本 {0} -> 0x{1:x8}" -f $Current, $oldValue)
    Write-Host ("目标版本 {0} -> 0x{1:x8}" -f $Target, $newValue)

    $pids = if ($PidOverride -gt 0) { @([uint32]$PidOverride) } else { [WxMem]::PidsOf('WeChat.exe') }
    if (-not $pids -or $pids.Count -eq 0) {
        Write-Host "✗ 没找到 WeChat.exe 进程，请先启动微信" -ForegroundColor Red
        return 2
    }
    Write-Host ("候选进程: {0}" -f ($pids -join ', '))

    $targetPid = 0; $dllBase = 0; $dllSize = 0
    foreach ($procId in $pids) {
        $b = 0L; $s = 0
        if ([WxMem]::FindModule([uint32]$procId, 'WeChatWin.dll', [ref]$b, [ref]$s)) {
            $targetPid = [uint32]$procId; $dllBase = $b; $dllSize = $s
            break
        }
    }
    if ($targetPid -eq 0) {
        Write-Host "✗ 没有进程加载 WeChatWin.dll（微信可能还没到登录界面）" -ForegroundColor Red
        return 3
    }
    Write-Host ("目标进程 pid={0}  WeChatWin.dll base=0x{1:x}  size={2:N1}MB" -f $targetPid, $dllBase, ($dllSize / 1MB))

    $handle = [WxMem]::Open($targetPid)
    if ($handle -eq [IntPtr]::Zero) {
        Write-Host "✗ OpenProcess 失败（可能需要管理员权限）" -ForegroundColor Red
        return 4
    }

    $chunkSize = 4194304
    $hits = New-Object 'System.Collections.Generic.List[long]'
    $offset = 0
    while ($offset -lt $dllSize) {
        $size = [Math]::Min($chunkSize, $dllSize - $offset)
        $data = [WxMem]::Read($handle, $dllBase + $offset, $size)
        if ($null -ne $data) {
            for ($i = 0; $i -le $data.Length - 4; $i++) {
                if ($data[$i] -eq $oldBytes[0] -and $data[$i + 1] -eq $oldBytes[1] -and
                    $data[$i + 2] -eq $oldBytes[2] -and $data[$i + 3] -eq $oldBytes[3]) {
                    $hits.Add([long]($offset + $i))
                }
            }
        }
        $offset += $size
    }

    Write-Host ("扫描完成，命中 {0} 处" -f $hits.Count)
    foreach ($h in $hits) { Write-Host ("   0x{0:x}" -f ($dllBase + $h)) }

    if ($hits.Count -eq 0) {
        [WxMem]::Close($handle)
        Write-Host "✗ 未找到版本号，请核对 -Current 与微信实际版本" -ForegroundColor Red
        return 5
    }
    if ($DryRun -or -not $Apply) {
        [WxMem]::Close($handle)
        Write-Host "（只读模式，未修改内存；加 -Apply 执行写入）" -ForegroundColor Yellow
        return 0
    }

    $written = 0
    foreach ($h in $hits) {
        if ([WxMem]::Write($handle, $dllBase + $h, $newBytes)) { $written++ }
    }
    Write-Host ("已写入 {0}/{1} 处" -f $written, $hits.Count)

    $remainOld = 0; $newCount = 0
    $offset = 0
    while ($offset -lt $dllSize) {
        $size = [Math]::Min($chunkSize, $dllSize - $offset)
        $data = [WxMem]::Read($handle, $dllBase + $offset, $size)
        $remainOld += (Count-Pattern $data $oldBytes)
        $newCount += (Count-Pattern $data $newBytes)
        $offset += $size
    }
    [WxMem]::Close($handle)

    Write-Host ("验证：旧值剩余 {0} 处，新值存在 {1} 处" -f $remainOld, $newCount)
    if ($written -gt 0 -and $remainOld -eq 0) {
        Write-Host "✓ 版本伪装完成，现在去扫码登录" -ForegroundColor Green
        return 0
    }
    return 6
}

exit (Invoke-Spoof)
