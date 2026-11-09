<#
.SYNOPSIS
  Read-only survey of what ANY authenticated Windows account can read or change on this machine, to size the
  exposure of the SFTP backup account (docs/BACKUP.md, "What the account can reach").

.DESCRIPTION
  A dedicated, non-administrator account such as flowdesk-backup gets, whatever local groups it is in, the
  permissions granted to Everyone, NT AUTHORITY\Authenticated Users and NT AUTHORITY\NETWORK. On a default
  Windows install Authenticated Users hold Modify on anything created directly under the root of a drive, and
  new folders there inherit it. Removing the account from the Users group does not remove that.

  For each location this prints, separately, what the Users group grants and what Everyone / Authenticated Users /
  NETWORK grant. It changes nothing, except that it briefly creates and deletes an empty folder at the root of C:\
  to show what a new top-level folder inherits (skipped quietly if that is not permitted).

  powershell -NoProfile -ExecutionPolicy Bypass -File infra\backup\acl-survey.ps1
#>
$ErrorActionPreference = 'SilentlyContinue'
$always = @('Everyone', 'NT AUTHORITY\Authenticated Users', 'NT AUTHORITY\NETWORK', 'NT AUTHORITY\This Organization')
$fsr = [Security.AccessControl.FileSystemRights]
$readBits = $fsr::ReadData
$writeBits = $fsr::WriteData -bor $fsr::AppendData -bor $fsr::Delete -bor $fsr::DeleteSubdirectoriesAndFiles

function Survey([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return }
    $acl = Get-Acl -LiteralPath $path
    $users = @{R = $false; W = $false}; $anyone = @{R = $false; W = $false}
    foreach ($ace in $acl.Access) {
        if ($ace.AccessControlType -ne 'Allow') { continue }
        $who = $ace.IdentityReference.Value
        $bucket = $null
        if ($who -eq 'BUILTIN\Users') { $bucket = $users } elseif ($always -contains $who) { $bucket = $anyone }
        if ($null -eq $bucket) { continue }
        if (($ace.FileSystemRights -band $readBits) -ne 0) { $bucket.R = $true }
        if (($ace.FileSystemRights -band $writeBits) -ne 0) { $bucket.W = $true }
    }
    '{0,-34} read: Users={1,-5} Anyone={2,-5}   write: Users={3,-5} Anyone={4,-5}' -f $path, $users.R, $anyone.R, $users.W, $anyone.W
}

'"Anyone" = Everyone / Authenticated Users / NETWORK: what the account gets even if removed from Users.'
'Local fixed drives: ' + ((Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Root -match '^[A-Z]:\\$' } | ForEach-Object { $_.Root }) -join ' ')
''
foreach ($p in @('C:\', 'C:\Windows', 'C:\Program Files', 'C:\Program Files (x86)', 'C:\ProgramData', 'C:\Users', 'C:\Users\Public',
                 $env:USERPROFILE, (Join-Path $env:USERPROFILE 'Documents'))) { Survey $p }
foreach ($d in (Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Root -match '^[A-Z]:\\$' -and $_.Root -ne 'C:\' })) { Survey $d.Root }

''
'Top-level folders directly under each drive root, and whether Authenticated Users can modify them'
'(these are the folders an SFTP-only account could read AND change or delete):'
foreach ($d in (Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Root -match '^[A-Z]:\\$' })) {
    Get-ChildItem -LiteralPath $d.Root -Directory -Force | Where-Object { $_.Name -notin @('Windows', 'Program Files', 'Program Files (x86)', 'ProgramData', 'Users', '$Recycle.Bin', 'System Volume Information', 'Recovery', 'PerfLogs', 'Documents and Settings') } | ForEach-Object {
        $acl = Get-Acl -LiteralPath $_.FullName
        $modify = $false
        foreach ($ace in $acl.Access) {
            if ($ace.AccessControlType -eq 'Allow' -and $always -contains $ace.IdentityReference.Value -and
                (($ace.FileSystemRights -band $writeBits) -ne 0)) { $modify = $true }
        }
        '  {0,-40} anyone-can-modify={1}' -f $_.FullName, $modify
    }
}

''
'The root of C:\ in full, because new top-level folders inherit from it:'
(icacls C:\) | Select-Object -First 7
