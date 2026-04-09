# iOS Delivery

Boss can archive, export, sign, and upload iOS apps to TestFlight from the local machine. All execution happens locally through governed subprocesses — no CI server required.

## Prerequisites

### Required

- **macOS** with Xcode installed (provides `xcodebuild`, `xcrun`, `security`)
- **Xcode Command Line Tools** — run `xcode-select --install` if not present
- **An Xcode project or workspace** with at least one app target and a valid scheme

Verify with:

```
xcode-select -p          # → /Applications/Xcode.app/Contents/Developer
xcodebuild -version      # → Xcode 16.x
xcrun --version           # → xcrun version 84
```

### Optional (for TestFlight uploads)

- **fastlane** — preferred upload method (`brew install fastlane` or `gem install fastlane`)
- **App Store Connect API key** — `.p8` file from App Store Connect → Users and Access → Integrations → App Store Connect API
- **iOS signing configuration** at `~/.boss/ios-signing.json`

Without these, Boss can still archive and export IPAs. Uploads require at least an API key.

## Signing Configuration

Create `~/.boss/ios-signing.json`:

```json
{
  "api_key": {
    "key_id": "XXXXXXXXXX",
    "issuer_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    "key_path": "~/.boss/AuthKey_XXXXXXXXXX.p8"
  },
  "team_id": "ABCD1234EF",
  "fastlane": {
    "api_key_path": "~/.boss/fastlane_api_key.json",
    "match_git_url": "",
    "match_type": "appstore",
    "match_readonly": true
  },
  "keychain": {
    "name": "login",
    "allow_create": false
  }
}
```

### Fields

| Field | Required for | Description |
|---|---|---|
| `api_key.key_id` | Upload | 10-character key ID from App Store Connect |
| `api_key.issuer_id` | Upload | UUID issuer from App Store Connect |
| `api_key.key_path` | Upload | Path to `.p8` private key file |
| `team_id` | Signing | 10-character Apple Developer team ID |
| `fastlane.api_key_path` | Fastlane upload | Path to fastlane-format API key JSON |
| `fastlane.match_git_url` | Fastlane match | Git repo URL for certificate/profile storage |
| `keychain.name` | Signing | Keychain to search for signing identities |

### Security rules

- The `.p8` file must **not** be world-readable (`chmod 600` or `640`)
- Boss reads only the first line of the `.p8` to confirm the PEM header — it never loads the full private key into memory
- World-readable `.p8` files will cause the signing readiness check to report `INSECURE_PERMISSIONS` and block uploads

### Generating the API key

