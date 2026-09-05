# One scheduled run: fetch from PMD, republish the static data, push it.
#
# This is the only part of the project that has to run on a machine you control,
# because PMD refuses datacenter IPs (see brain/landmines.md #12). It needs about
# ten seconds a day. Everything else -- hosting the data, serving the site -- is
# free and handled by GitHub Pages.
#
# Install as a scheduled task (run once, from the repo root):
#
#   powershell -ExecutionPolicy Bypass -File scripts\install-task.ps1
#
# Or run it by hand any time:
#
#   powershell -ExecutionPolicy Bypass -File scripts\daily-poll.ps1

$ErrorActionPreference = "Stop"

# Resolve the repo root from this script's location, so the task works no matter
# what working directory the scheduler hands us.
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "No virtualenv at $python. Run: python -m venv .venv; .venv\Scripts\pip install -e ."
}

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Write-Output "[$stamp] starting poll"

# `hawa poll` deliberately exits 0 even when PMD is down, so a transient outage
# does not show up as a failed scheduled task every time their server hiccups.
& $python -m hawa.cli poll
$pollExit = $LASTEXITCODE

if ($pollExit -ne 0) {
    Write-Output "poll exited $pollExit; not committing"
    exit $pollExit
}

# Commit only if the published data actually changed. Unchanged files are left
# untouched by the publisher, so most runs produce nothing to commit -- that is
# the normal, quiet case, not an error.
$changes = git status --porcelain -- docs
if ([string]::IsNullOrWhiteSpace($changes)) {
    Write-Output "no data changes; nothing to publish"
    exit 0
}

$today = Get-Date -Format "yyyy-MM-dd"
git add docs
git commit -q -m "data: pollen readings as of $today"

# A failed push must not lose the commit -- it stays local and the next run
# pushes both. Common causes are no network or expired credentials.
git push -q origin main
if ($LASTEXITCODE -ne 0) {
    Write-Output "push failed; the commit is safe locally and will go out next run"
    exit 0
}

Write-Output "published and pushed"
