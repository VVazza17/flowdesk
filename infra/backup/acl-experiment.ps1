<#
.SYNOPSIS
  Measures what the two NTFS permission variants in docs/BACKUP.md actually allow. Reproduces the finding that
  "deny Delete" is not append-only: under Variant D an open asking only for the data-write right overwrote an
  existing backup in place and truncated it (24 bytes to 1); Variant W refused every overwrite.

.DESCRIPTION
  Runs the EXACT icacls commands from docs/BACKUP.md against a throwaway folder in %TEMP%, using the CURRENT
  user as the stand-in for the flowdesk-backup account, then resets and removes the folder. It creates no
  account and touches no service.

  Run it in an ordinary (NOT elevated) PowerShell window. An elevated token has privileges that bypass ACLs, so
  the numbers would mean nothing; the script refuses to run elevated.

  powershell -NoProfile -ExecutionPolicy Bypass -File infra\backup\acl-experiment.ps1
#>
$ErrorActionPreference = 'Stop'
$acct = "$env:USERDOMAIN\$env:USERNAME"
$elevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($elevated) { Write-Output 'STOP: this PowerShell is elevated, so ACLs would not apply. Open a normal one.'; exit 3 }

Add-Type -TypeDefinition @'
using System; using System.Runtime.InteropServices; using Microsoft.Win32.SafeHandles;
public static class FlowdeskWin32 {
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
  public static extern SafeFileHandle CreateFileW(string f, uint access, uint share, IntPtr sa, uint disp, uint flags, IntPtr t);
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool WriteFile(SafeFileHandle h, byte[] b, uint n, out uint w, IntPtr o);
}
'@
$FILE_WRITE_DATA = [uint32]0x2; $GENERIC_WRITE = [uint32]0x40000000
$OPEN_EXISTING = [uint32]3; $CREATE_ALWAYS = [uint32]2

function Raw-Open([string]$path, [uint32]$access, [uint32]$disposition) {
    $h = [FlowdeskWin32]::CreateFileW($path, $access, 7, [IntPtr]::Zero, $disposition, 0x80, [IntPtr]::Zero)
    if ($h.IsInvalid) { return 'denied' }
    $n = [uint32]0; [void][FlowdeskWin32]::WriteFile($h, [byte[]](88), 1, [ref]$n, [IntPtr]::Zero); $h.Close()
    return 'ALLOWED'
}
function Try-Op([scriptblock]$op) { try { & $op | Out-Null; 'allowed' } catch { 'denied' } }

function Measure-Variant([string[]]$commands) {
    $t = Join-Path $env:TEMP ('flowdesk-acl-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory $t | Out-Null
    $r = [ordered]@{}
    try {
        foreach ($c in $commands) {
            $line = $c.Replace('C:\FlowdeskBackups', $t).Replace('flowdesk-backup', $acct)
            $o = cmd /c "$line 2>&1"
            if ($LASTEXITCODE -ne 0) { throw "icacls failed: $c : $o" }
        }
        # A backup already on the destination, created through the same restricted account.
        $fs = [IO.File]::Open("$t\old.tar.age", 'CreateNew', 'Write')
        $b = [Text.Encoding]::UTF8.GetBytes('previous backup contents'); $fs.Write($b, 0, $b.Length); $fs.Close()
        $r['List the folder; read a backup'] = Try-Op { Get-ChildItem $t; [IO.File]::ReadAllText("$t\old.tar.age") }
        $r['Create a new file and fill it in one open (an upload)'] = Try-Op {
            $f = [IO.File]::Open("$t\new.tar.age", 'CreateNew', 'Write'); $b = [byte[]](1, 2, 3); $f.Write($b, 0, 3); $f.Close() }
        $r['Delete a backup'] = Try-Op { Remove-Item "$t\new.tar.age" -Force }
        $r['Rename a backup (so: no temp name then rename)'] = Try-Op { Move-Item "$t\new.tar.age" "$t\renamed.tar.age" }
        $r['Create a subfolder'] = Try-Op { New-Item -ItemType Directory "$t\sub" }
        $r['Append; or overwrite asking for generic write (what most runtimes ask)'] =
            if ((Raw-Open "$t\old.tar.age" $GENERIC_WRITE $OPEN_EXISTING) -eq 'ALLOWED') { 'ALLOWED' } else { 'denied' }
        $r['Open an existing file asking ONLY for the data-write right, write 1 byte in place'] = Raw-Open "$t\old.tar.age" $FILE_WRITE_DATA $OPEN_EXISTING
        $before = (Get-Item "$t\old.tar.age").Length
        $r['Truncating overwrite (CREATE_ALWAYS) asking ONLY for the data-write right'] = Raw-Open "$t\old.tar.age" $FILE_WRITE_DATA $CREATE_ALWAYS
        $r['  size of the existing backup afterwards'] = "$before bytes -> $((Get-Item "$t\old.tar.age").Length) bytes"
    }
    finally {
        icacls $t /reset /T /C /Q | Out-Null
        Remove-Item $t -Recurse -Force -ErrorAction SilentlyContinue
    }
    return $r
}

$base = @(
    'icacls C:\FlowdeskBackups /inheritance:r',
    'icacls C:\FlowdeskBackups /grant "SYSTEM:(OI)(CI)F" "BUILTIN\Administrators:(OI)(CI)F"'
)
$variantW = $base + @(
    'icacls C:\FlowdeskBackups /grant "flowdesk-backup:(RX,WD)"',
    'icacls C:\FlowdeskBackups /grant "flowdesk-backup:(OI)(IO)(RX)"',
    'icacls C:\FlowdeskBackups /deny "flowdesk-backup:(OI)(IO)(WD,AD,WEA,WA,DE)"',
    'icacls C:\FlowdeskBackups /deny "flowdesk-backup:(DC)"'
)
$variantD = $base + @(
    'icacls C:\FlowdeskBackups /grant "flowdesk-backup:(OI)(CI)(RX,WD,WEA,WA)"',
    'icacls C:\FlowdeskBackups /deny "flowdesk-backup:(OI)(CI)(DE,DC)"'
)

$w = Measure-Variant $variantW
$d = Measure-Variant $variantD

Write-Output ''
Write-Output ('{0,-84} {1,-26} {2}' -f 'Operation', 'Variant W (write-once)', 'Variant D (deny Delete)')
Write-Output ('-' * 140)
foreach ($k in $w.Keys) { Write-Output ('{0,-84} {1,-26} {2}' -f $k, $w[$k], $d[$k]) }
Write-Output ''
if ($d['Open an existing file asking ONLY for the data-write right, write 1 byte in place'] -eq 'ALLOWED' -and
    $w['Open an existing file asking ONLY for the data-write right, write 1 byte in place'] -eq 'denied') {
    Write-Output 'RESULT: as documented. Variant D lets an existing backup be modified in place; Variant W refuses every overwrite.'
} else {
    Write-Output 'RESULT: DIFFERENT from docs/BACKUP.md. Read the table before relying on either variant, and tell whoever maintains the docs.'
}
