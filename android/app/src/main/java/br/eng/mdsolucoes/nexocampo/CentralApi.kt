package br.eng.mdsolucoes.nexocampo

import android.content.Context
import android.os.Build
import br.eng.mdsolucoes.nexocampo.rede.Levantamento
import br.eng.mdsolucoes.nexocampo.rede.LevantamentoJson
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.DataOutputStream
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID

/** Ligação do app com a Central (NexoHub): entrar com o usuário da MD, listar empresas e enviar a visita. */
class CentralApi(contexto: Context) {

    data class Empresa(val id: Int, val nome: String, val locais: List<Pair<String, String>>, val chamados: List<Pair<Int, String>>)

    sealed class Entrada {
        data class Ok(val nome: String) : Entrada()
        data class PrecisaCodigo(val mensagem: String) : Entrada()
        data class Erro(val mensagem: String) : Entrada()
    }

    class Falha(mensagem: String, val sairDaConta: Boolean = false) : Exception(mensagem)

    private val prefs = contexto.getSharedPreferences("central", Context.MODE_PRIVATE)
    val token: String? get() = prefs.getString("token", null)
    val nome: String? get() = prefs.getString("nome", null)
    fun numeroEnviado(idVisita: String): Int? = prefs.getInt("enviada:$idVisita", 0).takeIf { it > 0 }

    private fun abrir(caminho: String, metodo: String = "GET"): HttpURLConnection =
        (URL(Atualizador.CENTRAL + caminho).openConnection() as HttpURLConnection).apply {
            requestMethod = metodo; connectTimeout = 15000; readTimeout = 60000
            token?.let { setRequestProperty("Authorization", "Bearer $it") }
        }

    private fun resposta(c: HttpURLConnection): Pair<Int, JSONObject> {
        val codigo = c.responseCode
        val corpo = runCatching { (if (codigo >= 400) c.errorStream else c.inputStream)?.bufferedReader()?.readText() }.getOrNull()
        c.disconnect()
        return codigo to (runCatching { JSONObject(corpo ?: "{}") }.getOrNull() ?: JSONObject())
    }

    suspend fun entrar(usuario: String, senha: String, codigo: String?): Entrada = withContext(Dispatchers.IO) {
        try {
            val c = abrir("/api/v2/campo/entrar", "POST")
            c.doOutput = true
            c.setRequestProperty("Content-Type", "application/json")
            val corpo = JSONObject().put("usuario", usuario.trim()).put("senha", senha).put("codigo", codigo ?: "")
                .put("aparelho", "${Build.MANUFACTURER} ${Build.MODEL}".trim())
            c.outputStream.use { it.write(corpo.toString().toByteArray()) }
            val (status, o) = resposta(c)
            when {
                status == 200 -> {
                    prefs.edit().putString("token", o.getString("token")).putString("nome", o.optString("nome")).apply()
                    Entrada.Ok(o.optString("nome"))
                }
                o.optBoolean("precisa_codigo") -> Entrada.PrecisaCodigo(o.optString("error", "Digite o código do autenticador."))
                else -> Entrada.Erro(o.optString("error", "A Central respondeu $status."))
            }
        } catch (e: Exception) {
            Entrada.Erro("Sem conexão com a Central. Confira a internet do celular.")
        }
    }

    suspend fun sair() = withContext(Dispatchers.IO) {
        runCatching { resposta(abrir("/api/v2/campo/sair", "POST").apply { doOutput = true; outputStream.close() }) }
        prefs.edit().remove("token").remove("nome").apply()
    }

    suspend fun empresas(): List<Empresa> = withContext(Dispatchers.IO) {
        val (status, o) = try { resposta(abrir("/api/v2/campo/locais")) } catch (e: Exception) {
            throw Falha("Sem conexão com a Central.")
        }
        if (status == 401) { prefs.edit().remove("token").apply(); throw Falha("Entre de novo na Central.", sairDaConta = true) }
        if (status != 200) throw Falha(o.optString("error", "A Central respondeu $status."))
        val lista = o.getJSONArray("empresas")
        (0 until lista.length()).map { i ->
            val e = lista.getJSONObject(i)
            val locais = e.getJSONArray("locais"); val chamados = e.getJSONArray("chamados")
            Empresa(e.getInt("id"), e.getString("nome"),
                (0 until locais.length()).map { locais.getJSONObject(it).let { l -> l.getString("mac_id") to (l.optString("nome_local").ifBlank { l.getString("mac_id") }) } },
                (0 until chamados.length()).map { chamados.getJSONObject(it).let { c -> c.getInt("numero") to c.optString("titulo") } })
        }
    }

    /** Envia (ou reenvia) a visita com o PDF. Devolve o número dela na Central. */
    suspend fun enviar(l: Levantamento, pdf: File, empresaId: Int, sensorMac: String?, chamado: Int?): Int = withContext(Dispatchers.IO) {
        val fronteira = "----nexocampo" + UUID.randomUUID().toString().replace("-", "")
        val c = abrir("/api/v2/campo/visitas", "POST")
        c.doOutput = true
        c.setRequestProperty("Content-Type", "multipart/form-data; boundary=$fronteira")
        c.setChunkedStreamingMode(64 * 1024)
        try {
            DataOutputStream(c.outputStream).use { s ->
                fun campo(nome: String, valor: String) {
                    s.writeBytes("--$fronteira\r\nContent-Disposition: form-data; name=\"$nome\"\r\n\r\n")
                    s.write(valor.toByteArray()); s.writeBytes("\r\n")
                }
                campo("dados", LevantamentoJson.paraJson(l).toString())
                campo("empresa_id", empresaId.toString())
                sensorMac?.let { campo("sensor_mac", it) }
                chamado?.let { campo("chamado", it.toString()) }
                s.writeBytes("--$fronteira\r\nContent-Disposition: form-data; name=\"pdf\"; filename=\"${pdf.name}\"\r\nContent-Type: application/pdf\r\n\r\n")
                pdf.inputStream().use { it.copyTo(s) }
                s.writeBytes("\r\n--$fronteira--\r\n")
            }
        } catch (e: Exception) {
            throw Falha("Sem conexão com a Central. A visita continua salva no celular; envie de novo depois.")
        }
        val (status, o) = resposta(c)
        if (status == 401) { prefs.edit().remove("token").apply(); throw Falha("Entre de novo na Central.", sairDaConta = true) }
        if (status !in 200..201) throw Falha(o.optString("error", "A Central respondeu $status."))
        val numero = o.getInt("numero")
        prefs.edit().putInt("enviada:${l.id}", numero).apply()
        numero
    }
}
