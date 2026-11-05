package br.eng.mdsolucoes.nexocampo.rede

import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Typeface
import android.graphics.pdf.PdfDocument
import android.net.Uri
import androidx.core.content.FileProvider
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Tudo o que foi medido numa visita, para exportar. */
data class Levantamento(
    val local: String,
    val tecnico: String,
    val observacoes: String,
    val quando: Long,
    val versaoApp: String,
    val rede: RedeAtual?,
    val wifi: List<RedeWifi>,
    val melhorCanal24: Pair<Int, String>?,
    val pings: List<ResultadoPing>,
    val dns: List<ResultadoDns>,
    val velocidade: ResultadoVelocidade?,
    val dispositivos: List<Dispositivo>,
)

object Exportar {

    private fun dataHora(ms: Long) = SimpleDateFormat("dd/MM/yyyy HH:mm", Locale("pt", "BR")).format(Date(ms))
    private fun nomeArquivo(l: Levantamento, ext: String): String {
        val base = l.local.ifBlank { "levantamento" }.lowercase()
            .replace(Regex("[^a-z0-9]+"), "-").trim('-').ifBlank { "levantamento" }
        return "nexocampo-$base-${SimpleDateFormat("yyyyMMdd-HHmm", Locale.US).format(Date(l.quando))}.$ext"
    }

    private fun pasta(contexto: Context) = File(contexto.cacheDir, "levantamentos").apply { mkdirs() }

