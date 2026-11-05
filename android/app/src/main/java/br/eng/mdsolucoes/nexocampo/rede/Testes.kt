package br.eng.mdsolucoes.nexocampo.rede

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.URL
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong

object Testes {

    private val RE_TEMPO = Regex("""time[=<]([\d.]+)\s*ms""")
    private val RE_RESUMO = Regex("""(\d+) packets transmitted, (\d+) (?:packets )?received""")

    /** Ping pelo programa do próprio Android (não precisa de root). */
    suspend fun ping(nome: String, alvo: String, vezes: Int = 5): ResultadoPing = withContext(Dispatchers.IO) {
        try {
            val processo = ProcessBuilder("/system/bin/ping", "-c", "$vezes", "-i", "0.3", "-W", "2", alvo)
                .redirectErrorStream(true).start()
            if (!processo.waitFor(20, TimeUnit.SECONDS)) processo.destroy()
            val saida = processo.inputStream.bufferedReader().readText()
            val tempos = RE_TEMPO.findAll(saida).map { it.groupValues[1].toDouble() }.toList()
            val resumo = RE_RESUMO.find(saida)
            val enviados = resumo?.groupValues?.get(1)?.toInt() ?: vezes
            val recebidos = resumo?.groupValues?.get(2)?.toInt() ?: tempos.size
            ResultadoPing(
                nome, alvo, enviados, recebidos, tempos.minOrNull(), tempos.takeIf { it.isNotEmpty() }?.average(), tempos.maxOrNull(),
                erro = if (resumo == null && tempos.isEmpty()) saida.lineSequence().firstOrNull { it.isNotBlank() } ?: "sem resposta" else null,
                tempos = tempos,
            )
        } catch (e: Exception) {
            ResultadoPing(nome, alvo, vezes, 0, null, null, null, e.message ?: "falhou")
        }
    }

    suspend fun dns(nome: String): ResultadoDns = withContext(Dispatchers.IO) {
        val inicio = System.nanoTime()
        try {
            val enderecos = InetAddress.getAllByName(nome).mapNotNull { it.hostAddress }
            ResultadoDns(nome, (System.nanoTime() - inicio) / 1_000_000, enderecos)
        } catch (e: Exception) {
            ResultadoDns(nome, null, emptyList(), "não resolveu")
        }
    }

    private const val SERVIDOR = "https://speed.cloudflare.com"

    /** Uma resposta de ping (para o ping contínuo); null se não respondeu em 1 s. */
    suspend fun pingUma(alvo: String): Double? = withContext(Dispatchers.IO) {
        try {
            val processo = ProcessBuilder("/system/bin/ping", "-c", "1", "-W", "1", alvo).redirectErrorStream(true).start()
            if (!processo.waitFor(3, TimeUnit.SECONDS)) { processo.destroy(); return@withContext null }
            RE_TEMPO.find(processo.inputStream.bufferedReader().readText())?.groupValues?.get(1)?.toDouble()
        } catch (e: Exception) { null }
    }

    /**
     * Velocidade pela Cloudflare: 4 conexões em paralelo, cerca de 8 s em cada sentido.
     * [aoVivo] recebe a velocidade do último meio segundo (para o número subir na tela durante o teste).
     */
    suspend fun velocidade(progresso: (String) -> Unit = {}, aoVivo: (Double) -> Unit = {}): ResultadoVelocidade = withContext(Dispatchers.IO) {
        try {
            progresso("Medindo a resposta…")
            val latencia = (1..5).mapNotNull { medirLatencia() }.sorted().let { if (it.isEmpty()) null else it[it.size / 2] }
            progresso("Medindo o download…")
            val amostras = mutableListOf<Double>()
            val down = medirDownload(8_000) { mbps -> amostras.add(mbps); aoVivo(mbps) }
            progresso("Medindo o upload…")
            val up = medirUpload(8_000, aoVivo)
            ResultadoVelocidade(down, up, latencia, amostrasDownload = amostras)
        } catch (e: Exception) {
            ResultadoVelocidade(null, null, null, e.message ?: "falhou")
        }
    }

