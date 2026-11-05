package br.eng.mdsolucoes.nexocampo.rede

import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Tudo o que foi medido numa visita: é o que fica salvo no celular, vai no PDF e no JSON. */
data class Levantamento(
    val id: String,
    val local: String,
    val tecnico: String,
    val observacoes: String,
    val criadaEm: Long,
    val quando: Long,
    val versaoApp: String,
    val rede: RedeAtual?,
    val wifi: List<RedeWifi>,
    val melhorCanal24: Pair<Int, String>?,
    val pings: List<ResultadoPing>,
    val dns: List<ResultadoDns>,
    val velocidade: ResultadoVelocidade?,
    val dispositivos: List<Dispositivo>,
    val ambientes: List<Ambiente> = emptyList(),
    val pingContinuo: PingContinuo? = null,
    val fotos: List<Foto> = emptyList(),
) {
    val vazio: Boolean
        get() = local.isBlank() && observacoes.isBlank() && wifi.isEmpty() && pings.isEmpty() && velocidade == null &&
            dispositivos.isEmpty() && ambientes.isEmpty() && pingContinuo == null && fotos.isEmpty()
}

/** JSON do levantamento (formato próximo do levantamento do sensor), nos dois sentidos. */
object LevantamentoJson {

    const val FORMATO = "nexocampo.levantamento-rede"
    const val VERSAO = 2

    private fun iso(ms: Long) = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", Locale.US).format(Date(ms))
    private fun JSONObject.str(nome: String): String? = if (isNull(nome) || !has(nome)) null else optString(nome)
    private fun JSONObject.int(nome: String): Int? = if (isNull(nome) || !has(nome)) null else optInt(nome)
    private fun JSONObject.dbl(nome: String): Double? = if (isNull(nome) || !has(nome)) null else optDouble(nome)
    private fun JSONObject.lng(nome: String): Long? = if (isNull(nome) || !has(nome)) null else optLong(nome)
    private fun JSONArray?.objetos(): List<JSONObject> = if (this == null) emptyList() else (0 until length()).map { getJSONObject(it) }
    private fun JSONArray?.textos(): List<String> = if (this == null) emptyList() else (0 until length()).map { getString(it) }
    private fun JSONArray?.inteiros(): List<Int> = if (this == null) emptyList() else (0 until length()).map { getInt(it) }
    private fun JSONArray?.numeros(): List<Double> = if (this == null) emptyList() else (0 until length()).map { getDouble(it) }

    fun paraJson(l: Levantamento): JSONObject {
        val o = JSONObject()
        o.put("formato", FORMATO); o.put("versao_formato", VERSAO); o.put("id", l.id)
        o.put("gerado_por", "NexoCampo ${l.versaoApp}")
        o.put("criada_em", iso(l.criadaEm)); o.put("criada_em_ms", l.criadaEm)
        o.put("quando", iso(l.quando)); o.put("quando_ms", l.quando)
        o.put("local", l.local); o.put("tecnico", l.tecnico); o.put("observacoes", l.observacoes)
        o.put("limitacoes", "Medido por um celular Android: sem MAC dos outros aparelhos (bloqueado pelo Android 10+); " +
            "marca e tipo são inferidos por portas, ONVIF, UPnP, mDNS e página web. Retrato do momento da visita.")
        l.rede?.let { r ->
            o.put("rede", JSONObject().apply {
                put("tipo", r.tipo); put("ssid", r.ssid); put("bssid", r.bssid); put("sinal_dbm", r.sinalDbm)
                put("frequencia_mhz", r.frequenciaMhz); put("canal", r.canal); put("velocidade_mbps", r.velocidadeMbps)
                put("padrao", r.padrao); put("seguranca", r.seguranca); put("ip", r.ip); put("prefixo", r.prefixo)
                put("gateway", r.gateway); put("dns", JSONArray(r.dns))
            })
        }
        o.put("wifi_ao_redor", JSONArray(l.wifi.map { w ->
            JSONObject().apply {
                put("ssid", w.ssid); put("bssid", w.bssid); put("sinal_dbm", w.sinalDbm); put("frequencia_mhz", w.frequenciaMhz)
                put("faixa", w.faixa); put("canal", w.canal); put("largura_mhz", w.larguraMhz); put("seguranca", w.seguranca)
                put("conectada", w.conectada)
            }
        }))
        l.melhorCanal24?.let { o.put("melhor_canal_2_4ghz", JSONObject().put("canal", it.first).put("motivo", it.second)) }
        o.put("ping", JSONArray(l.pings.map { p ->
            JSONObject().apply {
                put("nome", p.nome); put("alvo", p.alvo); put("enviados", p.enviados); put("recebidos", p.recebidos)
                put("perda_pct", p.perdaPct); put("min_ms", p.minMs); put("media_ms", p.mediaMs); put("max_ms", p.maxMs)
                put("erro", p.erro); put("tempos_ms", JSONArray(p.tempos))
            }
        }))
        o.put("dns", JSONArray(l.dns.map { d -> JSONObject().put("nome", d.nome).put("ms", d.ms).put("enderecos", JSONArray(d.enderecos)).put("erro", d.erro) }))
        l.velocidade?.let { v ->
            o.put("velocidade", JSONObject().put("download_mbps", v.downloadMbps).put("upload_mbps", v.uploadMbps)
                .put("latencia_ms", v.latenciaMs).put("erro", v.erro).put("amostras_download_mbps", JSONArray(v.amostrasDownload)))
        }
        o.put("dispositivos", JSONArray(l.dispositivos.map { d ->
            JSONObject().apply {
                put("ip", d.ip); put("tipo", d.tipo); put("nome", d.nome); put("fabricante", d.fabricante); put("modelo", d.modelo)
                put("mac", d.mac); put("portas", JSONArray(d.portas)); put("servicos", JSONArray(d.servicos))
                put("encontrado_por", JSONArray(d.origens)); put("este_celular", d.esteCelular)
            }
        }))
        o.put("ambientes", JSONArray(l.ambientes.map { a ->
            JSONObject().put("nome", a.nome).put("ssid", a.ssid).put("bssid", a.bssid).put("sinal_dbm", a.sinalDbm)
                .put("canal", a.canal).put("quando_ms", a.quando)
        }))
        l.pingContinuo?.let { p ->
            o.put("ping_continuo", JSONObject().put("nome", p.nome).put("alvo", p.alvo).put("inicio_ms", p.inicio)
                .put("perda_pct", p.perdaPct).put("media_ms", p.mediaMs).put("max_ms", p.maxMs).put("oscilacao_ms", p.jitterMs)
                .put("tempos_ms", JSONArray(p.tempos.map { it ?: JSONObject.NULL })))
        }
        o.put("fotos", JSONArray(l.fotos.map { JSONObject().put("arquivo", it.arquivo).put("legenda", it.legenda) }))
        return o
    }

