# Registers a Windows scheduled task that runs JobHunter daily (9:00 and 18:00) using profile.yaml.
#   powershell -ExecutionPolicy Bypass -File .\scripts\setup_schedule.ps1
# Remove:  schtasks /Delete /TN JobHunter /F
param([string]$Profile = "profile.yaml")
$dir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not (Test-Path (Join-Path $dir $Profile))) { throw "Create $Profile first (copy profile.example.yaml)." }
$py = (Get-Command python).Source
$action = New-ScheduledTaskAction -Execute $py -Argument "-m jobhunter run --profile $Profile --notify" -WorkingDirectory $dir
$triggers = @((New-ScheduledTaskTrigger -Daily -At 9:00AM), (New-ScheduledTaskTrigger -Daily -At 6:00PM))
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "JobHunter" -Action $action -Trigger $triggers -Settings $settings -Force | Out-Null
Write-Host "Scheduled 'JobHunter' daily at 9 AM and 6 PM. Report: $dir\output\report.html"
