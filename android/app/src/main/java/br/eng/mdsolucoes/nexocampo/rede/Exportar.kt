package br.eng.mdsolucoes.nexocampo.rede

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Typeface
import android.graphics.pdf.PdfDocument
import android.net.Uri
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.core.content.res.ResourcesCompat
import androidx.core.graphics.drawable.toBitmap
import androidx.exifinterface.media.ExifInterface
import br.eng.mdsolucoes.nexocampo.R
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

object Exportar {

    private fun dataHora(ms: Long) = SimpleDateFormat("dd/MM/yyyy HH:mm", Locale("pt", "BR")).format(Date(ms))
    private fun nomeArquivo(l: Levantamento, ext: String): String {
        val base = l.local.ifBlank { "levantamento" }.lowercase()
            .replace(Regex("[^a-z0-9]+"), "-").trim('-').ifBlank { "levantamento" }
        return "nexocampo-$base-${SimpleDateFormat("yyyyMMdd-HHmm", Locale.US).format(Date(l.quando))}.$ext"
    }

    private fun pasta(contexto: Context) = File(contexto.cacheDir, "levantamentos").apply { mkdirs() }

    fun json(contexto: Context, l: Levantamento): File {
        val arquivo = File(pasta(contexto), nomeArquivo(l, "json"))
        arquivo.writeText(LevantamentoJson.paraJson(l).toString(2))
        return arquivo
    }

    // ------------------------------------------------------------------ PDF
    private class Pagina(val contexto: Context, val doc: PdfDocument) {
        val largura = 595f; val altura = 842f; val margem = 40f
        var numero = 0
        var pagina: PdfDocument.Page? = null
        var y = 0f
        private val inter = ResourcesCompat.getFont(contexto, R.font.inter_regular) ?: Typeface.DEFAULT
        private val interNegrito = ResourcesCompat.getFont(contexto, R.font.inter_bold) ?: Typeface.DEFAULT_BOLD
        private val mono = ResourcesCompat.getFont(contexto, R.font.mono_medium) ?: Typeface.MONOSPACE
        val texto = Paint().apply { color = Color.rgb(20, 43, 69); textSize = 9.5f; isAntiAlias = true; typeface = inter }
        val suave = Paint(texto).apply { color = Color.rgb(76, 101, 128); textSize = 8.5f }
        val numeros = Paint(texto).apply { typeface = mono; textSize = 9f }
        val negrito = Paint(texto).apply { typeface = interNegrito }
        val titulo = Paint(negrito).apply { color = Color.rgb(11, 27, 50); textSize = 14f }
        val linha = Paint().apply { color = Color.rgb(213, 224, 234); strokeWidth = 0.8f }
        val acento = Paint().apply { color = Color.rgb(8, 171, 208) }

        fun nova() {
            pagina?.let { rodape(it); doc.finishPage(it) }
            numero++
            pagina = doc.startPage(PdfDocument.PageInfo.Builder(595, 842, numero).create())
            y = margem
        }
        private fun rodape(p: PdfDocument.Page) {
            p.canvas.drawText("NexoCampo · MD Soluções Tecnológicas", margem, altura - 22f, suave)
            p.canvas.drawText("Página $numero", largura - margem - suave.measureText("Página $numero"), altura - 22f, suave)
        }
        val canvas get() = pagina!!.canvas
        fun espaco(precisa: Float) { if (pagina == null || y + precisa > altura - 50f) nova() }
        fun secao(nome: String) {
            espaco(46f); y += 18f
            canvas.drawRect(margem, y - 10f, margem + 3f, y + 2f, acento)
            canvas.drawText(nome, margem + 9f, y, titulo); y += 8f
        }
        fun par(rotulo: String, valor: String?) {
            espaco(14f); y += 13f
            canvas.drawText(rotulo, margem, y, suave)
            canvas.drawText(valor ?: "—", margem + 140f, y, numeros)
        }
        fun paragrafo(t: String, paint: Paint = texto) {
            for (l in quebrar(t, paint, largura - 2 * margem)) { espaco(13f); y += 12f; canvas.drawText(l, margem, y, paint) }
        }
        fun tabela(cabecalho: List<String>, pesos: List<Float>, linhas: List<List<String>>) {
            val total = largura - 2 * margem
            val larguras = pesos.map { it / pesos.sum() * total }
            fun desenhar(celulas: List<String>, paint: Paint) {
                val quebradas = celulas.mapIndexed { i, c -> quebrar(c, paint, larguras[i] - 6f) }
                val h = quebradas.maxOf { it.size } * 11f + 6f
                espaco(h)
                var x = margem
                quebradas.forEachIndexed { i, ls ->
                    ls.forEachIndexed { j, l -> canvas.drawText(l, x, y + 11f + j * 11f, paint) }
                    x += larguras[i]
                }
                y += h
                canvas.drawLine(margem, y, largura - margem, y, linha)
            }
            y += 4f
            desenhar(cabecalho, suave)
            linhas.forEach { desenhar(it, texto) }
        }
        fun fechar() { pagina?.let { rodape(it); doc.finishPage(it) } }

