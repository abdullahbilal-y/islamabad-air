# Register the daily poll as a Windows scheduled task.
#
# Run once, from the repo root:
#   powershell -ExecutionPolicy Bypass -File scripts\install-task.ps1
#
# Remove it later with:
#   Unregister-ScheduledTask -TaskName "Hawa pollen poll" -Confirm:$false

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$script = Join-Path $repo "scripts\daily-poll.ps1"
$taskName = "Hawa pollen poll"

if (-not (Test-Path $script)) { Write-Error "Cannot find $script" }

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`"" `
    -WorkingDirectory $repo

# PMD publishes once a day, so twice is plenty and gives a second chance if the
# machine was asleep or their server was down at the first attempt.
$triggers = @(
    New-ScheduledTaskTrigger -Daily -At 10:00
    New-ScheduledTaskTrigger -Daily -At 19:00
)

# StartWhenAvailable is the setting that makes this workable on a laptop: if the
# machine was off at 10:00, the task runs shortly after it next wakes rather
# than silently skipping the day. A missed day cannot be recovered later --
# PMD keeps no archive -- so catching up on wake genuinely matters.
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Output "replaced the existing task"
}

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $triggers `
    -Settings $settings `
    -Description "Fetches Islamabad pollen counts from PMD, republishes the open dataset, and pushes to GitHub." | Out-Null

Write-Output "Registered '$taskName' (daily at 10:00 and 19:00, catching up on wake)."
Write-Output "Test it now with:  Start-ScheduledTask -TaskName '$taskName'"
