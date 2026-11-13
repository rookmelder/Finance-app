<#
.SYNOPSIS
  Finds which worker tools and API keys this machine has, tests each key with a free call,
  and writes SETUP.md (git-ignored). delegate.ps1 skips workers marked "no" there.
  Run it at the start of a project and whenever you add a tool or key:
    powershell -NoProfile -File agent/check-setup.ps1
  Never prints key values.
#>
param([switch]$Offline)   # skip the key tests (only checks that keys exist)
$ErrorActionPreference = 'Continue'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
# Fresh PATH (tools installed after this shell started are found too).
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + $env:Path

# Keys: environment first, then .env (same rule as delegate.ps1).
$keys = @{}
if (Test-Path .env) {
    foreach ($l in Get-Content -Encoding UTF8 .env) { if ($l -match '^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$' -and $Matches[2]) { $keys[$Matches[1]] = $Matches[2].Trim('"') } }
}
function Get-Key($n) { $v = [Environment]::GetEnvironmentVariable($n); if ($v) { $v } else { $keys[$n] } }
function Has($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

$rows = @(); $avail = [ordered]@{}

# --- Programs ----------------------------------------------------------------------------
$tools = [ordered]@{
    git     = 'Required: tracks what workers change.'
    agy     = 'Google Antigravity CLI: runs Gemini and agy Claude/gpt-oss workers (needs a Google AI Pro plan for real quota).'
    copilot = 'GitHub Copilot CLI: runs every API worker (NVIDIA, Gemini API, OpenRouter, OpenAI).'
    ollama  = 'Optional: private local model.'
    python  = 'Only if the project uses Python.'
    node    = 'Only if the project uses JavaScript/TypeScript.'
}
foreach ($t in $tools.Keys) { $rows += [pscustomobject]@{ Item = $t; Status = $(if (Has $t) { 'found' } else { 'missing' }); Note = $tools[$t] } }
$avail['agy'] = Has 'agy'

$ollamaModels = ''
# Read the model folder instead of `ollama list`: that can start the Ollama server, which keeps our pipe open (hang).
$man = if ($env:OLLAMA_MODELS) { Join-Path $env:OLLAMA_MODELS 'manifests' } else { Join-Path $env:USERPROFILE '.ollama\models\manifests' }
if ((Has 'ollama') -and (Test-Path $man)) {
    $ollamaModels = (Get-ChildItem -Recurse -File $man | ForEach-Object { "$($_.Directory.Name):$($_.Name)" }) -join ', '
}
$avail['ollama'] = [bool]$ollamaModels

# --- API keys (each test is free: a model list or a 1-token call) ----------------------
function Test-Provider($name, $keyName, [scriptblock]$probe) {
    $k = Get-Key $keyName
    if (-not $k) { return @('no key', $false) }
    if ($Offline) { return @('key present (not tested)', $true) }
    try { & $probe $k | Out-Null; @('key works', $true) }
    catch { $code = $_.Exception.Response.StatusCode.value__; @("key FAILED ($(if ($code) { "HTTP $code" } else { 'no answer' }))", $false) }
}
$probes = [ordered]@{
    nvidia     = @('NVIDIA_API_KEY', { param($k) Invoke-RestMethod -TimeoutSec 30 -Method Post -Uri 'https://integrate.api.nvidia.com/v1/chat/completions' -Headers @{ Authorization = "Bearer $k" } -ContentType 'application/json' -Body '{"model":"mistralai/mistral-large-4-1t-a52b","messages":[{"role":"user","content":"hi"}],"max_tokens":1}' })
    gemini     = @('GEMINI_API_KEY', { param($k) Invoke-RestMethod -TimeoutSec 20 -Uri 'https://generativelanguage.googleapis.com/v1beta/models?pageSize=1' -Headers @{ 'x-goog-api-key' = $k } })
    openrouter = @('OPENROUTER_API_KEY', { param($k) Invoke-RestMethod -TimeoutSec 20 -Uri 'https://openrouter.ai/api/v1/key' -Headers @{ Authorization = "Bearer $k" } })
    openai     = @('OPENAI_API_KEY', { param($k) Invoke-RestMethod -TimeoutSec 20 -Uri 'https://api.openai.com/v1/models' -Headers @{ Authorization = "Bearer $k" } })
}
foreach ($p in $probes.Keys) {
    $r = Test-Provider $p $probes[$p][0] $probes[$p][1]
    $ok = $r[1] -and (Has 'copilot')
    $note = if ($r[1] -and -not (Has 'copilot')) { 'Copilot CLI missing: API workers need it.' } else { "Key name in .env: $($probes[$p][0])" }
    $rows += [pscustomobject]@{ Item = "$p API"; Status = $r[0]; Note = $note }
    $avail[$p] = $ok
}

# --- Write SETUP.md ---------------------------------------------------------------------
$table = ($rows | ForEach-Object { "| $($_.Item) | $($_.Status) | $($_.Note) |" }) -join "`n"
$availBlock = ($avail.Keys | ForEach-Object { "${_}: $(if ($avail[$_]) { 'yes' } else { 'no' })" }) -join "`n"
$md = @"
# Setup on this machine

Made by ``agent/check-setup.ps1`` on $(Get-Date -Format 'yyyy-MM-dd HH:mm'). Not committed (each machine has its own).
Run it again after installing a tool or adding a key. How to set each one up: ``agent/setup/``.

| Item | Status | Note |
|---|---|---|
$table

Ollama models: $(if ($ollamaModels) { $ollamaModels } else { 'none' })

## Available workers (read by delegate.ps1; "no" = skipped)
``````available
$availBlock
``````

## Confirmed by the user (the Lead asks once and fills this in)
- Claude plan: <which plan>
- Google AI Pro (agy quota): <yes/no>
- OpenAI data sharing ON (needed for the free daily tokens): <yes/no/no key>
- OpenRouter credits ever bought >= `$10 (1000 instead of 50 free requests/day): <yes/no/no key>
- Private project allowed on free cloud tiers: see PROJECT.md
"@
$setupPath = Join-Path $root 'SETUP.md'
# Keep the user's answers from a previous run (only the detected part is refreshed).
if ((Test-Path $setupPath) -and ((Get-Content -Raw -Encoding UTF8 $setupPath) -match '(?s)(## Confirmed by the user.*)$')) {
    $md = ($md -replace '(?s)## Confirmed by the user.*$', '') + $Matches[1].TrimEnd() + "`n"
}
[IO.File]::WriteAllText($setupPath, $md.Replace("`r`n", "`n"), (New-Object Text.UTF8Encoding $false))
$rows | Format-Table -AutoSize | Out-String -Width 200
"Available: " + (($avail.Keys | Where-Object { $avail[$_] }) -join ', ')
"Written: SETUP.md"