    private fun medirLatencia(): Double? = try {
        val inicio = System.nanoTime()
        val c = URL("$SERVIDOR/__down?bytes=0").openConnection() as HttpURLConnection
        c.connectTimeout = 3000; c.readTimeout = 3000
        c.inputStream.use { it.readBytes() }
        c.disconnect()
        (System.nanoTime() - inicio) / 1e6
    } catch (e: Exception) { null }

    /** A cada meio segundo, a velocidade desde a última amostra. */
    private suspend fun amostrar(bytes: AtomicLong, fim: Long, aoAmostrar: (Double) -> Unit) {
        var anterior = bytes.get()
        while (System.currentTimeMillis() < fim) {
            delay(500)
            val agora = bytes.get()
            aoAmostrar((agora - anterior) * 8 / 0.5 / 1e6)
            anterior = agora
        }
    }

    private suspend fun medirDownload(duracaoMs: Long, aoAmostrar: (Double) -> Unit): Double? = coroutineScope {
        val bytes = AtomicLong(0)
        val fim = System.currentTimeMillis() + duracaoMs
        val inicio = System.nanoTime()
        val amostrador = launch { amostrar(bytes, fim, aoAmostrar) }
        (1..4).map {
            async(Dispatchers.IO) {
                while (System.currentTimeMillis() < fim) {
                    val c = URL("$SERVIDOR/__down?bytes=25000000").openConnection() as HttpURLConnection
                    c.connectTimeout = 5000; c.readTimeout = 5000
                    try {
                        c.inputStream.use { ler(it, bytes, fim) }
                    } catch (e: Exception) {
                        if (bytes.get() == 0L) throw e   // nada passou: a internet não está chegando
                    } finally { c.disconnect() }
                }
            }
        }.awaitAll()
        amostrador.cancel()
        val segundos = (System.nanoTime() - inicio) / 1e9
        if (bytes.get() == 0L) null else bytes.get() * 8 / segundos / 1e6
    }

    private fun ler(entrada: InputStream, bytes: AtomicLong, fim: Long) {
        val buffer = ByteArray(64 * 1024)
        while (System.currentTimeMillis() < fim) {
            val n = entrada.read(buffer)
            if (n < 0) break
            bytes.addAndGet(n.toLong())
        }
    }

    private suspend fun medirUpload(duracaoMs: Long, aoAmostrar: (Double) -> Unit): Double? = coroutineScope {
        val bytes = AtomicLong(0)
        val fim = System.currentTimeMillis() + duracaoMs
        val inicio = System.nanoTime()
        val amostrador = launch { amostrar(bytes, fim, aoAmostrar) }
        val bloco = ByteArray(64 * 1024) { (it % 251).toByte() }
        (1..4).map {
            async(Dispatchers.IO) {
                while (System.currentTimeMillis() < fim) {
                    val c = URL("$SERVIDOR/__up").openConnection() as HttpURLConnection
                    c.connectTimeout = 5000; c.readTimeout = 5000
                    c.doOutput = true; c.requestMethod = "POST"
                    c.setChunkedStreamingMode(bloco.size)
                    try {
                        c.outputStream.use { saida ->
                            var enviados = 0L
                            while (System.currentTimeMillis() < fim && enviados < 20_000_000) {
                                saida.write(bloco); enviados += bloco.size; bytes.addAndGet(bloco.size.toLong())
                            }
                        }
                        runCatching { c.inputStream.use { it.readBytes() } }
                    } catch (e: Exception) {
                        // fim do tempo no meio do envio: o que já saiu conta
                    } finally { c.disconnect() }
                }
            }
        }.awaitAll()
        amostrador.cancel()
        val segundos = (System.nanoTime() - inicio) / 1e9
        if (bytes.get() == 0L) null else bytes.get() * 8 / segundos / 1e6
    }
}
