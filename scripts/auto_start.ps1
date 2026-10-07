# ZUZZ Auto-Start — Windows Task Scheduler
param(
    [string]$TaskName = "ZUZZ Auto Start",
    [string]$Python = "python",
    [string]$Script = (Resolve-Path "..\start.py").Path,
    [string]$WorkDir = (Resolve-Path "..\").Path
)

$action = New-ScheduledTaskAction -Execute $Python -Argument $Script -WorkingDirectory $WorkDir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RunOnlyIfNetworkAvailable:$false
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -RunLevel Highest -LogonType Interactive

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "Start ZUZZ on login" -Force | Out-Null

$logDir = Join-Path $WorkDir "storage\logs"
if (!(Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$logFile = Join-Path $logDir "startup.log"
"[$(Get-Date)] ZUZZ auto-start registered." | Out-File -FilePath $logFile -Append

Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.MessageBox]::Show("ZUZZ will start on next login.", "Auto-start configured") | Out-Null
