# Field App — Release Pipeline

Signed release builds for the **dotmac_field** technician app.
Push/FCM setup is in [FCM_SETUP.md](FCM_SETUP.md); this doc covers shipping.

- **Android** → GitHub Actions (`.github/workflows/field-app-release.yml`) → signed `.aab`/`.apk` artifact
- **iOS** → Xcode Cloud (`ios/ci_scripts/ci_post_clone.sh`) → TestFlight

Both are **scaffolded and wired**; they need credentials + store records (below)
before they can actually ship. Nothing else in the app blocks release.

App identifiers:
- Android `applicationId`: `io.dotmac.dotmac_field`
- iOS bundle id: `io.dotmac.dotmacField`
- API base defaults to `https://crm.dotmac.io` (override with `--dart-define=API_BASE_URL=...`)

---

## Android

### One-time setup
1. **Generate an upload keystore** (Play requires a fresh key):
   ```bash
   keytool -genkey -v -keystore upload-keystore.jks -keyalg RSA -keysize 2048 \
     -validity 10000 -alias upload
   ```
   Back this file up — losing it before Play App Signing enrollment means you can
   never update the app.
2. **Add GitHub repo secrets** (Settings → Secrets → Actions):
   | Secret | Value |
   |---|---|
   | `ANDROID_KEYSTORE_BASE64` | `base64 -i upload-keystore.jks` |
   | `ANDROID_KEYSTORE_PASSWORD` | keystore password |
   | `ANDROID_KEY_ALIAS` | `upload` |
   | `ANDROID_KEY_PASSWORD` | key password |
   | `ANDROID_GOOGLE_SERVICES_JSON_B64` | *(optional)* `base64 -i google-services.json` — enables FCM |
   | `MOBILE_SENTRY_DSN` | *(optional)* Sentry DSN for crash reporting |

### Build
- Manual: **Actions → Field App Release → Run workflow** (choose `appbundle` or `apk`)
- Tag: push `field-mobile-v1.0.1` → builds automatically

The signed artifact `dotmac-field-android-release` is attached to the run.

### Signing plumbing (already in the repo)
- `android/app/build.gradle.kts` reads `android/key.properties`; without it, release
  builds fall back to the debug key so local `flutter run --release` still works.
- The `com.google.gms.google-services` plugin is declared in `settings.gradle.kts`
  and **applied conditionally** — only when `android/app/google-services.json`
  exists. So you don't need `flutterfire configure` to touch Gradle; just drop the
  JSON in (or provide the `ANDROID_GOOGLE_SERVICES_JSON_B64` secret) and FCM turns on.

### Play Console
The app is a fresh publish (no prior listing). You'll need: store listing, content
rating, Data safety form, target audience, countries, and — for a new personal
account — a 12-tester / 14-day closed test before production.

---

## iOS (Xcode Cloud → TestFlight)

iOS release archives are built by **Xcode Cloud**, not GitHub Actions (the
`ios-release` job in the workflow is a deliberate gate that points here). Xcode
Cloud owns Apple signing via managed certificates.

### One-time setup
1. **App Store Connect**: create an app record for bundle id `io.dotmac.dotmacField`.
2. **Xcode Cloud**: create a workflow on `ios/Runner.xcworkspace`.
   `ios/ci_scripts/ci_post_clone.sh` auto-runs after clone and bootstraps Flutter
   (pinned to the revision in `mobile/.metadata`), runs drift codegen, and builds.
3. **Post-Actions**: add **TestFlight Internal Testing** and select your tester
   group so builds attach automatically (otherwise each build must be added by hand).

### FCM push (optional, operator-gated)
Add these as Xcode Cloud environment variables (secret):
| Variable | Value |
|---|---|
| `GOOGLE_SERVICE_INFO_PLIST_B64` | `base64 -i GoogleService-Info.plist` |
| `API_BASE_URL` | override backend (defaults to prod) |
| `SENTRY_DSN` | crash reporting |

When `GOOGLE_SERVICE_INFO_PLIST_B64` is set, `ci_post_clone.sh` materializes the
plist, flips `Runner.entitlements` to `aps-environment: production`, and runs
`wire_firebase.rb` to bundle the plist + attach the entitlement to the Runner
target. Without it the app builds with push disabled (`NoopPushSource`).

Also upload your APNs auth key (`.p8`) to Firebase → Project Settings → Cloud
Messaging so the server can deliver to iOS.

---

## What's still yours to provide (not code)
1. Firebase project → `google-services.json` + `GoogleService-Info.plist` + backend
   `FCM_SERVICE_ACCOUNT_JSON` / `FCM_PROJECT_ID` (see [FCM_SETUP.md](FCM_SETUP.md))
2. Android upload keystore → the four `ANDROID_*` GitHub secrets
3. Store records: App Store Connect app + Play Console listing