1. Go to [App Store Connect → Users and Access → Integrations → App Store Connect API](https://appstoreconnect.apple.com/access/integrations/api)
2. Click **Generate API Key**, choose **App Manager** role or higher
3. Download the `.p8` file (you can only download it once)
4. Note the **Key ID** and **Issuer ID**
5. Place the `.p8` at the path specified in `key_path`
6. Set permissions: `chmod 600 ~/.boss/AuthKey_XXXXXXXXXX.p8`

### Fastlane API key JSON

If using fastlane pilot for uploads, also create the fastlane-format key:

```json
{
  "key_id": "XXXXXXXXXX",
  "issuer_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----",
  "in_house": false
}
```

Save to `~/.boss/fastlane_api_key.json` and reference it in `fastlane.api_key_path`.

## Pipeline Phases

A delivery run proceeds through these phases sequentially:

| Phase | What happens | Can fail because |
|---|---|---|
| **Pending** | Run created, waiting for start | — |
| **Inspecting** | Scans Xcode project for targets, schemes, bundle IDs, signing config | No `.xcodeproj` found, no app targets |
| **Archiving** | Runs `xcodebuild archive` (or `fastlane gym`) | Compilation errors, signing failures, missing provisioning profiles |
| **Exporting** | Runs `xcodebuild -exportArchive` with generated `ExportOptions.plist` | Export method mismatch, team ID required |
| **Uploading** | Runs `fastlane pilot upload` or `xcrun altool --upload-app` | Invalid credentials, network errors, App Store Connect rejection |
| **Completed** | All phases succeeded | — |

If any phase fails, the run stops and records the error. The run can be retried from the UI.

### Upload methods

Boss resolves the upload method automatically:

1. **fastlane pilot** (preferred) — if fastlane is installed and `fastlane.api_key_path` is configured
2. **xcrun altool** (fallback) — if xcrun is available and `api_key` credentials are configured

Fastlane pilot can wait for App Store Connect processing and report when the build is ready for testing. Altool uploads but cannot query processing status.

## Using from the macOS App

1. Open Boss and navigate to **iOS Delivery** in the sidebar (or press ⌘0)
2. The **Signing & Credentials** card shows the current readiness state
3. Click **New Run** in the header to open the run creation form
4. Fill in the project path, optional scheme, configuration, export method, and upload target
5. Click **Start Pipeline** — the run appears in the **Runs** list with live phase progress
6. Click any run to see detail: metadata, artifact paths, logs, errors
7. Use **Copy** buttons to grab full artifact paths or log output
8. For failed runs, use **Retry** to create a new run with the same configuration

### What the UI shows

- **New Run form** — compact form for project path, scheme, config, export method, upload target
- **Phase progress bar** — visual indicator of which pipeline phase is active
- **Artifact paths** — archive, IPA, and dSYM locations with copy buttons
- **Build/export/upload logs** — scrollable log output per phase
- **Upload status** — tracks processing state for TestFlight builds
- **Error details** — specific failure messages with copy support

## Using from Chat

The iOS delivery tools are available to the agent in **agent mode**:

### Read-only inspection (all modes)

- `inspect_xcode_project` — scan a project path for targets, schemes, signing
- `list_xcode_schemes` — list available schemes with build/test capabilities
- `summarize_ios_project` — delivery-focused readiness summary

### Creating and starting runs (agent mode only)

- `start_ios_delivery` — create and start a delivery pipeline run (requires approval)
- `ios_delivery_status` — check progress of active and recent runs

**Approval required:** `start_ios_delivery` has execution type `run`, which requires user approval unless an `always_allow` permission rule is stored for the `ios-delivery:run` scope.

**Ask/plan/review modes** only have access to the read-only inspection tools. They cannot create or start delivery runs.

### Example chat workflow

1. "Inspect the Xcode project at ~/Developer/MyApp" — agent uses `inspect_xcode_project`
2. "Build and upload MyApp to TestFlight" — agent uses `start_ios_delivery` (triggers approval prompt)
3. "What's the status of my iOS build?" — agent uses `ios_delivery_status`

### What still requires manual setup

- Apple Developer account enrollment
- App Store Connect app record creation
- API key generation and `~/.boss/ios-signing.json` configuration
- Provisioning profile setup (automatic signing handles most cases)
- Code signing certificate installation in Keychain

## Diagnostics

### Dev Doctor

`dev_doctor.py` checks iOS delivery prerequisites:

```
[PASS] ios toolchain: xcodebuild: /usr/bin/xcodebuild (16.2)
[PASS] ios toolchain: xcrun: /usr/bin/xcrun (84)
[PASS] ios toolchain: fastlane: /usr/local/bin/fastlane (2.225.0)
[PASS] ios toolchain: security: /usr/bin/security (optional)
[PASS] ios toolchain: xcode-select: /Applications/Xcode.app/Contents/Developer
[PASS] ios signing config: api_key=XXXX…, p8=found, team=ABCD1234EF
```

### Signing readiness API

```
GET /api/ios-delivery/status
```

Returns `signing` object with `can_sign`, `can_upload`, and per-credential check details.

## Known Limitations

### Manual Apple setup required

Boss cannot automate these steps — they require human action in Apple's web portals:

- **Apple Developer enrollment** — you must have an active Apple Developer Program membership
- **App Store Connect app record** — create the app record manually before first upload
- **App Store Connect API key generation** — download the `.p8` from the portal
- **Provisioning profiles** — either manage manually or use fastlane match
- **Certificates** — signing identity must exist in your local keychain or via match

### Pipeline limitations

- **No incremental builds** — each archive is a clean build
- **No parallel runs** — one pipeline at a time (concurrent runs will interleave subprocesses)
- **No automatic retry on transient failures** — network errors during upload require manual retry
- **altool upload status** — altool cannot query processing status; you must check App Store Connect manually
- **Fastlane pilot processing wait** — pilot waits for processing by default, which can take 15–30 minutes

### Signing edge cases

- **Manual signing** — Boss sets `CODE_SIGN_ALLOW_PROVISIONING_UPDATES=YES` which works best with automatic signing
- **Enterprise distribution** — export method `enterprise` is supported but untested
- **Multiple teams** — only one team ID per signing config; switch configs manually if needed

## File Locations

| Path | Purpose |
|---|---|
| `~/.boss/ios-signing.json` | Signing credentials configuration |
| `~/.boss/ios-deliveries/<run_id>.json` | Per-run state (phase, paths, logs, error) |
| `~/.boss/ios-deliveries/<run_id>.events.jsonl` | Append-only event log per run |
| `boss/ios_delivery/` | Engine, state, toolchain, signing, upload, runner modules |
| `boss/tools/ios.py` | Governed tool definitions for chat agent |
| `BossApp/Sources/IOSDeliveryView.swift` | macOS app delivery surface |
| `BossApp/Sources/State/IOSDeliveryState.swift` | Client-side state management |
