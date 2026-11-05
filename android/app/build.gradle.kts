plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

// Assinatura fixa, para o celular aceitar a versão nova por cima da antiga. Enquanto o app é só de teste
// (instalado à mão, fora da Play Store), usa a chave de teste do repositório; para publicar, troque pelos
// secrets NEXOCAMPO_KEYSTORE (caminho do arquivo) e NEXOCAMPO_KEYSTORE_SENHA no GitHub.
val chave: String = System.getenv("NEXOCAMPO_KEYSTORE") ?: "assinatura-teste.jks"
val senhaChave: String = System.getenv("NEXOCAMPO_KEYSTORE_SENHA") ?: "nexocampo-teste"

android {
    namespace = "br.eng.mdsolucoes.nexocampo"
    compileSdk = 35

    defaultConfig {
        applicationId = "br.eng.mdsolucoes.nexocampo"
        minSdk = 26
        targetSdk = 35
        versionCode = (System.getenv("GITHUB_RUN_NUMBER") ?: "1").toInt()
        versionName = "0.1.${System.getenv("GITHUB_RUN_NUMBER") ?: "0"}"
    }

    signingConfigs {
        create("release") {
            storeFile = file(chave)
            storePassword = senhaChave
            keyAlias = "nexocampo"
            keyPassword = senhaChave
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("release")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { compose = true }
}

dependencies {
    val bom = platform("androidx.compose:compose-bom:2024.10.01")
    implementation(bom)
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.7")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
}
