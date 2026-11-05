package br.eng.mdsolucoes.nexocampo.rede

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Um aparelho como o sensor do local conhece: com MAC e marca pelo registro IEEE, inclusive os que saíram da rede. */
data class AparelhoSensor(
    val ip: String?, val mac: String?, val apelido: String?, val nome: String?, val marca: String?, val tipo: String?,
    val modelo: String?, val status: String?, val cftv: Boolean,
)

data class Ocorrencia(val tipo: String, val detalhes: String, val gravidade: String, val dataHora: String) {
    val queda: Boolean get() = tipo.startsWith("Queda") || (tipo == "Conectividade Central" && gravidade == "Crítica")
    /** "2026-11-05 18:02:11" (hora do sensor) -> "05/11 18:02". */
    val quando: String get() = if (dataHora.length >= 16) "${dataHora.substring(8, 10)}/${dataHora.substring(5, 7)} ${dataHora.substring(11, 16)}" else dataHora
}

data class DadosSensor(
    val ip: String, val nome: String?, val mac: String?, val versao: String?, val rede: String?, val gateway: String?,
    val aparelhos: List<AparelhoSensor>, val ocorrencias: List<Ocorrencia>, val quedas: Int, val dias: Int, val lidoEm: Long,
)

/**
 * O painel local do sensor NexoPulse (porta 10000), lido pelo app na rede do local, sem internet.
 * Entra como o navegador (usuário e senha do painel) e lê /api/campo; num sensor que ainda não tem
 * essa rota, junta /api/dispositivos e /api/eventos.
 */
class SensorLocal(private val ip: String, private val porta: Int = 10000) {

    class Falha(mensagem: String, val senhaErrada: Boolean = false) : Exception(mensagem)

    private var cookie: String? = null
    private fun url(caminho: String) = URL("http://$ip:$porta$caminho")

    suspend fun entrar(usuario: String, senha: String) = withContext(Dispatchers.IO) {
        val c = url("/login").openConnection() as HttpURLConnection
        c.instanceFollowRedirects = false
        c.connectTimeout = 5000; c.readTimeout = 10000
        c.requestMethod = "POST"; c.doOutput = true
        c.setRequestProperty("Content-Type", "application/x-www-form-urlencoded")
        val corpo = "usuario=${URLEncoder.encode(usuario, "UTF-8")}&senha=${URLEncoder.encode(senha, "UTF-8")}&proximo=%2F"
        try {
            c.outputStream.use { it.write(corpo.toByteArray()) }
            val codigo = c.responseCode
            val destino = c.getHeaderField("Location") ?: ""
            val sessao = c.headerFields["Set-Cookie"].orEmpty().mapNotNull { it.substringBefore(';').takeIf { s -> s.contains('=') } }
            c.disconnect()
            when {
                codigo == 302 && !destino.contains("/login") && sessao.isNotEmpty() -> cookie = sessao.joinToString("; ")
                codigo == 401 -> throw Falha("Usuário ou senha do painel local incorretos (ou muitas tentativas: espere 5 min).", senhaErrada = true)
                else -> throw Falha("O painel do sensor respondeu $codigo.")
            }
        } catch (e: Falha) {
            throw e
        } catch (e: Exception) {
            throw Falha("Não consegui falar com o sensor em $ip:$porta. Confira se o celular está na mesma rede.")
        }
    }

    private fun obter(caminho: String): Pair<Int, String> {
        val c = url(caminho).openConnection() as HttpURLConnection
        c.connectTimeout = 5000; c.readTimeout = 20000
        cookie?.let { c.setRequestProperty("Cookie", it) }
        val codigo = c.responseCode
        val corpo = runCatching { (if (codigo >= 400) c.errorStream else c.inputStream)?.bufferedReader()?.readText() }.getOrNull() ?: ""
        c.disconnect()
        return codigo to corpo
    }

    suspend fun ler(dias: Int = 7): DadosSensor = withContext(Dispatchers.IO) {
        try {
            val (codigo, corpo) = obter("/api/campo?dias=$dias")
            when (codigo) {
                200 -> interpretar(JSONObject(corpo))
                401 -> throw Falha("A sessão no painel do sensor expirou. Conecte de novo.", senhaErrada = true)
                404 -> lerVersaoAntiga(dias)          // sensor ainda sem a rota do app (chega na atualização automática)
                else -> throw Falha("O painel do sensor respondeu $codigo.")
            }
        } catch (e: Falha) {
            throw e
        } catch (e: Exception) {
            throw Falha("Não consegui ler o sensor em $ip: ${e.message}")
        }
    }