        fun quebrar(t: String, paint: Paint, max: Float): List<String> {
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

    /** Foto reduzida e na posição certa (o celular grava a rotação no EXIF). */
    private fun carregarFoto(arquivo: File, maximo: Int = 900): Bitmap? = runCatching {
        val limites = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(arquivo.path, limites)
        var amostra = 1
        while (limites.outWidth / amostra > maximo * 2 || limites.outHeight / amostra > maximo * 2) amostra *= 2
        val bmp = BitmapFactory.decodeFile(arquivo.path, BitmapFactory.Options().apply { inSampleSize = amostra }) ?: return null
        val graus = when (ExifInterface(arquivo.path).getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)) {
            ExifInterface.ORIENTATION_ROTATE_90 -> 90f
            ExifInterface.ORIENTATION_ROTATE_180 -> 180f
            ExifInterface.ORIENTATION_ROTATE_270 -> 270f
            else -> 0f
        }
        if (graus == 0f) bmp else Bitmap.createBitmap(bmp, 0, 0, bmp.width, bmp.height, Matrix().apply { postRotate(graus) }, true)
    }.getOrNull()

    fun pdf(contexto: Context, l: Levantamento): File {
        val doc = PdfDocument()
        val p = Pagina(contexto, doc)
        p.nova()
        // Faixa do topo com a marca.
        p.canvas.drawRect(0f, 0f, p.largura, 92f, Paint().apply { color = Color.rgb(11, 27, 50) })
        ContextCompat.getDrawable(contexto, R.drawable.logo_nexocampo)?.let { d ->
            val h = 34f; val w = h * d.intrinsicWidth / d.intrinsicHeight
            p.canvas.drawBitmap(d.toBitmap((w * 3).toInt(), (h * 3).toInt()), null, RectF(p.margem, 22f, p.margem + w, 22f + h), Paint(Paint.FILTER_BITMAP_FLAG))
        }
        val claro = Paint(p.suave).apply { color = Color.rgb(189, 204, 220) }
        p.canvas.drawText("Levantamento de rede em campo", p.margem, 76f, claro)
        p.y = 120f
        p.canvas.drawText(l.local.ifBlank { "Local não informado" }, p.margem, p.y, Paint(p.titulo).apply { textSize = 20f })
        p.y += 6f
        p.paragrafo(listOfNotNull(dataHora(l.quando), l.tecnico.ifBlank { null }?.let { "Técnico: $it" }).joinToString(" · "), p.suave)
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

        if (l.ambientes.isNotEmpty()) {
            p.secao("Sinal por ambiente")
            p.tabela(listOf("Ambiente", "Sinal", "Avaliação", "Rede / canal"), listOf(2.4f, 1.2f, 1.2f, 2.4f),
                l.ambientes.map { a -> listOf(a.nome, a.sinalDbm?.let { "$it dBm" } ?: "—", qualidadeSinal(a.sinalDbm),
                    listOfNotNull(a.ssid, a.canal?.let { "canal $it" }).joinToString(" · ").ifBlank { "—" }) })
        }

        if (l.pings.isNotEmpty() || l.velocidade != null || l.dns.isNotEmpty() || l.pingContinuo != null) {
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
            l.pingContinuo?.let { c ->
                p.y += 6f
                p.paragrafo("Ping contínuo até ${c.nome} (${c.alvo}), ${c.enviados} s a partir de ${dataHora(c.inicio)}:", p.negrito)
                p.par("Perda", "${c.perdaPct}% (${c.perdidos} de ${c.enviados})")
                p.par("Média / máxima", listOfNotNull(c.mediaMs?.let { "%.0f ms".format(it) }, c.maxMs?.let { "%.0f ms".format(it) }).joinToString(" / ").ifBlank { null })
                p.par("Oscilação", c.jitterMs?.let { "%.1f ms".format(it) })
            }
        }

        if (l.wifi.isNotEmpty()) {
            p.secao("Wi-Fi ao redor")
            p.paragrafo("${l.wifi.size} redes vistas.", p.suave)
            l.melhorCanal24?.let { (c, motivo) -> p.paragrafo("Canal menos disputado em 2,4 GHz: $c ($motivo).") }
            p.tabela(listOf("Rede", "Sinal", "Faixa / canal", "Segurança"), listOf(3f, 1.4f, 1.4f, 1.4f),
                l.wifi.take(25).map { listOf((if (it.conectada) "● " else "") + it.ssid, "${it.sinalDbm} dBm", "${it.faixa} · ${it.canal}", it.seguranca) })
        }

        l.sensor?.let { s ->
            p.secao("Sensor NexoPulse do local")
            p.par("Sensor", listOfNotNull(s.nome, s.ip).joinToString(" · "))
            p.par("Versão / MAC", listOfNotNull(s.versao, s.mac).joinToString(" · ").ifBlank { null })
            p.par("Aparelhos que ele conhece", "${s.aparelhos.size} (${s.aparelhos.count { it.status == "offline" }} fora da rede agora)")
            p.par("Quedas em ${s.dias} dias", "${s.quedas}")
            if (s.ocorrencias.isNotEmpty()) p.tabela(listOf("Quando", "Ocorrência", "Detalhe"), listOf(1.5f, 1.6f, 3.6f),
                s.ocorrencias.take(25).map { listOf(it.quando, it.tipo, it.detalhes) })
        }

        if (l.dispositivos.isNotEmpty()) {
            p.secao("Aparelhos na rede")
            val porTipo = l.dispositivos.groupingBy { it.tipo }.eachCount().entries.sortedByDescending { it.value }
            p.paragrafo("${l.dispositivos.size} aparelhos: " + porTipo.joinToString(", ") { "${it.value} ${it.key.lowercase()}" } + ".", p.suave)
            p.tabela(listOf("IP", "Tipo", "Nome / marca / MAC", "Portas abertas"), listOf(1.3f, 1.8f, 2.8f, 1.4f),
                l.dispositivos.map { d ->
                    listOf(d.ip + if (d.foraDaRede) " (fora da rede)" else "", d.tipo,
                        listOfNotNull(d.nome, d.fabricante, d.modelo, d.mac).distinct().joinToString(" · ").ifBlank { "—" },
                        d.portas.joinToString(", ").ifBlank { "—" })
                })
        }

        if (l.fotos.isNotEmpty()) {
            p.secao("Fotos")
            val colunas = 2
            val w = (p.largura - 2 * p.margem - 12f) / colunas
            l.fotos.chunked(colunas).forEach { linha ->
                val imagens = linha.map { f -> f to carregarFoto(Visitas.foto(contexto, l.id, f.arquivo)) }
                val alturas = imagens.map { (_, b) -> if (b == null) 0f else w * b.height / b.width }
                val h = (alturas.maxOrNull() ?: 0f).coerceAtMost(260f) + 20f
                p.espaco(h + 8f); p.y += 8f
                imagens.forEachIndexed { i, (f, b) ->
                    val x = p.margem + i * (w + 12f)
                    if (b != null) {
                        val altura = (w * b.height / b.width).coerceAtMost(260f)
                        val largura = altura * b.width / b.height
                        p.canvas.drawBitmap(b, null, RectF(x, p.y, x + largura, p.y + altura), Paint(Paint.FILTER_BITMAP_FLAG))
                        b.recycle()
                    }
                    if (f.legenda.isNotBlank()) p.canvas.drawText(f.legenda.take(60), x, p.y + h - 6f, p.suave)
                }
                p.y += h
            }
        }

        p.secao("Como foi medido")
        p.paragrafo("Medido pelo celular do técnico com o NexoCampo ${l.versaoApp}. O Android não deixa um app ler o MAC dos outros " +
            "aparelhos, então marca e tipo são inferidos por portas abertas, ONVIF, UPnP, mDNS e pela página web de cada aparelho" +
            (if (l.sensor != null) "; MAC, marca e os aparelhos fora da rede vêm do sensor NexoPulse do local. " else ". ") +
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
