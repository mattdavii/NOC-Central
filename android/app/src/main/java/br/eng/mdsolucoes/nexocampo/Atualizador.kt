package br.eng.mdsolucoes.nexocampo

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.content.FileProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * Atualização pelo próprio app (sem Play Store): pergunta à Central qual é a versão mais nova, baixa o APK
 * e abre a tela de instalação do Android. A confirmação final ("Instalar") é sempre do usuário.
 */
object Atualizador {

    const val CENTRAL = "https://nexopulse.up.railway.app"

    data class Versao(val nome: String, val codigo: Long, val tamanho: Long?, val url: String)

    fun versaoInstalada(contexto: Context): Long {
        val pm = contexto.packageManager
        val info = if (Build.VERSION.SDK_INT >= 33) pm.getPackageInfo(contexto.packageName, android.content.pm.PackageManager.PackageInfoFlags.of(0))
        else @Suppress("DEPRECATION") pm.getPackageInfo(contexto.packageName, 0)
        return if (Build.VERSION.SDK_INT >= 28) info.longVersionCode else @Suppress("DEPRECATION") info.versionCode.toLong()
    }

    /** A versão mais nova, se for maior que a instalada; null se já está em dia ou se não deu para consultar. */
    suspend fun verificar(contexto: Context): Versao? = withContext(Dispatchers.IO) {
        try {
            val c = URL("$CENTRAL/app/nexocampo/versao").openConnection() as HttpURLConnection
            c.connectTimeout = 8000; c.readTimeout = 8000
            if (c.responseCode != 200) return@withContext null
            val o = JSONObject(c.inputStream.bufferedReader().readText())
            c.disconnect()
            val v = Versao(o.getString("versao"), o.getLong("versao_codigo"), o.optLong("tamanho").takeIf { it > 0 }, o.getString("url"))
            if (v.codigo > versaoInstalada(contexto)) v else null
        } catch (e: Exception) {
            null
        }
    }

    suspend fun baixar(contexto: Context, v: Versao, progresso: (Float) -> Unit): File = withContext(Dispatchers.IO) {
        val pasta = File(contexto.cacheDir, "atualizacao").apply { mkdirs(); listFiles()?.forEach { it.delete() } }
        val destino = File(pasta, "nexocampo-${v.nome}.apk")
        val c = URL(v.url).openConnection() as HttpURLConnection
        c.connectTimeout = 15000; c.readTimeout = 30000
        if (c.responseCode != 200) throw IllegalStateException("a Central respondeu ${c.responseCode}")
        val total = (v.tamanho ?: c.contentLengthLong).takeIf { it > 0 }
        c.inputStream.use { entrada ->
            destino.outputStream().use { saida ->
                val buffer = ByteArray(64 * 1024)
                var lidos = 0L
                while (true) {
                    val n = entrada.read(buffer)
                    if (n < 0) break
                    saida.write(buffer, 0, n); lidos += n
                    if (total != null) progresso((lidos.toFloat() / total).coerceAtMost(1f))
                }
            }
        }
        c.disconnect()
        destino
    }

    /** O Android exige liberar "instalar apps desta origem" uma vez para o NexoCampo. */
    fun podeInstalar(contexto: Context) = contexto.packageManager.canRequestPackageInstalls()

    fun abrirPermissao(contexto: Context) {
        contexto.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${contexto.packageName}"))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    fun instalar(contexto: Context, apk: File) {
        val uri = FileProvider.getUriForFile(contexto, "${contexto.packageName}.arquivos", apk)
        contexto.startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}
