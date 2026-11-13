<#
.SYNOPSIS
  Send one task to a worker (agy, Copilot+cloud API, or local Ollama) by role, with safe defaults.

.EXAMPLES
  powershell -NoProfile -File delegate.ps1 -Role builder -TaskFile .worker/t1.md -Include src/shop.py
  powershell -NoProfile -File delegate.ps1 -Role reviewer -TaskFile .worker/review.md
  powershell -NoProfile -File delegate.ps1 -Role builder -Provider nvidia -TaskFile .worker/t1.md   # force one worker
  powershell -NoProfile -File delegate.ps1 -Local -TaskFile .worker/t1.md                         # private, text only

  Roles: builder | builderplus | tester | reviewer | scribe (rules in roles/<role>.md).
  Each role has a worker chain; on a transient provider error (429/503/no capacity) it retries
  once, then moves to the next worker. -Model / -Provider pick one worker (no fallback chain).
  Output: a short summary (full JSON saved in .worker/last.json); -Json prints the JSON instead.
  Exit: 0 ok, 2 no task, 3 timeout (check git status: files often exist), 4 blocked, 5 empty, 6 provider error.
  Project settings (check commands, worker notes) come from PROJECT.md.
#>
param(
    [string]$Task,
    [string]$TaskFile,
    [ValidateSet('builder', 'builderplus', 'tester', 'reviewer', 'scribe')]
    [string]$Role = 'builder',
    [string[]]$Include,            # files appended to the prompt with line numbers (saves exploring calls)
    [ValidateSet('gemini-3.8-flash-low', 'gemini-3.8-flash-medium', 'gemini-3.8-flash-high', 'gpt-oss-120b-medium', 'claude-sonnet-4-6')]
    [string]$Model,
    [ValidateSet('openrouter', 'nvidia', 'gemini', 'openai')]
    [string]$Provider,
    [string]$ProviderModel,
    [string]$OpenRouter,           # shortcut: -Provider openrouter -ProviderModel <id>
    [string]$Conversation,
    [string]$Schema,
    [int]$TimeoutSec = 480,
    [switch]$NoSandbox,
    [switch]$NoAgent,              # agy: use the full default tool set instead of the role agent
    [switch]$Json,
    [switch]$Local,
    [string]$LocalModel = 'gemma4:12b'
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
New-Item -ItemType Directory -Force (Join-Path $root '.worker') | Out-Null   # git-ignored, so missing in a fresh clone

# API keys: real environment variables win; otherwise read KEY=VALUE lines from .env (process only).
$envFile = Join-Path $root '.env'
if (Test-Path $envFile) {
    foreach ($l in Get-Content -Encoding UTF8 $envFile) {
        if ($l -match '^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$' -and $Matches[2] -and -not [Environment]::GetEnvironmentVariable($Matches[1], 'Process')) {
            [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim('"'), 'Process')
        }
    }
}

# Project settings from PROJECT.md: the ```settings block (key: value) and the "## Worker notes" section.
$proj = @{ check_one = ''; check_all = ''; check_dir = '' }; $workerNotes = ''
$projFile = Join-Path $root 'PROJECT.md'
if (Test-Path $projFile) {
    $pt = Get-Content -Raw -Encoding UTF8 $projFile
    if ($pt -match '(?s)```settings\s*\r?\n(.*?)```') {
        foreach ($l in $Matches[1] -split '\r?\n') { if ($l -match '^\s*(\w+)\s*:\s*(.*?)\s*$') { $proj[$Matches[1]] = $Matches[2].Trim('`') } }
    }
    if ($pt -match '(?s)## Worker notes[^\n]*\n(.*?)(?=\n## |\z)') { $workerNotes = $Matches[1].Trim() }
    if ($workerNotes -match '^- <') { $workerNotes = '' }   # template placeholder, not filled in
}
$checkStem = if ($proj.check_one) { ($proj.check_one -split '\s+')[0] } else { '' }   # Copilot matches the command's first word only

# --- Workers per role (first = main, rest = fallbacks) --------------------------------
# Order = MODELS.md pick order, minus models that failed for that job (OpenAI writes files via the
# shell, so it is only used for the read-only reviewer). Last resort: OpenRouter free (1000 req/day).
$orFree = @{ h = 'cp'; p = 'openrouter'; pm = 'nvidia/nemotron-3-ultra-550b-a55b:free' }
$chains = @{
    builder     = @(@{ h = 'agy'; m = 'gemini-3.8-flash-low' },    @{ h = 'cp'; p = 'nvidia' }, $orFree)
    builderplus = @(@{ h = 'agy'; m = 'gemini-3.8-flash-medium' }, @{ h = 'cp'; p = 'nvidia' }, $orFree)
    tester      = @(@{ h = 'cp'; p = 'gemini' },                    @{ h = 'cp'; p = 'nvidia' }, $orFree)
    reviewer    = @(@{ h = 'agy'; m = 'claude-sonnet-4-6' },       @{ h = 'cp'; p = 'openai'; pm = 'o3' }, @{ h = 'agy'; m = 'gpt-oss-120b-medium' })
    scribe      = @(@{ h = 'cp'; p = 'nvidia'; pm = 'nvidia/nemotron-3.5-lightning-30b-a3b' }, @{ h = 'agy'; m = 'gemini-3.8-flash-low' }, $orFree)
}
$roleFile = @{ builder = 'builder'; builderplus = 'builder'; tester = 'tester'; reviewer = 'reviewer'; scribe = 'scribe' }[$Role]
$providers = @{
    openrouter = @{ url = 'https://openrouter.ai/api/v1'; key = 'OPENROUTER_API_KEY'; model = 'openrouter/free' }
    nvidia     = @{ url = 'https://integrate.api.nvidia.com/v1'; key = 'NVIDIA_API_KEY'; model = 'mistralai/mistral-large-4-1t-a52b' }
    gemini     = @{ url = 'https://generativelanguage.googleapis.com/v1beta/openai/'; key = 'GEMINI_API_KEY'; model = 'gemini-flash-lite-latest' }
    # OpenAI: free daily tokens only for listed models with data sharing on (see MODELS.md). GPT-5 uses the responses API.
    openai     = @{ url = 'https://api.openai.com/v1'; key = 'OPENAI_API_KEY'; model = 'gpt-5.4-mini' }
}
# Copilot tools per role: only what the role needs (each tool definition is resent on every call).
# No check command in PROJECT.md = no shell tool at all.
$sh = if ($checkStem) { @("shell(${checkStem}:*)") } else { @() }
$shTools = if ($checkStem) { ',powershell,read_powershell' } else { '' }
$testerWrite = if ($proj.check_dir) { "write($($proj.check_dir)/**)" } else { 'write' }
$cpTools = @{
    builder  = @{ tools = "view,create,edit,grep,glob$shTools"; allow = @('write') + $sh }
    tester   = @{ tools = "view,create,edit,grep,glob$shTools"; allow = @($testerWrite) + $sh }
    scribe   = @{ tools = 'view,create,edit,grep,glob'; allow = @('write(**/*.md)') }
    reviewer = @{ tools = "view,grep,glob$shTools"; allow = $sh }
}
$cpRole = @{ builder = 'builder'; builderplus = 'builder'; tester = 'tester'; reviewer = 'reviewer'; scribe = 'scribe' }[$Role]

if ($OpenRouter) { $Provider = 'openrouter'; if (-not $ProviderModel) { $ProviderModel = $OpenRouter } }
if ($Model) { $chain = @(@{ h = 'agy'; m = $Model }) }
elseif ($Provider) { $chain = @(@{ h = 'cp'; p = $Provider; pm = $ProviderModel }) }
else { $chain = $chains[$Role] }

# --- Prompt: role rules + task + included files ---------------------------------------
if ($TaskFile) { $Task = Get-Content -Raw -Encoding UTF8 $TaskFile }
if (-not $Task) { Write-Error 'Give -Task "..." or -TaskFile <path>.'; exit 2 }
if (-not $Conversation) {
    $rules = Get-Content -Raw -Encoding UTF8 (Join-Path $root "roles\$roleFile.md")
    $check = if ($Role -eq 'scribe') { 'Run no commands.' }
             elseif ($proj.check_one) { "Check command (the ONLY command you may run, for your own part; {name} = your part): ``$($proj.check_one)``" }
             else { 'This project has no check command: run no commands. Re-read your files against the spec instead.' }
    $notes = if ($workerNotes) { "`n$workerNotes" } else { '' }
    $Task = $rules + "`n---`n# PROJECT`n$check$notes`n`n---`n# YOUR TASK`n`n" + $Task
}
foreach ($f in $Include) {
    $n = 0; $body = (Get-Content -Encoding UTF8 $f | ForEach-Object { $n++; "{0,4}  {1}" -f $n, $_ }) -join "`n"
    $Task += "`n`n## File: $f (line numbers added; do not copy them)`n``````n$body`n``````"
}
if ($Include) { $Task += "`n`nThe files above are current. Don't re-read them; read others only if the task needs them." }

# --- Local Ollama (text only) -----------------------------------------------------------
if ($Local) {
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) { Write-Error 'ollama not found on PATH.'; exit 3 }
    $Task += "`n`nNOTE: you cannot edit files or run commands. Reply with the COMPLETE contents of every file you change, each in a fenced block whose first line is a comment with its path. Then the report."
    $Task | ollama run $LocalModel
    exit $LASTEXITCODE
}

