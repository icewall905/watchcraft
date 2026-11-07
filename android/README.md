# WatchCraft for Android

The Android client connects to a WatchCraft server address entered on the device. The default server is `https://wc.tdgs.org`; users can change it in the app. It combines native Android navigation and device integrations with the WatchCraft web interface hosted by the connected server. Its TV, Today, Library, Alerts, and More tabs take users into server-provided feature screens; the native shell adds search, sharing, browser opening, downloads, microphone permission, fullscreen video, and back navigation. File downloads use Android’s save-file picker and must finish while WatchCraft is open; in-memory exports support files up to 16 MB. Server-side saved media remains on the server. Native background notifications and background media playback are not provided by this version. This keeps the service's existing feature set available without pretending the service's screens are separate native implementations.

## Build and install a debug APK

With the Android SDK and JDK 21 available, build and install the debug variant from the repository root:

```sh
android/gradlew -p android :app:assembleDebug
adb install -r android/app/build/outputs/apk/debug/app-debug.apk
```

Debug builds use Android's debug key and are for development. They cannot update an installation signed with the production release key.

## Production release signing

Release builds must keep the same signing key for every version so Android and Obtainium can update an existing installation. The release build reads these environment variables:

- `ANDROID_KEYSTORE_PATH`: path to the production keystore file
- `ANDROID_KEYSTORE_PASSWORD`: keystore password
- `ANDROID_KEY_ALIAS`: signing key alias
- `ANDROID_KEY_PASSWORD`: signing key password

For a local release build, set these variables in your shell and run `android/gradlew -p android :app:assembleRelease`. Keep the keystore and passwords out of the repository and build logs. Back up the original keystore and its passwords in a secure, separate location; losing or replacing this key prevents updates to installations signed by it.

GitHub Actions also requires repository secrets named `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, and `ANDROID_KEY_PASSWORD`. `ANDROID_KEYSTORE_BASE64` is the base64 encoding of the existing production keystore. The local initial signing key is kept outside the repository at `~/.local/share/watchcraft-android/release.jks`, with private signing metadata in the same directory. Back up that directory securely. The workflow decodes it into the runner's temporary directory and passes its path to Gradle. Create these secrets from the same stable production key used for every previous release. The workflow does not create signing keys.

## Obtainium updates

Add the GitHub repository as an Obtainium app source:

```text
https://github.com/icewall905/watchcraft
```

Select GitHub Releases as the source and set the release tag filter to `android-v.*`. Each release tag has the form `android-v<versionName>`, such as `android-v0.1.0`; its release contains `watchcraft-<versionName>.apk` and `SHA256SUMS`. Obtainium updates require every APK to use the same production signing key and Android application ID.

## Release workflow

`Android release` can be started manually from the Actions page to build a signed APK artifact. A push of an `android-v*` tag also builds the APK and publishes a GitHub Release. Before publishing, the workflow checks that the tag exactly matches `versionName` in `android/version.properties` and that `versionCode` is greater than the previous Android release. The build uses Java 21 and Gradle 9.6.1. Only the publish job receives `contents: write`; the build job has read-only repository access.

For example, after setting the Android version to `0.1.1`, commit the change and push the matching tag:

```sh
git tag android-v0.1.1
git push origin android-v0.1.1
```

The APK and checksum are attached to that GitHub Release for Obtainium to discover.
