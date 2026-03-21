#
# Windows release pipeline: build → (sign) → publish to CDN.
#
# Usage:
#   .\release_win.ps1              # Interactive — prompts for version + notes
#   .\release_win.ps1 -Test        # Unsigned build only (no sign/publish)
#
# Prerequisites:
#   - Python 3.10+ available as 'python'
#   - Windows SDK (for SignTool.exe) — needed for signing
#   - AWS CLI configured for S3 upload
#

param(
    [switch]$Test
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# ── Configuration (set these as environment variables) ──
# FM_WIN_SIGN_CERT    - Path to .pfx code signing certificate
# FM_WIN_SIGN_PASS    - Certificate password (or use -csp for hardware token)
# FM_S3_BUCKET        - S3 bucket name for CDN uploads
# FM_CDN_BASE         - CDN base URL (e.g. https://yourdomain.com)
if (-not $Test) {
    $SignCert = $env:FM_WIN_SIGN_CERT
    if (-not $SignCert) { Write-Error "Set FM_WIN_SIGN_CERT env var"; exit 1 }
    $Bucket = $env:FM_S3_BUCKET
    if (-not $Bucket) { Write-Error "Set FM_S3_BUCKET env var"; exit 1 }
    $CdnBase = $env:FM_CDN_BASE
    if (-not $CdnBase) { Write-Error "Set FM_CDN_BASE env var"; exit 1 }
}

$Prefix = "desktop"

# ── Prompt for version ──
$CurrentVersion = (Select-String -Path main.py -Pattern 'APP_VERSION = "([^"]*)"').Matches[0].Groups[1].Value
Write-Host "Current version: $CurrentVersion"
$Version = Read-Host "New version"
if (-not $Version) {
    Write-Error "Version is required"
    exit 1
}

# ── Prompt for release notes ──
$Notes = ""
if (-not $Test) {
    $Notes = Read-Host "Release notes"
}

$AppName = "FoodieMoiety"
$ExePath = "dist\$AppName\$AppName.exe"
$BuildVenv = ".build_venv_win"

# ── Bump version ──
Write-Host ""
Write-Host "Bumping version to $Version..."
(Get-Content main.py) -replace 'APP_VERSION = "[^"]*"', "APP_VERSION = `"$Version`"" | Set-Content main.py
(Get-Content foodie_moiety_prod_win.spec) -replace "version='[^']*'", "version='$Version'" | Set-Content foodie_moiety_prod_win.spec

Write-Host ""
Write-Host "=========================================="
Write-Host "  Building Foodie Moiety v$Version"
if ($Test) {
    Write-Host "  Mode: TEST (unsigned, no publish)"
} else {
    Write-Host "  Mode: RELEASE (signed + publish)"
}
Write-Host "=========================================="
Write-Host ""

# ── Step 1: Create isolated build virtualenv ──
Write-Host "Step 1/5: Creating build virtualenv..."
if (Test-Path $BuildVenv) {
    Remove-Item -Recurse -Force $BuildVenv
}
python -m venv $BuildVenv
& "$BuildVenv\Scripts\Activate.ps1"

Write-Host "  Installing build dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements-build.txt pyinstaller
Write-Host ""

# ── Step 2: Generate clean production database ──
Write-Host "Step 2/5: Generating clean production database..."
python create_prod_db.py
Copy-Item "dist\foodie_moiety_prod.db" "dist\foodie_moiety.db"
Write-Host ""

# ── Step 3: Run PyInstaller ──
Write-Host "Step 3/5: Running PyInstaller (Windows production spec)..."
pyinstaller foodie_moiety_prod_win.spec --clean --noconfirm
Write-Host ""

# ── Step 4: Code sign ──
if (-not $Test) {
    Write-Host "Step 4/5: Code signing executable..."
    $SignTool = Get-ChildItem "C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -Last 1
    if (-not $SignTool) {
        Write-Error "SignTool.exe not found. Install the Windows SDK."
        exit 1
    }

    $SignArgs = @(
        "sign",
        "/f", $SignCert,
        "/tr", "http://timestamp.digicert.com",
        "/td", "sha256",
        "/fd", "sha256"
    )

    # Add password if provided
    if ($env:FM_WIN_SIGN_PASS) {
        $SignArgs += "/p"
        $SignArgs += $env:FM_WIN_SIGN_PASS
    }

    # Sign all DLLs and EXEs in the bundle
    Write-Host "  Signing binaries..."
    Get-ChildItem "dist\$AppName" -Recurse -Include "*.exe","*.dll" | ForEach-Object {
        & $SignTool.FullName @SignArgs $_.FullName 2>$null
    }

    # Sign the main executable last (with full verification)
    Write-Host "  Signing main executable..."
    & $SignTool.FullName @SignArgs $ExePath
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Code signing failed"
        exit 1
    }

    # Verify
    Write-Host "  Verifying signature..."
    & $SignTool.FullName verify /pa $ExePath
    Write-Host "  Signature OK"
} else {
    Write-Host "Step 4/5: Skipping code signing (test mode)"
}
Write-Host ""

# ── Step 5: Report results ──
$BundleSize = [math]::Round((Get-ChildItem -Recurse "dist\$AppName" | Measure-Object -Property Length -Sum).Sum / 1MB)

# Cleanup build venv
deactivate
Remove-Item -Recurse -Force $BuildVenv

if (-not $Test) {
    Write-Host "Build complete. Test the app before publishing:"
    Write-Host "  dist\$AppName\$AppName.exe"
    Write-Host ""
    $Confirm = Read-Host "Ready to publish to CDN? (y/N)"
    if ($Confirm -notmatch "^[Yy]$") {
        Write-Host ""
        Write-Host "Publish skipped. To publish later, run from mac or use AWS CLI:"
        Write-Host "  ./publish-desktop-release.sh --version $Version --win dist\$AppName --notes `"$Notes`""
        exit 0
    }

    Write-Host ""
    Write-Host "=========================================="
    Write-Host "  Publishing to CDN"
    Write-Host "=========================================="
    Write-Host ""

    $InstallerKey = "$Prefix/$AppName-$Version.zip"
    $ManifestKey = "$Prefix/latest_version.json"

    # Create a zip of the app folder for distribution
    $ZipPath = "dist\$AppName-$Version.zip"
    Write-Host "Creating distribution archive..."
    Compress-Archive -Path "dist\$AppName\*" -DestinationPath $ZipPath -Force

    Write-Host "Uploading Windows installer..."
    aws s3 cp $ZipPath "s3://$Bucket/$InstallerKey" `
        --cache-control "public, max-age=31536000, immutable" `
        --content-type "application/zip"

    # Preserve Mac URL from existing manifest
    $MacUrl = ""
    try {
        $Existing = aws s3 cp "s3://$Bucket/$ManifestKey" - 2>$null | ConvertFrom-Json
        $MacUrl = $Existing.mac_url
    } catch {}

    Write-Host "Uploading version manifest..."
    $Manifest = @{
        version = $Version
        mac_url = $MacUrl
        win_url = "$CdnBase/$InstallerKey"
        notes   = $Notes
    } | ConvertTo-Json

    Write-Host "  Manifest contents:"
    Write-Host $Manifest
    $Manifest | aws s3 cp - "s3://$Bucket/$ManifestKey" `
        --cache-control "public, max-age=300" `
        --content-type "application/json"

    # Invalidate CloudFront cache
    try {
        $CfDist = aws cloudfront list-distributions --query "DistributionList.Items[?contains(Aliases.Items,'foodiemoiety.com')].Id" --output text 2>$null
        if ($CfDist) {
            Write-Host "  Invalidating CloudFront cache for manifest..."
            aws cloudfront create-invalidation --distribution-id $CfDist --paths "/$ManifestKey" 2>$null | Out-Null
        }
    } catch {
        Write-Host "  Warning: CloudFront invalidation failed (non-critical)"
    }

    # Commit version bump
    Write-Host ""
    Write-Host "Committing version bump..."
    git add main.py foodie_moiety_prod_win.spec
    git commit -m "Bump version to $Version"

    Write-Host ""
    Write-Host "=========================================="
    Write-Host "  Release complete!"
    Write-Host "=========================================="
    Write-Host "  Version:    $Version"
    Write-Host "  Size:       ${BundleSize}MB"
    Write-Host "  Manifest:   $CdnBase/$ManifestKey"
    Write-Host "  Download:   $CdnBase/$InstallerKey"
    Write-Host ""
    Write-Host "Don't forget to: git push"
} else {
    Write-Host "=========================================="
    Write-Host "  Test build complete"
    Write-Host "=========================================="
    Write-Host "  Version:  $Version"
    Write-Host "  Size:     ${BundleSize}MB"
    Write-Host "  Signed:   No (test mode)"
    Write-Host ""
    Write-Host "Test with: dist\$AppName\$AppName.exe"
    Write-Host ""
    Write-Host "For a full release: .\release_win.ps1"
}
