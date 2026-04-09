# iOS Signing & Authentication Setup

Boss manages iOS/TestFlight delivery locally. It **never** stores private keys or secrets — it only reads *references* to credentials you set up on your machine.

## Config file

Create `~/.boss/ios-signing.json`:

```json
{
    "api_key": {
        "key_id": "ABC123XYZ",
        "issuer_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
        "key_path": "~/.boss/keys/AuthKey_ABC123XYZ.p8"
    },
    "team_id": "ABCD1234EF",
    "fastlane": {
        "match_git_url": "git@github.com:your-org/certs.git",
        "match_type": "appstore",
        "match_readonly": true,
        "api_key_path": "~/.boss/keys/api_key.json"
    },
    "keychain": {
        "name": "login",
        "allow_create": false
    }
}
```

All fields are optional. Boss reports what is present and usable, so you can add credentials incrementally.

## Step-by-step

### 1. App Store Connect API key (required for uploads)

This is the preferred authentication method. It avoids interactive Apple ID sessions entirely.

1. Go to [App Store Connect → Users and Access → Integrations → Team Keys](https://appstoreconnect.apple.com/access/integrations/api).
2. Click **Generate API Key**. Choose a name (e.g. "Boss CI") and the **Developer** role (enough for TestFlight uploads).
3. Download the `.p8` file. **You can only download it once.**
4. Note the **Key ID** shown next to the key name.
5. Note the **Issuer ID** shown at the top of the keys page.

Place the key file on disk and lock permissions:

```sh
mkdir -p ~/.boss/keys
mv ~/Downloads/AuthKey_ABC123XYZ.p8 ~/.boss/keys/
chmod 600 ~/.boss/keys/AuthKey_ABC123XYZ.p8
```

Then fill in the `api_key` section of `~/.boss/ios-signing.json` with the key ID, issuer ID, and path to the `.p8` file. Tilde (`~`) and environment variables (`$HOME`) are expanded automatically.

### 2. Team ID (required for signing)

Your 10-character Apple Developer Team ID. Find it at [developer.apple.com/account](https://developer.apple.com/account) → Membership Details.

If your Xcode project already has `DEVELOPMENT_TEAM` set in the `.pbxproj`, Boss reads it from there automatically. The config file value acts as a fallback when the project doesn't specify one.

### 3. Fastlane Match (optional — managed certificates)

If you use [fastlane match](https://docs.fastlane.tools/actions/match/) for certificate and profile management:

- **`match_git_url`**: The git repo that stores your encrypted certificates.
- **`match_type`**: Usually `"appstore"` for TestFlight/App Store builds.
- **`match_readonly`**: Set `true` to avoid accidental cert regeneration. Recommended.
- **`api_key_path`**: Path to a fastlane-format API key JSON file (see [fastlane docs](https://docs.fastlane.tools/app-store-connect-api/)). This is separate from the `.p8` file — fastlane wraps it in its own JSON envelope.

To create the fastlane API key JSON:

```sh
# Using fastlane itself:
fastlane run create_api_key \
  key_id:ABC123XYZ \
  issuer_id:xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx \
  key_filepath:~/.boss/keys/AuthKey_ABC123XYZ.p8

# Or manually create ~/.boss/keys/api_key.json:
{
  "key_id": "ABC123XYZ",
  "issuer_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----",
  "in_house": false
}
```

**Do not commit the fastlane API key JSON or `.p8` files to version control.**

### 4. Keychain (optional)

By default Boss assumes the `login` keychain. The `keychain` config section is informational — Boss does not create or modify keychains automatically.

- **`name`**: Keychain name to use (default: `"login"`).
- **`allow_create`**: Whether Boss may create a temporary keychain for CI-style builds. Default `false`. Not recommended for local development.

## Checking your setup

### API endpoint

With the Boss server running:

```sh
curl -s http://127.0.0.1:8321/api/system/ios-signing | python3 -m json.tool
```

This returns a credential readiness report — which credentials are available, missing, or misconfigured — without revealing any secrets.

### In a chat

Ask Boss: *"What's my iOS signing status?"* — it will check the config and report what's ready and what's missing.

## Security notes

- **Private keys stay on disk.** Boss reads only the file path and checks that the file exists and has a valid PEM header. It never reads or logs key contents.
- **Secrets are redacted in API responses.** Issuer IDs are partially masked. Full file paths are collapsed to `~/`-relative form.
- **File permissions are checked.** Boss warns if a `.p8` key file is world-readable (`chmod 644` or wider). Use `chmod 600`.
- **Nothing is sent upstream.** All signing checks are local. The only external calls are to Apple's APIs during actual uploads, using Apple's standard tooling (Xcode, altool, or fastlane).
- **Do not commit `~/.boss/ios-signing.json` or key files to any repository.**