    // ------------------------------------------------------------------ JSON (formato próximo do levantamento do sensor)
    fun json(contexto: Context, l: Levantamento): File {
        val o = JSONObject()
        o.put("formato", "nexocampo.levantamento-rede"); o.put("versao_formato", 1)
        o.put("gerado_por", "NexoCampo ${l.versaoApp}")
        o.put("quando", SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", Locale.US).format(Date(l.quando)))
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
                put("ssid", w.ssid); put("bssid", w.bssid); put("sinal_dbm", w.sinalDbm); put("faixa", w.faixa)
                put("canal", w.canal); put("largura_mhz", w.larguraMhz); put("seguranca", w.seguranca); put("conectada", w.conectada)
            }
        }))
        l.melhorCanal24?.let { o.put("melhor_canal_2_4ghz", JSONObject().put("canal", it.first).put("motivo", it.second)) }
        o.put("ping", JSONArray(l.pings.map { p ->
            JSONObject().apply {
                put("nome", p.nome); put("alvo", p.alvo); put("enviados", p.enviados); put("recebidos", p.recebidos)
                put("perda_pct", p.perdaPct); put("min_ms", p.minMs); put("media_ms", p.mediaMs); put("max_ms", p.maxMs); put("erro", p.erro)
            }
        }))
        o.put("dns", JSONArray(l.dns.map { d -> JSONObject().put("nome", d.nome).put("ms", d.ms).put("enderecos", JSONArray(d.enderecos)).put("erro", d.erro) }))
        l.velocidade?.let { v ->
            o.put("velocidade", JSONObject().put("download_mbps", v.downloadMbps).put("upload_mbps", v.uploadMbps)
                .put("latencia_ms", v.latenciaMs).put("erro", v.erro))
        }
        o.put("dispositivos", JSONArray(l.dispositivos.map { d ->
            JSONObject().apply {
                put("ip", d.ip); put("tipo", d.tipo); put("nome", d.nome); put("fabricante", d.fabricante); put("modelo", d.modelo)
                put("mac", d.mac); put("portas", JSONArray(d.portas)); put("servicos", JSONArray(d.servicos))
                put("encontrado_por", JSONArray(d.origens)); put("este_celular", d.esteCelular)
            }
        }))
        val arquivo = File(pasta(contexto), nomeArquivo(l, "json"))
        arquivo.writeText(o.toString(2))
        return arquivo
    }

    // ------------------------------------------------------------------ PDF
    private class Pagina(val doc: PdfDocument) {
        val largura = 595f; val altura = 842f; val margem = 40f
        var numero = 0
        var pagina: PdfDocument.Page? = null
        var y = 0f
        val texto = Paint().apply { color = Color.rgb(20, 43, 69); textSize = 9.5f; isAntiAlias = true }
        val suave = Paint(texto).apply { color = Color.rgb(76, 101, 128); textSize = 8.5f }
        val negrito = Paint(texto).apply { typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD) }
        val titulo = Paint(negrito).apply { color = Color.rgb(11, 52, 96); textSize = 15f }
        val linha = Paint().apply { color = Color.rgb(213, 224, 234); strokeWidth = 0.8f }

        fun nova() {
            pagina?.let { rodape(it); doc.finishPage(it) }
            numero++
            pagina = doc.startPage(PdfDocument.PageInfo.Builder(595, 842, numero).create())
            y = margem
        }
        private fun rodape(p: PdfDocument.Page) {
            p.canvas.drawText("NexoCampo · MD Soluções Tecnológicas", margem, altura - 22f, suave)
            p.canvas.drawText("Página $numero", largura - margem - 40f, altura - 22f, suave)
        }
        fun espaco(precisa: Float) { if (pagina == null || y + precisa > altura - 50f) nova() }
        fun secao(nome: String) { espaco(40f); y += 14f; pagina!!.canvas.drawText(nome, margem, y, titulo); y += 10f }
        fun par(rotulo: String, valor: String?) {
            espaco(14f); y += 13f
            pagina!!.canvas.drawText(rotulo, margem, y, suave)
            pagina!!.canvas.drawText(valor ?: "—", margem + 140f, y, texto)
        }
        fun paragrafo(t: String, paint: Paint = texto) {
            for (l in quebrar(t, paint, largura - 2 * margem)) { espaco(13f); y += 12f; pagina!!.canvas.drawText(l, margem, y, paint) }
        }
        /** Tabela simples: larguras relativas das colunas; o texto de cada célula quebra na largura dela. */
        fun tabela(cabecalho: List<String>, pesos: List<Float>, linhas: List<List<String>>) {
            val total = largura - 2 * margem
            val larguras = pesos.map { it / pesos.sum() * total }
            fun desenhar(celulas: List<String>, paint: Paint) {
                val quebradas = celulas.mapIndexed { i, c -> quebrar(c, paint, larguras[i] - 6f) }
                val h = quebradas.maxOf { it.size } * 11f + 6f
                espaco(h)
                var x = margem
                quebradas.forEachIndexed { i, ls ->
                    ls.forEachIndexed { j, l -> pagina!!.canvas.drawText(l, x, y + 11f + j * 11f, paint) }
                    x += larguras[i]
                }
                y += h
                pagina!!.canvas.drawLine(margem, y, largura - margem, y, linha)
            }
            y += 4f
            desenhar(cabecalho, suave)
            linhas.forEach { desenhar(it, texto) }
        }
        fun fechar() { pagina?.let { rodape(it); doc.finishPage(it) } }

        private fun quebrar(t: String, paint: Paint, max: Float): List<String> {
            val saida = mutableListOf<String>()
            for (paragrafo in t.split('\n')) {
                var atual = ""
                for (palavra in paragrafo.split(' ')) {
                    val tentativa = if (atual.isEmpty()) palavra else "$atual $palavra"
                    if (paint.measureText(tentativa) <= max) atual = tentativa
                    else { if (atual.isNotEmpty()) saida.add(atual); atual = palavra }
                }
                saida.add(atual)
            }
            return saida
        }
    }

    fun pdf(contexto: Context, l: Levantamento): File {
        val doc = PdfDocument()
        val p = Pagina(doc)
        p.nova()
        p.y += 6f
        p.pagina!!.canvas.drawText("Levantamento de rede", p.margem, p.y + 14f, Paint(p.titulo).apply { textSize = 22f })
        p.y += 30f
        p.paragrafo(listOf(l.local.ifBlank { "Local não informado" }, dataHora(l.quando), l.tecnico.ifBlank { null })
            .filterNotNull().joinToString(" · "), p.suave)
        if (l.observacoes.isNotBlank()) { p.y += 4f; p.paragrafo(l.observacoes) }

        p.secao("Rede do celular")
        val r = l.rede
        if (r == null) p.paragrafo("Não lida.") else {
            p.par("Conexão", r.tipo)
            if (r.tipo == "Wi-Fi") {
                p.par("Wi-Fi", listOfNotNull(r.ssid, r.bssid).joinToString(" · ").ifBlank { null })
                p.par("Sinal", r.sinalDbm?.let { "$it dBm (${qualidadeSinal(it)})" })
                p.par("Canal", r.canal?.let { "$it · ${r.frequenciaMhz?.let { f -> faixaDe(f) } ?: ""}" })
                p.par("Velocidade do link", r.velocidadeMbps?.let { "$it Mbps" })
                p.par("Padrão / segurança", listOfNotNull(r.padrao, r.seguranca).joinToString(" · ").ifBlank { null })
            }
            p.par("IP do celular", r.ip?.let { "$it/${r.prefixo ?: "?"}" })
            p.par("Gateway", r.gateway)
            p.par("DNS", r.dns.joinToString(", ").ifBlank { null })
        }

        if (l.pings.isNotEmpty() || l.velocidade != null || l.dns.isNotEmpty()) {
            p.secao("Testes")
            l.velocidade?.let { v ->
                p.par("Download", v.downloadMbps?.let { "%.1f Mbps".format(it) } ?: v.erro)
                p.par("Upload", v.uploadMbps?.let { "%.1f Mbps".format(it) })
                p.par("Resposta (servidor)", v.latenciaMs?.let { "%.0f ms".format(it) })
            }
            if (l.pings.isNotEmpty()) p.tabela(listOf("Ping", "Alvo", "Perda", "Média", "Mín / máx"), listOf(2f, 2f, 1f, 1f, 1.4f),
                l.pings.map { listOf(it.nome, it.alvo, "${it.perdaPct}%", it.mediaMs?.let { m -> "%.0f ms".format(m) } ?: (it.erro ?: "—"),
                    if (it.minMs != null) "%.0f / %.0f ms".format(it.minMs, it.maxMs) else "—") })
            if (l.dns.isNotEmpty()) p.tabela(listOf("DNS", "Tempo", "Resposta"), listOf(2f, 1f, 3f),
                l.dns.map { listOf(it.nome, it.ms?.let { m -> "$m ms" } ?: "—", it.erro ?: it.enderecos.take(2).joinToString(", ")) })
        }

        if (l.wifi.isNotEmpty()) {
            p.secao("Wi-Fi ao redor")
            p.paragrafo("${l.wifi.size} redes vistas.", p.suave)
            l.melhorCanal24?.let { (c, motivo) -> p.paragrafo("Melhor canal em 2,4 GHz: $c ($motivo).") }
            p.tabela(listOf("Rede", "Sinal", "Faixa / canal", "Segurança"), listOf(3f, 1.4f, 1.4f, 1.4f),
                l.wifi.take(25).map { listOf((if (it.conectada) "● " else "") + it.ssid, "${it.sinalDbm} dBm", "${it.faixa} · ${it.canal}", it.seguranca) })
        }

        if (l.dispositivos.isNotEmpty()) {
            p.secao("Aparelhos na rede")
            val porTipo = l.dispositivos.groupingBy { it.tipo }.eachCount().entries.sortedByDescending { it.value }
            p.paragrafo("${l.dispositivos.size} aparelhos: " + porTipo.joinToString(", ") { "${it.value} ${it.key.lowercase()}" } + ".", p.suave)
            p.tabela(listOf("IP", "Tipo", "Nome / marca", "Portas abertas"), listOf(1.3f, 1.8f, 2.6f, 1.6f),
                l.dispositivos.map { d ->
                    listOf(d.ip, d.tipo, listOfNotNull(d.nome, d.fabricante, d.modelo).distinct().joinToString(" · ").ifBlank { "—" },
                        d.portas.joinToString(", ").ifBlank { "—" })
                })
        }
        p.secao("Como foi medido")
        p.paragrafo("Medido pelo celular do técnico com o NexoCampo ${l.versaoApp}. O Android não deixa um app ler o MAC dos outros " +
            "aparelhos, então marca e tipo são inferidos por portas abertas, ONVIF, UPnP, mDNS e pela página web de cada aparelho. " +
            "É o retrato do momento da visita; o monitoramento contínuo é feito pelo sensor NexoPulse.", p.suave)
        p.fechar()
        val arquivo = File(pasta(contexto), nomeArquivo(l, "pdf"))
        arquivo.outputStream().use { doc.writeTo(it) }
        doc.close()
        return arquivo
    }

    fun compartilhar(contexto: Context, arquivos: List<File>) {
        val uris = ArrayList<Uri>(arquivos.map { FileProvider.getUriForFile(contexto, "${contexto.packageName}.arquivos", it) })
        val intent = if (uris.size == 1) {
            Intent(Intent.ACTION_SEND).apply {
                type = if (arquivos[0].extension == "pdf") "application/pdf" else "application/json"
                putExtra(Intent.EXTRA_STREAM, uris[0])
            }
        } else {
            Intent(Intent.ACTION_SEND_MULTIPLE).apply { type = "*/*"; putParcelableArrayListExtra(Intent.EXTRA_STREAM, uris) }
        }
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        contexto.startActivity(Intent.createChooser(intent, "Enviar levantamento").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}
