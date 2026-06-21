# Registers a Scheduled Task that runs the dashboard at logon and restarts it
# if it stops. Writes the task with YOUR paths (no hard-coded usernames).
#
# Run:    powershell -ExecutionPolicy Bypass -File install-task.ps1
# Remove: Unregister-ScheduledTask -TaskName "agents-dashboard" -Confirm:$false

$ErrorActionPreference = "Stop"

$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command python3 -ErrorAction SilentlyContinue).Source }
if (-not $py) { Write-Error "Python not found in PATH. Install Python 3.9+ and try again."; exit 1 }

$action  = New-ScheduledTaskAction -Execute $py -Argument "`"$dir\server.py`"" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName "agents-dashboard" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

Write-Host "Installed task 'agents-dashboard'."
Write-Host "Dashboard: http://localhost:8420"
Write-Host "Start it now without logging out:  Start-ScheduledTask -TaskName agents-dashboard"
