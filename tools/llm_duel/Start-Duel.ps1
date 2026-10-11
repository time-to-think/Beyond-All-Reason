param([switch]$Headless, [switch]$Hidden, [switch]$Mock, [switch]$PrepareOnly, [int]$TestSeconds=0,
      [string]$ConfigPath=(Join-Path $PSScriptRoot 'config.json'),
      [string]$RuntimeRoot=(Join-Path $env:LOCALAPPDATA 'BAR-LLM-Duel'))
$ErrorActionPreference='Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$data = (Resolve-Path (Join-Path $repo '../BAR-Launcher/data')).Path
$engineDir = Join-Path $data 'engine/recoil_2026.07.04'
$python = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
if (-not (Test-Path $python)) { $python=(Get-Command python -ErrorAction Stop).Source }
$ConfigPath=(Resolve-Path -LiteralPath $ConfigPath).Path
$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
if ($config.port -lt 1024 -or $config.port -gt 65535 -or $config.match_minutes -le 0) {
 throw 'Invalid port or match duration in config.'
}
$run = Join-Path $RuntimeRoot ((Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,6))
New-Item -ItemType Directory -Path $run -Force | Out-Null
# Isolated writable profile, with read access to the already installed content.
$content=Join-Path $RuntimeRoot 'content'
$cache=Join-Path $RuntimeRoot 'cache'
New-Item -ItemType Directory -Path $content,$cache,(Join-Path $content 'games') -Force | Out-Null
$links=@{maps=(Join-Path $data 'maps');base=(Join-Path $engineDir 'base');fonts=(Join-Path $engineDir 'fonts');'games/BAR.sdd'=$repo}
foreach ($name in $links.Keys) {
 $path=Join-Path $content $name
 if (Test-Path -LiteralPath $path) {
  $existing=Get-Item -LiteralPath $path
  if ($existing.LinkType -ne 'Junction' -or [string]$existing.Target -ne $links[$name]) {
   throw "Content link points elsewhere: $path. Choose another RuntimeRoot."
  }
 } else { New-Item -ItemType Junction -Path $path -Target $links[$name] | Out-Null }
}
New-Item -ItemType Junction -Path (Join-Path $run 'cache') -Target $cache | Out-Null
$token = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
@{token=$token} | ConvertTo-Json | Set-Content (Join-Path $run 'session.json') -Encoding UTF8
$frames=[int]($config.match_minutes*60*30)
$debug=''
if ($TestSeconds -gt 0) { $debug='llm_duel_test_exit=1; debugcommands=' + ($TestSeconds*30) + ':quitforce;' }
$start = @"
[GAME]
{
 MapName=Comet Catcher Remake;
 GameType=Beyond All Reason `$VERSION;
 GameStartDelay=0;
 StartPosType=0;
 RecordDemo=1;
 MyPlayerName=DuelObserver;
 IsHost=1;
 FixedRNGSeed=42;
 NumPlayers=1;
 NumUsers=1;
 HostIP=127.0.0.1;
 HostPort=0;
 NoHelperAIs=0;
 [MODOPTIONS]
 {
  llm_duel=1;
  llm_duel_token=$token;
  llm_duel_port=$($config.port);
  llm_local_model=$($config.local_model);
  llm_cloud_model=$($config.cloud_model);
  llm_duel_frames=$frames;
  deathmode=com;
  $debug
 }
 [ALLYTEAM0] { NumAllies=0; StartRectLeft=0; StartRectTop=0; StartRectRight=0.3; StartRectBottom=0.3; }
 [ALLYTEAM1] { NumAllies=0; StartRectLeft=0.7; StartRectTop=0.7; StartRectRight=1; StartRectBottom=1; }
 [TEAM0] { Side=Armada; AllyTeam=0; TeamLeader=0; StartPosX=1200; StartPosZ=1200; RGBColor=0.2 0.7 1; }
 [TEAM1] { Side=Armada; AllyTeam=1; TeamLeader=0; StartPosX=6800; StartPosZ=6800; RGBColor=1 0.3 0.2; }
 [AI0] { Host=0; Name=Local_Qwen35; ShortName=LLM Duel Local; Team=0; IsFromDemo=0; }
 [AI1] { Host=0; Name=Cloud_GPTOSS120; ShortName=LLM Duel Cloud; Team=1; IsFromDemo=0; }
 [PLAYER0] { Name=DuelObserver; Team=0; Spectator=1; IsFromDemo=0; }
}
"@
$start | Set-Content (Join-Path $run 'startscript.txt') -Encoding ASCII
@"
SpringData = $content
LogFlushLevel = 0
TCPAllowConnect = 127.0.0.1:$($config.port)
Fullscreen = 0
XResolutionWindowed = 1280
YResolutionWindowed = 720
VSync = 1
MaxParticles = 4000
ShadowMapSize = 2048
"@ | Set-Content (Join-Path $run 'springsettings.cfg') -Encoding ASCII
Write-Output "Run directory: $run"
if ($PrepareOnly) { return }
if (-not $Mock) {
 & $python (Join-Path $PSScriptRoot 'bridge.py') --config $ConfigPath --probe
 if ($LASTEXITCODE -ne 0) { throw 'Model readiness check failed. See model/error above; no match started.' }
}
$bridgeArgs=@(('"'+(Join-Path $PSScriptRoot 'bridge.py')+'"'),'--config',('"'+$ConfigPath+'"'),'--session',('"'+(Join-Path $run 'session.json')+'"'))
if ($Mock) { $bridgeArgs+='--mock' }
$bridge=Start-Process -FilePath $python -ArgumentList $bridgeArgs -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $run 'bridge.log') -RedirectStandardError (Join-Path $run 'bridge-errors.log')
$exe=if ($Headless) { 'spring-headless.exe' } else { 'spring.exe' }
try {
 $readyDeadline=(Get-Date).AddSeconds(10)
 while (-not (Test-Path (Join-Path $run 'bridge.ready')) -and -not $bridge.HasExited -and (Get-Date) -lt $readyDeadline) {
  Start-Sleep -Milliseconds 100
  $bridge.Refresh()
 }
 if ($bridge.HasExited) { throw 'Bridge could not start. Check bridge-errors.log (port may already be in use).' }
 if (-not (Test-Path (Join-Path $run 'bridge.ready'))) { throw 'Bridge readiness timed out.' }
 $engineArgs=@('--isolation',('--isolation-dir="'+$content+'"'),('--write-dir="'+$run+'"'),('--config="'+(Join-Path $run 'springsettings.cfg')+'"'),('"'+(Join-Path $run 'startscript.txt')+'"'))
 if ($Hidden) { $engineArgs+='--hidden' }
 $style=if ($Headless -or $Hidden) { 'Hidden' } else { 'Normal' }
 $game=Start-Process -FilePath (Join-Path $engineDir $exe) -ArgumentList $engineArgs -WorkingDirectory $run -WindowStyle $style -PassThru -RedirectStandardOutput (Join-Path $run 'engine-console.log') -RedirectStandardError (Join-Path $run 'engine-errors.log')
 # Windows PowerShell 5.1 needs the process handle retained to read ExitCode after exit.
 $gameHandle=$game.Handle
 if ($TestSeconds -gt 0) {
  if (-not $game.WaitForExit(($TestSeconds+180)*1000)) {
   Stop-Process -Id $game.Id
   throw 'Test exceeded its wall-clock deadline; its game process was stopped.'
  }
 } else { $game.WaitForExit() }
 Write-Output "Engine exit: $($game.ExitCode)"
 if ($game.ExitCode -ne 0) { throw "Game launch failed. Check $run/infolog.txt" }
} finally {
 if (-not $bridge.HasExited) { Stop-Process -Id $bridge.Id }
}