    private fun aparelhos(lista: JSONArray?): List<AparelhoSensor> =
        if (lista == null) emptyList() else (0 until lista.length()).map { i ->
            val d = lista.getJSONObject(i)
            fun txt(o: JSONObject?, k: String) = o?.let { if (it.isNull(k)) null else it.optString(k).takeIf { s -> s.isNotBlank() } }
            AparelhoSensor(
                ip = txt(d, "ip"), mac = txt(d, "mac"), apelido = txt(d, "apelido"), nome = txt(d.optJSONObject("nome"), "valor"),
                marca = txt(d.optJSONObject("marca"), "valor"), tipo = txt(d, "tipo"), modelo = txt(d, "modelo"),
                status = txt(d, "status"), cftv = !d.isNull("cftv") && d.has("cftv"),
            )
        }

    private fun ocorrencias(lista: JSONArray?): List<Ocorrencia> =
        if (lista == null) emptyList() else (0 until lista.length()).map { i ->
            val e = lista.getJSONObject(i)
            Ocorrencia(e.optString("tipo"), e.optString("detalhes"), e.optString("gravidade"), e.optString("data_hora"))
        }

    private fun interpretar(o: JSONObject): DadosSensor {
        val s = o.optJSONObject("sensor") ?: JSONObject()
        val oc = ocorrencias(o.optJSONArray("ocorrencias"))
        return DadosSensor(ip, s.optString("nome_local").ifBlank { null }, s.optString("mac_id").ifBlank { null },
            s.optString("agent_version").ifBlank { null }, s.optString("rede_cidr").ifBlank { null }, s.optString("ip_gateway").ifBlank { null },
            aparelhos(o.optJSONArray("dispositivos")), oc, o.optInt("quedas", oc.count { it.queda }), o.optInt("dias", 7), System.currentTimeMillis())
    }

    private fun lerVersaoAntiga(dias: Int): DadosSensor {
        val (c1, disp) = obter("/api/dispositivos")
        if (c1 != 200) throw Falha("O painel do sensor respondeu $c1.")
        val de = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date(System.currentTimeMillis() - dias * 86_400_000L))
        val (c2, ev) = obter("/api/eventos?limite=500&de=$de")
        val tipos = setOf("Queda de Alvo", "Queda de Alvo Local", "Queda de Energia", "Alvo Restaurado", "Energia Restaurada", "Conectividade Central")
        val oc = if (c2 == 200) ocorrencias(JSONObject(ev).optJSONArray("eventos")).filter { it.tipo in tipos || it.gravidade == "Crítica" } else emptyList()
        return DadosSensor(ip, null, null, null, null, null, aparelhos(JSONObject(disp).optJSONArray("dispositivos")), oc.take(100),
            oc.count { it.queda }, dias, System.currentTimeMillis())
    }

    companion object {
        /** Tipo do sensor para os tipos do app (o mesmo ícone e cor na lista). */
        fun tipoDoApp(a: AparelhoSensor): String? {
            val t = (a.tipo ?: "").lowercase()
            return when {
                a.cftv || listOf("câmera", "camera", "nvr", "dvr", "cftv", "gravador").any { it in t } -> "Câmera / DVR / NVR"
                "sensor" in t && "nexopulse" in t -> "Sensor NexoPulse"
                listOf("roteador", "router", "gateway", "onu", "modem").any { it in t } -> "Roteador"
                listOf("switch", "rádio", "radio", "access point", "ponto de acesso").any { it in t } -> "Rádio / ponto de acesso"
                "impressora" in t || "printer" in t -> "Impressora"
                listOf("inversor", "datalogger", "solar").any { it in t } -> "Inversor / datalogger solar"
                listOf("computador", "notebook", "servidor").any { it in t } -> "Computador"
                listOf("clp", "plc", "modbus", "medidor", "relé", "rele").any { it in t } -> "Equipamento industrial (Modbus)"
                else -> null
            }
        }
    }
}
