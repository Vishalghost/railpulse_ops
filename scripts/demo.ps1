<#
.SYNOPSIS
  Drives the RailPulse live demo: one simulator run with a scripted timeline of faults and a
  peak-transit burst. Start it exactly 5 minutes before presenting (see DEMO.md): the default
  offsets put the crack ~0:40 and the burst ~3:00 into the talk.

  Keep this file ASCII-only: Windows PowerShell 5.1 reads BOM-less scripts as ANSI, and
  characters like an ellipsis or arrow break parsing.

.EXAMPLE
  .\scripts\demo.ps1                 # full timeline
  .\scripts\demo.ps1 -DryRun         # print the timeline and the simulator command only
#>
param(
    [int]$CrackAt = 340,      # s after start (talk ~0:40); baselines need ~100 s to warm up
    [int]$SilentAt = 60,      # s; segment must report first, then go dark
    [int]$BurstAt = 480,      # s (talk ~3:00); scaling needs ~2 min to show
    [int]$BurstFor = 300,
    [int]$Duration = 2400,    # 40 min, then stops on its own (IoT messages cost money)
    [int]$SilentAfter = 900,  # must match the deployed heartbeat (-c silent_after_s=...)
    [string]$CrackSegment = "SEG-DLI-012",
    [string]$SilentSegment = "SEG-MAS-007",
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
Set-Location (Join-Path $root "simulator")
# The simulator logs progress to stderr; under "Stop", Windows PowerShell 5.1 would turn each
# stderr line into a terminating error. Judge Python by its exit code instead.
$ErrorActionPreference = "Continue"

# Is the crack signature implemented yet (simulator/railpulse_sim/faults.py, apply_fault)?
& $py -m railpulse_sim --dry-run --duration 1 --limit 3 --divisions DLI --fault "crack@SEG-DLI-001" 2>&1 | Out-Null
$crackReady = $LASTEXITCODE -eq 0

$faults = @("--fault", "silent@$SilentSegment+$SilentAt")
if ($crackReady) {
    $faults += @("--fault", "crack@$CrackSegment+$CrackAt")
} else {
    Write-Warning "apply_fault() is not implemented yet - running without the crack. The silent-segment and burst beats still work."
}
$simArgs = @("-m", "railpulse_sim", "--interval", "5", "--duration", "$Duration",
             "--burst-at", "$BurstAt", "--burst-for", "$BurstFor") + $faults

function Cue([int]$t, [string]$what) { "{0,6}  {1}" -f ([TimeSpan]::FromSeconds($t).ToString("mm\:ss")), $what }
""
"RailPulse demo timeline (from simulator start)"
Cue 0 "Fleet starts reporting; dashboard track strip fills with green signals"
Cue $SilentAt "$SilentSegment goes dark (simulated cable cut)"
Cue ($SilentAt + $SilentAfter) "...P2 'Segment gone silent' appears at the next 5-min heartbeat after this"
if ($crackReady) {
    Cue $CrackAt "Crack starts on $CrackSegment -> P2, then P1 as it grows; gangman paged"
}
Cue $BurstAt "Peak-transit burst x5 for $BurstFor s -> queue backlog, worker scale-out"
Cue $Duration "Simulator stops"
""
"$py $($simArgs -join ' ')"
if ($DryRun) { return }
& $py @simArgs