    fun deJson(o: JSONObject, versaoApp: String): Levantamento {
        val r = o.optJSONObject("rede")?.let { j ->
            RedeAtual(
                tipo = j.str("tipo") ?: "?", ssid = j.str("ssid"), bssid = j.str("bssid"), sinalDbm = j.int("sinal_dbm"),
                frequenciaMhz = j.int("frequencia_mhz"), canal = j.int("canal"), velocidadeMbps = j.int("velocidade_mbps"),
                padrao = j.str("padrao"), seguranca = j.str("seguranca"), ip = j.str("ip"), prefixo = j.int("prefixo"),
                gateway = j.str("gateway"), dns = j.optJSONArray("dns").textos(),
            )
        }
        return Levantamento(
            id = o.getString("id"), local = o.optString("local"), tecnico = o.optString("tecnico"), observacoes = o.optString("observacoes"),
            criadaEm = o.lng("criada_em_ms") ?: o.optLong("quando_ms"), quando = o.optLong("quando_ms"), versaoApp = versaoApp, rede = r,
            wifi = o.optJSONArray("wifi_ao_redor").objetos().map { w ->
                RedeWifi(w.optString("ssid"), w.optString("bssid"), w.optInt("sinal_dbm"), w.optInt("frequencia_mhz"), w.optInt("canal"),
                    w.optInt("largura_mhz", 20), w.optString("seguranca"), w.optBoolean("conectada"))
            },
            melhorCanal24 = o.optJSONObject("melhor_canal_2_4ghz")?.let { it.getInt("canal") to it.optString("motivo") },
            pings = o.optJSONArray("ping").objetos().map { p ->
                ResultadoPing(p.optString("nome"), p.optString("alvo"), p.optInt("enviados"), p.optInt("recebidos"), p.dbl("min_ms"),
                    p.dbl("media_ms"), p.dbl("max_ms"), p.str("erro"), p.optJSONArray("tempos_ms").numeros())
            },
            dns = o.optJSONArray("dns").objetos().map { d -> ResultadoDns(d.optString("nome"), d.lng("ms"), d.optJSONArray("enderecos").textos(), d.str("erro")) },
            velocidade = o.optJSONObject("velocidade")?.let { v ->
                ResultadoVelocidade(v.dbl("download_mbps"), v.dbl("upload_mbps"), v.dbl("latencia_ms"), v.str("erro"),
                    v.optJSONArray("amostras_download_mbps").numeros())
            },
            dispositivos = o.optJSONArray("dispositivos").objetos().map { d ->
                Dispositivo(d.optString("ip"), d.optString("tipo"), d.str("nome"), d.str("fabricante"), d.str("modelo"),
                    d.optJSONArray("portas").inteiros(), d.optJSONArray("servicos").textos(), d.str("mac"),
                    d.optJSONArray("encontrado_por").textos(), d.optBoolean("este_celular"))
            },
            ambientes = o.optJSONArray("ambientes").objetos().map { a ->
                Ambiente(a.optString("nome"), a.str("ssid"), a.str("bssid"), a.int("sinal_dbm"), a.int("canal"), a.optLong("quando_ms"))
            },
            pingContinuo = o.optJSONObject("ping_continuo")?.let { p ->
                val t = p.optJSONArray("tempos_ms")
                PingContinuo(p.optString("nome"), p.optString("alvo"), p.optLong("inicio_ms"),
                    if (t == null) emptyList() else (0 until t.length()).map { if (t.isNull(it)) null else t.getDouble(it) })
            },
            fotos = o.optJSONArray("fotos").objetos().map { Foto(it.optString("arquivo"), it.optString("legenda")) },
        )
    }
}