function Get-Changes {
    @(git -C $root status --porcelain -uall 2>$null | Where-Object { $_ -notmatch '__pycache__|\.worker/' })
}

# --- One attempt on agy ------------------------------------------------------------------
function Invoke-Agy($w) {
    $prompt = $Task
    if ($Role -in 'builder', 'builderplus', 'tester') {
        $prompt += if ($proj.check_one) { "`n`nREMINDER: only command allowed: '$($proj.check_one)' for your own part. Run it max 2x. End with the report." }
                   else { "`n`nREMINDER: run no commands. End with the report." }
    }
    $a = @('--input-format', 'stream-json', '--output-format', 'stream-json', '--model', $w.m, '--print-timeout', "${TimeoutSec}s", '--mode', 'accept-edits')
    if (-not $NoAgent) { $a += @('--agent', "w-$cpRole") }
    if (-not $NoSandbox) { $a += '--sandbox' }
    if ($Conversation) { $a += @('--conversation', $Conversation) }
    if ($Schema) { $a += @('--json-schema', "`"$((Resolve-Path $Schema).Path)`"") }
    # Prompt via stdin (no 32 KB argv limit, no quote escaping); cmd redirect avoids PowerShell's BOM.
    $pf = Join-Path ([IO.Path]::GetTempPath()) ("agy-prompt-" + [guid]::NewGuid() + ".ndjson")
    $line = @{ event = 'user'; message = @{ content = $prompt } } | ConvertTo-Json -Compress -Depth 5
    [IO.File]::WriteAllText($pf, $line + "`n", (New-Object Text.UTF8Encoding $false))
    try { $stream = cmd /c "agy $($a -join ' ') < `"$pf`" 2>&1" } finally { Remove-Item $pf -ErrorAction SilentlyContinue }
    $resLine = $stream | Where-Object { "$_" -match '^\{"event":"result"' } | Select-Object -Last 1
    $r = if ($resLine) { ($resLine | ConvertFrom-Json).result } else { $null }
    $err = if ($r) { "$($r.error)" } else { (($stream | Select-Object -Last 3) -join ' ') }
    $u = $r.usage
    $o = [ordered]@{
        worker = "agy $($w.m)"; conversation_id = $r.conversation_id; response = $r.response; error = $err
        # agy doesn't report model calls (num_turns counts user turns), so calls = '?'
        calls = '?'; input_tokens = $u.input_tokens; cache_read_tokens = $u.cache_read_tokens
        output_tokens = $u.output_tokens; thinking_tokens = $u.thinking_tokens
        denied = @($r.denied_actions | Where-Object { $_ } | ForEach-Object { $_.display_name })
    }
    $o.code = if ("$($r.finished_reason)" -eq 'print_timeout') { 3 }
              elseif (-not $r -or ("$($r.status)" -eq 'ERROR' -and -not $r.response)) { 6 }
              elseif ($o.denied.Count) { 4 } elseif (-not $r.response) { 5 } else { 0 }
    $o
}

# --- One attempt on Copilot + cloud API ----------------------------------------------------
# OpenAI complimentary daily tokens (data sharing on): only these models, only up to these limits.
# Anything else is billed from the prepaid credit, so it is refused here. Reset: 00:00 UTC.
$openaiFree = @{
    big  = @{ limit = 250000;  models = @('gpt-5.4', 'gpt-5.2', 'gpt-5.1', 'gpt-5', 'gpt-4.1', 'gpt-4o', 'o1', 'o3') }
    mini = @{ limit = 2500000; models = @('gpt-5.4-mini', 'gpt-5.4-nano', 'gpt-5-mini', 'gpt-5-nano', 'gpt-4.1-mini', 'gpt-4.1-nano', 'gpt-4o-mini', 'o3-mini', 'o4-mini') }
}
$openaiRunCap = @{ big = 60000; mini = 400000 }   # max tokens per single run (normal task: 15-75k)
$openaiLog = Join-Path $root '.worker\openai-usage.jsonl'
function Get-LogTokens($dir) {   # Copilot writes token counts only to its debug log (also while running)
    $r = @{ in = 0; out = 0; total = 0; last = 0 }   # last = size of the latest call (in + out)
    if (Test-Path $dir) {
        $lastIn = 0; $lastOut = 0
        Select-String -Path "$dir\*.log" -Pattern '"prompt_tokens": (\d+)' -ErrorAction SilentlyContinue | ForEach-Object { $lastIn = [int]$_.Matches[0].Groups[1].Value; $r.in += $lastIn }
        Select-String -Path "$dir\*.log" -Pattern '"completion_tokens": (\d+)' -ErrorAction SilentlyContinue | ForEach-Object { $lastOut = [int]$_.Matches[0].Groups[1].Value; $r.out += $lastOut }
        $r.last = $lastIn + $lastOut
        $r.cached = 0; Select-String -Path "$dir\*.log" -Pattern '"cached_tokens": (\d+)' -ErrorAction SilentlyContinue | ForEach-Object { $r.cached += [int]$_.Matches[0].Groups[1].Value }
    }
    $r.total = $r.in + $r.out; $r
}
function Get-OpenAITier($m) { foreach ($t in 'big', 'mini') { if ($openaiFree[$t].models -contains $m) { return $t } }; $null }
function Get-OpenAIUsedToday($tier) {
    $day = [DateTime]::UtcNow.ToString('yyyy-MM-dd'); $sum = 0
    if (Test-Path $openaiLog) {
        foreach ($l in Get-Content $openaiLog) { $e = $l | ConvertFrom-Json; if ($e.day -eq $day -and $e.tier -eq $tier) { $sum += [int]$e.tokens } }
    }
    $sum
}

function Invoke-Copilot($w) {
    $pv = $providers[$w.p]
    if ($w.p -eq 'openai') {
        $m = if ($w.pm) { $w.pm } else { $pv.model }
        $tier = Get-OpenAITier $m
        if (-not $tier) {
            return [ordered]@{ worker = "openai $m"; code = 6; calls = 0; denied = @()
                error = "REFUSED: $m is not on the free list (would bill the prepaid credit). See MODELS.md." }
        }
        $used = Get-OpenAIUsedToday $tier; $limit = $openaiFree[$tier].limit
        if ($used -ge 0.9 * $limit) {
            return [ordered]@{ worker = "openai $m"; code = 6; calls = 0; denied = @()
                error = "REFUSED: OpenAI $tier free tier at $used of $limit tokens today (stop at 90%; capacity)." }
        }
    }
    if (-not (Get-Command copilot -ErrorAction SilentlyContinue)) { Write-Error 'copilot not found on PATH.'; exit 3 }
    $key = [Environment]::GetEnvironmentVariable($pv.key, 'Process')
    if (-not $key) { $key = [Environment]::GetEnvironmentVariable($pv.key, 'User') }
    if (-not $key) { Write-Error "Put $($pv.key)=... in .env (see .env.example)."; exit 3 }
    # Process-scoped: the user's own copilot/Ollama setup is untouched.
    $env:COPILOT_PROVIDER_BASE_URL = $pv.url; $env:COPILOT_PROVIDER_TYPE = 'openai'; $env:COPILOT_PROVIDER_API_KEY = $key
    $env:COPILOT_MODEL = if (-not $w.pm -or $w.pm -eq 'free') { $pv.model } else { $w.pm }
    $env:COPILOT_PROVIDER_MAX_PROMPT_TOKENS = '128000'
    Remove-Item Env:COPILOT_PROVIDER_BEARER_TOKEN, Env:COPILOT_PROVIDER_WIRE_MODEL, Env:COPILOT_PROVIDER_MODEL_ID, Env:COPILOT_PROVIDER_WIRE_API -ErrorAction SilentlyContinue
    if ($w.p -eq 'openai') { $env:COPILOT_PROVIDER_WIRE_API = if ($env:COPILOT_MODEL -match '^(gpt-5|o\d)') { 'responses' } else { 'completions' } }

    $prompt = $Task
    if ($PSVersionTable.PSVersion -lt [version]'7.3') { $prompt = ($prompt -replace '(\\*)"', '$1$1\"') -replace '(\\+)$', '$1$1' }
    $t = $cpTools[$cpRole]
    $logDir = Join-Path ([IO.Path]::GetTempPath()) ("cp-log-" + [guid]::NewGuid())
    $a = @('-p', $prompt, '--output-format', 'json', '--no-color', '--no-ask-user', '--no-custom-instructions',
           '--disable-builtin-mcps', '--disable-mcp-server', 'ddg-search',
           '--secret-env-vars', 'COPILOT_PROVIDER_API_KEY,OPENROUTER_API_KEY,NVIDIA_API_KEY,GEMINI_API_KEY,OPENAI_API_KEY',
           "--available-tools=$($t.tools)", '--allow-tool', 'read', '--deny-tool', 'shell(git:*)', '--deny-tool', 'url',
           '--log-level', 'debug', '--log-dir', $logDir)
    foreach ($x in $t.allow) { $a += @('--allow-tool', $x) }
    if (-not $NoSandbox) { $a += '--sandbox' }
    if ($Conversation) { $a += @('--resume', $Conversation) }

    # OpenAI: cap tokens DURING the run (a looping worker once used 1.4M in one run).
    # Cap = what is left below 90% of today's free tier, and at most $openaiRunCap per run.
    $cap = 0
    if ($w.p -eq 'openai') {
        $runCap = if ($env:DELEGATE_OPENAI_RUN_CAP) { [int]$env:DELEGATE_OPENAI_RUN_CAP } else { $openaiRunCap[$tier] }   # env: for testing
        $cap = [Math]::Min([int](0.9 * $limit) - $used, $runCap)
    }
    $pidFile = Join-Path ([IO.Path]::GetTempPath()) ("cp-pid-" + [guid]::NewGuid())
    $job = Start-Job -ScriptBlock { param($a, $d, $pf) $PID | Set-Content $pf; Set-Location $d; & copilot @a 2>&1 } -ArgumentList (, $a), (Get-Location).Path, $pidFile
    $sw2 = [Diagnostics.Stopwatch]::StartNew(); $timedOut = $false; $capped = $false
    while (-not (Wait-Job $job -Timeout 3)) {
        if ($sw2.Elapsed.TotalSeconds -ge $TimeoutSec) { $timedOut = $true; break }
        # Look one call ahead: calls only grow (history is resent), so stop if the next one could cross the cap.
        if ($cap -gt 0) { $lt = Get-LogTokens $logDir; if ($lt.total + $lt.last -ge $cap) { $capped = $true; break } }
    }
    if ($timedOut -or $capped) {   # kill the whole tree: Stop-Job alone leaves copilot.exe running
        $jp = Get-Content $pidFile -ErrorAction SilentlyContinue
        # Kill the job host's children (copilot + its tree), not the host: Receive-Job still needs it.
        if ($jp) { Get-CimInstance Win32_Process -Filter "ParentProcessId=$jp" | ForEach-Object { cmd /c "taskkill /PID $($_.ProcessId) /T /F >nul 2>&1" } }
        Stop-Job $job
    }
    Remove-Item $pidFile -ErrorAction SilentlyContinue
    $lines = Receive-Job $job -ErrorAction SilentlyContinue; Remove-Job $job -Force

    $ev = foreach ($l in $lines) { try { "$l" | ConvertFrom-Json } catch {} }
    $final = $ev | Where-Object { $_.type -eq 'result' } | Select-Object -Last 1
    $err = $ev | Where-Object { $_.type -eq 'session.error' } | Select-Object -Last 1
    $starts = @{}; $ev | Where-Object { $_.type -eq 'tool.execution_start' } | ForEach-Object { $starts[$_.data.toolCallId] = $_.data }
    $denied = @($ev | Where-Object { $_.type -eq 'tool.execution_complete' -and $_.data.error.code -eq 'denied' } |
        ForEach-Object { $s = $starts[$_.data.toolCallId]; "$($s.toolName): $($s.arguments.command)$($s.arguments.path)" })
    $text = ($ev | Where-Object { $_.type -eq 'assistant.message' -and $_.data.content } | Select-Object -Last 1).data.content
    $tok = Get-LogTokens $logDir; $inTok = $tok.in; $outTok = $tok.out
    Remove-Item $logDir -Recurse -Force -ErrorAction SilentlyContinue
    $o = [ordered]@{
        worker = "$($w.p) $env:COPILOT_MODEL"; conversation_id = $final.sessionId; response = $text; error = "$($err.data.message)"
        calls = @($ev | Where-Object { $_.type -eq 'model.call_start' }).Count
        input_tokens = $inTok; cache_read_tokens = $tok.cached; output_tokens = $outTok; thinking_tokens = 0; denied = $denied
    }
    if ($capped) { $o.error = "STOPPED: OpenAI token cap $cap reached during the run (free-tier guard)." }
    $o.code = if ($timedOut) { 3 } elseif ($capped -or $err) { 6 } elseif ($denied.Count) { 4 } elseif (-not $text) { 5 } else { 0 }
    if ($w.p -eq 'openai') {   # count free-tier usage per UTC day
        $rec = @{ day = [DateTime]::UtcNow.ToString('yyyy-MM-dd'); tier = (Get-OpenAITier $env:COPILOT_MODEL); model = $env:COPILOT_MODEL; tokens = $inTok + $outTok } | ConvertTo-Json -Compress
        Add-Content -Encoding utf8 $openaiLog $rec
    }
    $o
}

# --- Run the chain: transient provider error -> retry once -> next worker --------------------
$before = Get-Changes
$sw = [Diagnostics.Stopwatch]::StartNew()
$tried = @()
foreach ($w in $chain) {
    foreach ($try in 1, 2) {
        $res = if ($w.h -eq 'agy') { Invoke-Agy $w } else { Invoke-Copilot $w }
        $tried += "$($res.worker)=$($res.code)"
        $changed = @(Get-Changes | Where-Object { $before -notcontains $_ }).Count -gt 0
        $transient = $res.code -eq 6 -and -not $changed -and "$($res.error)" -match '429|503|UNAVAILABLE|RESOURCE_EXHAUSTED|capacity|rate limit|overloaded|timed out'
        if (-not $transient -or $try -eq 2) { break }
        Start-Sleep -Seconds 20
    }
    if ($res.code -ne 6 -or $changed) { break }
}
$after = Get-Changes
$res.role = $Role
$res.seconds = [int]$sw.Elapsed.TotalSeconds
$res.tried = $tried
$res.files_changed = @($after | Where-Object { $before -notcontains $_ } | ForEach-Object { $_.Trim() })

$jsonOut = $res | ConvertTo-Json -Depth 6
[IO.File]::WriteAllText((Join-Path $root '.worker\last.json'), $jsonOut, (New-Object Text.UTF8Encoding $false))
if ($Json) { $jsonOut }
else {
    $label = @{ 0 = 'OK'; 3 = 'TIMEOUT (check git status)'; 4 = 'BLOCKED'; 5 = 'EMPTY'; 6 = 'PROVIDER ERROR' }[[int]$res.code]
    "[$label] $Role via $($res.worker) | $($res.seconds)s | $($res.calls) calls | in $($res.input_tokens) (+cache $($res.cache_read_tokens)) out $($res.output_tokens)"
    if ($tried.Count -gt 1) { "tried: $($tried -join ', ')" }
    "files: " + $(if ($res.files_changed.Count) { $res.files_changed -join '; ' } else { 'none' })
    if ($res.denied.Count) { "denied: $($res.denied -join '; ')" }
    if ($res.error) { "error: $($res.error)" }
    "conversation: $($res.conversation_id)"
    "--- report ---"; "$($res.response)".Trim()
}
exit [int]$res.code
