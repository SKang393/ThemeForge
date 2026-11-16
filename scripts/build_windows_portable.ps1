param(
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,
    [string]$Python = "python",
    [string]$Version = "",
    [string]$ExpectedPyInstallerVersion = "6.21.0",
    [string]$RuntimeDirectory = ""
)

$ErrorActionPreference = "Stop"
function Test-PortableRuntimeSource {
    param([string]$Directory, [string]$ManifestPath, [string]$ManifestSHA256, [string]$SupplementalLicense = "")
    function Assert-LocalRuntimeEntry {
        param($Entry)
        $Attributes = [uint32]$Entry.Attributes
        if ($Attributes -band (0x1000 -bor 0x40000 -bor 0x400000)) { throw "Runtime input must be available locally, not an offline/recall placeholder." }
        if ($Attributes -band [uint32][IO.FileAttributes]::ReparsePoint) {
            if ($Entry.LinkType -or $Entry.Target) { throw "Runtime links and junctions are not permitted." }
            # Fully available OneDrive files may keep a CLOUD[_1..F] reparse
            # tag. It is not a pathname redirect. Unknown tags still fail.
            # Microsoft MS-FSCC Reparse Tags, verified 2026-11-15.
            $Metadata = (& (Join-Path $env:SystemRoot 'System32\fsutil.exe') reparsepoint query $Entry.FullName 2>&1 | Out-String)
            $TagMatch = [regex]::Match($Metadata, '0x[0-9a-fA-F]{8}')
            if ($LASTEXITCODE -ne 0 -or -not $TagMatch.Success) { throw "Cannot inspect runtime reparse tag." }
            $Tag = [Convert]::ToUInt32($TagMatch.Value.Substring(2), 16)
            if (($Tag -band [uint32]4294905855) -ne [uint32]2415919130) { throw "Unsupported runtime reparse tag." }
        }
    }
    if (-not [IO.Path]::IsPathRooted($Directory) -or $Directory.StartsWith('\\') -or $Directory.StartsWith('//')) {
        throw "Runtime directory must be an explicit absolute local directory."
    }
    if (-not (Test-Path -LiteralPath $Directory -PathType Container)) { throw "Runtime directory is missing." }
    Assert-LocalRuntimeEntry (Get-Item -LiteralPath $Directory)
    if ($ManifestSHA256 -notmatch '^[0-9a-f]{64}$' -or -not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { throw "Trusted runtime manifest/pin missing." }
    if ((Get-FileHash -LiteralPath $ManifestPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ManifestSHA256) { throw "Runtime manifest SHA256 mismatch." }
    $RuntimeManifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    if ($RuntimeManifest.schema_version -ne 1 -or $RuntimeManifest.executable -cne 'llama-server.exe' -or @($RuntimeManifest.files).Count -lt 1 -or @($RuntimeManifest.files).Count -gt 256) { throw "Unsupported runtime manifest." }
    $Seen = @{}
    $Verified = @()
    foreach ($Record in $RuntimeManifest.files) {
        $Name = [string]$Record.path
        if (-not $Name -or $Name -in @('.', '..') -or $Name -match '[\\/:]' -or $Seen.ContainsKey($Name)) { throw "Unsafe or duplicate runtime manifest path." }
        $Seen[$Name] = $true
        $SourcePath = Join-Path $Directory $Name
        if ($Name -ceq 'LICENSE-llama.cpp.txt' -and $SupplementalLicense) { $SourcePath = $SupplementalLicense }
        if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) { throw "Required runtime file missing: $Name" }
        $Item = Get-Item -LiteralPath $SourcePath
        Assert-LocalRuntimeEntry $Item
        if ($Record.sha256 -cnotmatch '^[0-9a-f]{64}$' -or $Item.Length -ne $Record.bytes -or (Get-FileHash -LiteralPath $SourcePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Record.sha256) { throw "Runtime file size/SHA256 mismatch: $Name" }
        $Verified += [pscustomobject]@{ path = $Name; source = $Item.FullName; bytes = $Item.Length; sha256 = $Record.sha256 }
    }
    if (-not $Seen.ContainsKey('llama-server.exe') -or -not $Seen.ContainsKey('LICENSE-llama.cpp.txt')) { throw "Runtime executable or llama.cpp license absent from manifest." }
    return $Verified
}

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Output = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $Output) {
    throw "Choose a fresh output directory. Existing files are never removed: $Output"
}
if (-not $Version) { $Version = (Get-Content -LiteralPath (Join-Path $Root "VERSION") -Raw).Trim() }
if ($Version -notmatch '^[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}$') { throw "Version must be a short filename-safe version string." }
$PyInstallerVersion = & $Python -c 'import importlib.metadata; print(importlib.metadata.version("pyinstaller"))'
if ($LASTEXITCODE -ne 0) { throw "BLOCKED: PyInstaller is unavailable in the selected Python. Install it explicitly before packaging." }
if ("$PyInstallerVersion".Trim() -ne $ExpectedPyInstallerVersion) {
    throw "Installed PyInstaller $PyInstallerVersion does not match expected $ExpectedPyInstallerVersion. No installation was attempted."
}
$PortableName = "ThemeForge-$Version-windows-portable"
$Build = Join-Path $Output "work"
$Dist = Join-Path $Output "dist"
$Release = Join-Path $Output "release"
$PortableDir = Join-Path $Release $PortableName
$ZipPath = Join-Path $Release "$PortableName.zip"
$Spec = Join-Path $Root "ThemeForge.spec"
$Entry = Join-Path $Root "scripts\themeforge_portable_entry.py"
$RuntimeFiles = @()
$RuntimePin = $null
$RuntimeManifestPath = Join-Path $Root "docs\third_party\llama-cpp-b12359\TRUSTED_FILES.json"
$RuntimeLicensePath = Join-Path $Root "docs\third_party\llama-cpp-b12359\LICENSE-llama.cpp.txt"
if ($RuntimeDirectory) {
    $RuntimePin = & $Python -c 'import runpy,sys; print(runpy.run_path(sys.argv[1])["RUNTIME_MANIFEST_SHA256"])' (Join-Path $Root "themeforge\bundled_runtime.py")
    if ($LASTEXITCODE -ne 0) { throw "Cannot read the bundled runtime identity constant." }
    $RuntimePin = "$RuntimePin".Trim()
    $RuntimeFiles = @(Test-PortableRuntimeSource -Directory $RuntimeDirectory -ManifestPath $RuntimeManifestPath -ManifestSHA256 $RuntimePin -SupplementalLicense $RuntimeLicensePath)
    if ($RuntimeFiles.Count -ne 53) { throw "Expected the pinned 53-file b12359 runtime, including llama.cpp license." }
}
foreach ($Required in @($Spec, $Entry, (Join-Path $Root "README.md"), (Join-Path $Root "LICENSE"))) {
    if (-not (Test-Path -LiteralPath $Required -PathType Leaf)) { throw "Required packaging input is missing: $Required" }
}
$SourceFiles = @((Get-ChildItem -LiteralPath (Join-Path $Root "themeforge") -Filter "*.py" -File).FullName) + @($Spec, $Entry, $MyInvocation.MyCommand.Path)
if ($RuntimeDirectory) { $SourceFiles += @($RuntimeManifestPath, $RuntimeLicensePath) + @($RuntimeFiles.source) }
$SourceHashes = [ordered]@{}
foreach ($Source in ($SourceFiles | Sort-Object)) { $SourceHashes[$Source] = (Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash.ToLowerInvariant() }

New-Item -ItemType Directory -Path $Output -ErrorAction Stop | Out-Null
$PreviousPyInstallerConfig = $env:PYINSTALLER_CONFIG_DIR
$env:PYINSTALLER_CONFIG_DIR = Join-Path $Output "cache"
Push-Location $Root
try {
    # Installed tool only. No downloads, old-tree cleanup, or spec regeneration.
    & $Python -m PyInstaller --distpath $Dist --workpath $Build $Spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE; partial output is preserved at $Output" }
    $Built = Join-Path $Dist "ThemeForge"
    if (-not (Test-Path -LiteralPath (Join-Path $Built "ThemeForge.exe") -PathType Leaf)) { throw "PyInstaller did not produce ThemeForge.exe" }
    foreach ($Source in $SourceHashes.Keys) {
        if ((Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash.ToLowerInvariant() -ne $SourceHashes[$Source]) {
            throw "Source changed during packaging; preserve output for review and use a fresh build: $Source"
        }
    }
    New-Item -ItemType Directory -Path $PortableDir -Force | Out-Null
    Get-ChildItem -LiteralPath $Built -Force | Copy-Item -Destination $PortableDir -Recurse
    Copy-Item -LiteralPath (Join-Path $Root "README.md"), (Join-Path $Root "LICENSE") -Destination $PortableDir
    $ReleaseNotes = Join-Path $Root "release_notes\v$Version.md"
    if (Test-Path -LiteralPath $ReleaseNotes -PathType Leaf) { Copy-Item -LiteralPath $ReleaseNotes -Destination $PortableDir }
    $ReviewedCategoriesGuide = Join-Path $Root "docs\reviewed_categories.md"
    if (Test-Path -LiteralPath $ReviewedCategoriesGuide -PathType Leaf) {
        Copy-Item -LiteralPath $ReviewedCategoriesGuide -Destination (Join-Path $PortableDir "REVIEWED_CATEGORIES.md")
    }
    $NeighborGuide = Join-Path $Root "docs\source_neighbors.md"
    if (Test-Path -LiteralPath $NeighborGuide -PathType Leaf) {
        Copy-Item -LiteralPath $NeighborGuide -Destination (Join-Path $PortableDir "SOURCE_NEIGHBORS.md")
    }
    foreach ($Guide in @('whole_study.md', 'local_model_assets.md', 'portable_runtime.md', 'candidate_data_review.md', 'main_candidate_review.md')) {
        $GuideSource = Join-Path (Join-Path $Root "docs") $Guide
        if (Test-Path -LiteralPath $GuideSource -PathType Leaf) { Copy-Item -LiteralPath $GuideSource -Destination (Join-Path $PortableDir $Guide.ToUpperInvariant()) }
    }
    $VerifiedRuntimeTargets = @{}
    if ($RuntimeDirectory) {
        $RuntimeTarget = Join-Path $PortableDir "runtime"
        New-Item -ItemType Directory -Path $RuntimeTarget -ErrorAction Stop | Out-Null
        foreach ($Record in $RuntimeFiles) {
            Copy-Item -LiteralPath $Record.source -Destination (Join-Path $RuntimeTarget $Record.path) -ErrorAction Stop
        }
        Copy-Item -LiteralPath $RuntimeManifestPath -Destination (Join-Path $RuntimeTarget "TRUSTED_FILES.json") -ErrorAction Stop
        $CopiedRuntime = @(Test-PortableRuntimeSource -Directory $RuntimeTarget -ManifestPath (Join-Path $RuntimeTarget "TRUSTED_FILES.json") -ManifestSHA256 $RuntimePin)
        foreach ($Record in $CopiedRuntime) { $VerifiedRuntimeTargets[$Record.source] = $true }
        $RuntimeBuildNotice = "Pinned b12359 CPU runtime is bundled under runtime/ (53 verified files plus its trusted manifest). Models are not bundled."
    } else {
        $RuntimeBuildNotice = "No local inference executable is bundled. Select a separately trusted runtime explicitly; there is no global-service fallback."
    }
    $NeighborNotices = Join-Path $Root "docs\third_party\neighbor-runtime"
    if (Test-Path -LiteralPath $NeighborNotices -PathType Container) {
        Copy-Item -LiteralPath $NeighborNotices -Destination (Join-Path $PortableDir "NEIGHBOR_RUNTIME_NOTICES") -Recurse
    }
    $Excluded = @("sentence_transformers", "transformers", "torch", "torchvision", "torchaudio", "tensorflow", "keras", "kiwipiepy", "kiwipiepy_model")
    @"
Development portable build, not a signed or published release.
Optional NLP stacks are excluded: $($Excluded -join ', ').
Legacy lexical fallbacks remain available; sentence-transformers and Kiwi are not bundled.
The optional source-neighbor feature includes ONNX Runtime, tokenizers and NumPy libraries.
Its reviewed small encoder model/profile must be supplied separately; there is no silent fallback.
$RuntimeBuildNotice
No model weights, study datasets or credentials are bundled.
Optional model manager verifies existing GGUF files offline or downloads a fixed
catalog model only after explicit unchecked-by-default network consent.
A successful build is not functional acceptance; inspect the separate packaged smoke report.

Quick start
1. Extract the entire ZIP and keep ThemeForge.exe beside its _internal folder.
   Launch ThemeForge.exe. Keep your transcripts and exported drafts outside this folder.
2. Open Whole-study coding queue for complete imports, native token planning,
   explicit local work periods and coverage. Its Optional models button verifies
   existing weights or offers an explicit pinned download. No model is bundled.
   Local candidate codebook remains the bounded sample and detailed review path.
   Use Open draft JSON to inspect an existing exported draft without loading a model
   or importing its transcripts into the current project. Opening a draft does not
   start inference. Only open draft files from a source you trust.
3. Generation is separate and explicit. It requires a verified compatible GGUF
   model and trusted native runtime. The optional model manager can verify existing
   model files without network, or download a selected pinned model after consent.
   A bundled runtime is used only when its trusted manifest verifies; otherwise
   supply a trusted local runtime explicitly. No model automatically starts.
   Qwen 3.03 GB and Gemma 5.34 GB need additional CPU/RAM; fit is not guaranteed.
4. For a new run, import your transcripts, choose the appropriate speaker-label
   style, select open or guided mode, and inspect the bounded passage selection.
   Guided mode uses your research questions as an analytic lens; open mode does not.
   Check target text and separate context before explicitly running local codes.
5. Review each proposed label, claim, definition and exact source evidence.
   Explicit evidence revisions retain original model support IDs, review history
   and reviewer origin (researcher or assistant_development_review). Citation repair
   does not validate a claim or rewrite the original raw model response.
6. Export JSON to preserve the complete draft and provenance for later reopening.
   Export HTML for readable source-linked review. Choose fresh export filenames.
7. Drafts remain unreviewed_machine_draft. Source/format checks and completed
   definitions are not semantic approval, accuracy evidence or validated themes.
   Any category proposal requires an explicit review gate and remains provisional.
8. Source-only review (no model) shows exact selected turns with source hashes,
   offsets and separate context. Open source-card JSON reopens its JSON export
   without importing codes. These cards contain no conceptual interpretation.
   Source-card JSON exports and loads share a 16 MiB limit.
9. Add category (no model) lets you write a provisional relationship over 2 to 6
   reviewed codes from distinct source contents. Record each member's contribution,
   limits and a rationale. Authorship is explicit; the original model outcome and
   source evidence remain intact. See REVIEWED_CATEGORIES.md for the walkthrough.
   Save the parent draft first; changing a saved category requires a new branch.
10. Candidate source neighbors compares original selected turns with a small local
    encoder and TF-IDF. Inspect exact evidence, contrasts and unassigned turns;
    record explicitly attributed AI or researcher review separately from candidates.
    No category or theme is automatically accepted. See SOURCE_NEIGHBORS.md.
    Compare saved-vector rules exports all six retrieval settings without another
    model call. Original candidates and reviews stay intact; each rule branch
    starts an empty review. CLI: --neighbor-rules --graph RAW_GRAPH --out NEW_FOLDER.
11. Whole-study queue imports all routed turns, plans source-exact work with actual
    native prompt/token accounting, and runs a bounded sequential work period.
    Resume pending work in a fresh output folder; completed calls are preserved.
    Codes, abstentions, failures, pending turns and incomplete definitions remain
    explicit. Source/execution coverage is not exhaustive semantic coding.
    Choose open mode or guided research questions and review every draft.
    See WHOLE_STUDY.md and LOCAL_MODEL_ASSETS.md when included.
12. Review candidate dataset opens explicitly fictional education candidates with
    immutable source evidence, separate AI proposals and attributed revision history.
    Save only to fresh JSON/HTML/CSV filenames. Editing never admits training data.
    See CANDIDATE_DATA_REVIEW.md. Candidate records are a separate local artifact.
This build has not established clean-machine compatibility. Review separate QA.
"@ | Set-Content -LiteralPath (Join-Path $PortableDir "PORTABLE_BUILD.txt") -Encoding UTF8
    $Files = @(Get-ChildItem -LiteralPath $PortableDir -Recurse -File)
    $Forbidden = @($Files | Where-Object {
        $_.Extension -in @('.gguf', '.safetensors', '.onnx', '.pt', '.pth', '.ckpt', '.tfproj', '.pdf', '.csv', '.xlsx') -or
        $_.Name -in @('.env', 'credentials.json', 'token.json') -or $_.Name -like '.env.*' -or
        ($_.Name -ieq 'llama-server.exe' -and -not $VerifiedRuntimeTargets.ContainsKey($_.FullName))
    })
    if ($Forbidden.Count -gt 0) { throw "Unexpected model/data/credential payload found; output preserved: $($Forbidden.FullName -join ', ')" }
    $Manifest = @($Files | Sort-Object FullName | ForEach-Object {
        [ordered]@{ path = $_.FullName.Substring($PortableDir.Length + 1); bytes = $_.Length; sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
    })
    $Info = [ordered]@{
        recorded_utc = [DateTime]::UtcNow.ToString("o"); status = 'development_build_completed_smoke_pending'
        version = $Version; python = $Python; pyinstaller_version = "$PyInstallerVersion"; expected_pyinstaller_version = $ExpectedPyInstallerVersion; spec = $Spec
        isolated_output = $Output; executable = (Join-Path $PortableDir "ThemeForge.exe")
        optional_nlp_excluded = $Excluded; bundled_models = $false; bundled_study_datasets = $false
        bundled_native_runtime = [bool]$RuntimeDirectory; runtime_manifest_sha256 = $RuntimePin
        verified_runtime_files = $RuntimeFiles.Count
        forbidden_payload_scan_passed = $true; no_old_output_deleted = $true
        source_sha256 = $SourceHashes; files = $Manifest
    }
    $Info | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $Output "BUILD_INFO.json") -Encoding UTF8
    Compress-Archive -LiteralPath $PortableDir -DestinationPath $ZipPath -ErrorAction Stop
    [ordered]@{ path = $ZipPath; bytes = (Get-Item -LiteralPath $ZipPath).Length; sha256 = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant() } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Output "ARCHIVE.json") -Encoding UTF8
    Write-Host "Created isolated development portable: $ZipPath"
} finally {
    Pop-Location
    [Environment]::SetEnvironmentVariable("PYINSTALLER_CONFIG_DIR", $PreviousPyInstallerConfig, "Process")
}
