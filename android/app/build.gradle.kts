import java.util.Properties
plugins { id("com.android.application") }
val appVersion = Properties().apply { rootProject.file("version.properties").inputStream().use { load(it) } }
val releaseStore = providers.environmentVariable("ANDROID_KEYSTORE_PATH").orNull
android {
    namespace = "org.watchcraft.android"
    compileSdk { version = release(37) { minorApiLevel = 0 } }
    defaultConfig {
        applicationId = "org.watchcraft.android"
        minSdk = 26
        targetSdk = 37
        versionCode = appVersion.getProperty("versionCode").toInt()
        versionName = appVersion.getProperty("versionName")
        testInstrumentationRunner = "android.test.InstrumentationTestRunner"
    }
    if (releaseStore != null) signingConfigs.create("production") {
        storeFile = file(releaseStore)
        storePassword = providers.environmentVariable("ANDROID_KEYSTORE_PASSWORD").get()
        keyAlias = providers.environmentVariable("ANDROID_KEY_ALIAS").get()
        keyPassword = providers.environmentVariable("ANDROID_KEY_PASSWORD").get()
    }
    buildTypes {
        getByName("debug") { applicationIdSuffix = ".debug"; versionNameSuffix = "-debug" }
        getByName("release") {
            if (releaseStore != null) signingConfig = signingConfigs.getByName("production")
            isMinifyEnabled = false
        }
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    buildFeatures { buildConfig = true }
    lint { abortOnError = true }
}
dependencies {
    implementation("androidx.activity:activity:1.13.0")
    testImplementation("junit:junit:4.13.2")
}
// Never accidentally distribute an unsigned or debug-signed production APK.
gradle.taskGraph.whenReady {
    if (allTasks.any { it.name == "assembleRelease" || it.name == "bundleRelease" }) {
        check(releaseStore != null) { "Production builds require ANDROID_KEYSTORE_PATH and signing credentials. See android/README.md." }
    }
}
