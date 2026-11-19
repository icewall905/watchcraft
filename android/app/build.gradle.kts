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
        val firebaseResources = mapOf("APP_ID" to "google_app_id", "API_KEY" to "google_api_key",
            "SENDER_ID" to "gcm_defaultSenderId", "PROJECT_ID" to "project_id")
        val firebaseValues = firebaseResources.mapValues { (name, _) ->
            providers.environmentVariable("WATCHCRAFT_FIREBASE_$name").orElse("").get()
        }
        if (firebaseValues.values.any { it.isNotEmpty() }) {
            require(firebaseValues.values.all { it.matches(Regex("[a-zA-Z0-9:_-]+")) }) {
                "Set all four WATCHCRAFT_FIREBASE build variables to valid Firebase values."
            }
            firebaseResources.forEach { (name, resource) ->
                resValue("string", resource, firebaseValues.getValue(name))
            }
        }
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
    buildFeatures { buildConfig = true; resValues = true }
    lint { abortOnError = true }
}
dependencies {
    implementation("com.google.firebase:firebase-messaging:26.0.2")
    implementation("androidx.activity:activity:1.13.0")
    testImplementation("junit:junit:4.13.2")
}
// Never accidentally distribute an unsigned or debug-signed production APK.
gradle.taskGraph.whenReady {
    if (allTasks.any { it.name == "assembleRelease" || it.name == "bundleRelease" }) {
        check(releaseStore != null) { "Production builds require ANDROID_KEYSTORE_PATH and signing credentials. See android/README.md." }
    }
}
