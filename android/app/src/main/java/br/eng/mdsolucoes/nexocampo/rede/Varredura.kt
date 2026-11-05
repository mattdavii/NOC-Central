package br.eng.mdsolucoes.nexocampo.rede

import android.content.Context
import android.net.wifi.WifiManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withTimeoutOrNull
import java.io.File
import java.net.ConnectException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.net.SocketTimeoutException
import java.net.URL
import java.util.Collections
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger

/**
 * Varredura da rede local pelo celular.
 *
 * O Android 10+ não deixa um app ler o MAC dos outros aparelhos, então a identificação vem de outros
 * caminhos: portas abertas, respostas de ONVIF (câmeras), UPnP/SSDP (roteadores, TVs, NVRs), mDNS
 * (impressoras, Chromecast, computadores) e a página web do aparelho (título e cabeçalho "Server").
 * Um aparelho que recusa a conexão também está vivo: o "connection refused" conta como presença.
 */
class Varredura(private val contexto: Context) {

    companion object {
        val PORTAS = listOf(21, 22, 23, 53, 80, 81, 139, 443, 445, 502, 554, 631, 1883, 3389, 5000, 8000,
            8008, 8009, 8080, 8291, 8443, 8728, 9100, 34567, 37777)
        private val PORTAS_WEB = listOf(80, 8080, 81, 8000)
        private const val TEMPO_PORTA_MS = 400
    }

    private class Achado(val ip: String) {
        val portas: MutableSet<Int> = Collections.synchronizedSet(sortedSetOf<Int>())
        val servicos: MutableSet<String> = Collections.synchronizedSet(linkedSetOf<String>())
        val origens: MutableSet<String> = Collections.synchronizedSet(linkedSetOf<String>())
        @Volatile var nome: String? = null
        @Volatile var fabricante: String? = null
        @Volatile var modelo: String? = null
        @Volatile var mac: String? = null
        @Volatile var onvif = false
        @Volatile var gatewayUpnp = false
        @Volatile var vivo = false
    }

    private val achados = ConcurrentHashMap<String, Achado>()
    private fun achado(ip: String) = achados.getOrPut(ip) { Achado(ip) }

    /** Roda a varredura inteira. [progresso] recebe (0..1, etapa) e a lista parcial. */
    suspend fun rodar(rede: RedeAtual, progresso: (Float, String, List<Dispositivo>) -> Unit): List<Dispositivo> {
        val ip = rede.ip ?: return emptyList()
        val alvos = enderecosDaRede(ip, rede.prefixo ?: 24)
        val wm = contexto.applicationContext.getSystemService(WifiManager::class.java)
        val trava = wm?.createMulticastLock("nexocampo")?.apply { setReferenceCounted(false); acquire() }
        try {
            coroutineScope {
                // Descobertas por multicast rodam junto da varredura de portas.
                val descobertas = listOf(
                    async(Dispatchers.IO) { runCatching { onvif() } },
                    async(Dispatchers.IO) { runCatching { ssdp() } },
                    async(Dispatchers.IO) { runCatching { mdns() } },
                )
                val total = alvos.size * PORTAS.size
                val feitos = AtomicInteger(0)
                val limite = Semaphore(96)
                val portasJobs = alvos.flatMap { alvo ->
                    PORTAS.map { porta ->
                        launch(Dispatchers.IO) {
                            limite.withPermit { testarPorta(alvo, porta) }
                            val n = feitos.incrementAndGet()
                            if (n % 200 == 0) progresso(0.75f * n / total, "Testando os endereços da rede…", parcial(rede))
                        }
                    }
                }
                portasJobs.forEach { it.join() }
                descobertas.awaitAll()
            }
            progresso(0.8f, "Lendo as páginas dos aparelhos…", parcial(rede))
            identificarPelaWeb()
            progresso(0.92f, "Buscando nomes…", parcial(rede))
            nomesReversos()
            lerArp()
        } finally {
            runCatching { trava?.release() }
        }
        val lista = parcial(rede)
        progresso(1f, "Pronto", lista)
        return lista
    }

    // ------------------------------------------------------------------ endereços
    private fun enderecosDaRede(ip: String, prefixo: Int): List<String> {
        val partes = ip.split(".").map { it.toInt() }
        val numero = (partes[0] shl 24) or (partes[1] shl 16) or (partes[2] shl 8) or partes[3]
        // Rede maior que /24 (ex.: /16): varre só o /24 do celular, como o sensor faz.
        val p = prefixo.coerceIn(24, 30)
        val mascara = (-1 shl (32 - p))
        val rede = numero and mascara
        val quantos = (1 shl (32 - p)) - 2
        return (1..quantos).map { i ->
            val n = rede + i
            "${(n ushr 24) and 255}.${(n ushr 16) and 255}.${(n ushr 8) and 255}.${n and 255}"
        }
    }

    private fun testarPorta(ip: String, porta: Int) {
        try {
            Socket().use { it.connect(InetSocketAddress(ip, porta), TEMPO_PORTA_MS) }
            val a = achado(ip)
            a.portas.add(porta); a.vivo = true; a.origens.add("portas")
        } catch (e: ConnectException) {
            // "Connection refused": o aparelho existe, só não tem nada nesta porta.
            if (e.message?.contains("ECONNREFUSED") == true || e.message?.contains("refused", true) == true) {
                val a = achado(ip); a.vivo = true; a.origens.add("respondeu")
            }
        } catch (e: SocketTimeoutException) {
        } catch (e: Exception) {
        }
    }

    // ------------------------------------------------------------------ ONVIF (câmeras e gravadores)
    private fun onvif() {
        val id = UUID.randomUUID().toString()
        val sonda = """<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
<e:Header><w:MessageID>uuid:$id</w:MessageID><w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To><w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action></e:Header>
<e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body></e:Envelope>"""
        ouvirMulticast(sonda.toByteArray(), "239.255.255.250", 3702, 3500) { ip, texto ->
            val a = achado(ip)
            a.vivo = true; a.onvif = true; a.origens.add("ONVIF"); a.servicos.add("ONVIF")
            Regex("""onvif://www\.onvif\.org/name/([^\s<]+)""").find(texto)?.let { a.nome = a.nome ?: decodificar(it.groupValues[1]) }
            Regex("""onvif://www\.onvif\.org/hardware/([^\s<]+)""").find(texto)?.let { a.modelo = a.modelo ?: decodificar(it.groupValues[1]) }
        }
    }

    // ------------------------------------------------------------------ UPnP / SSDP
    private fun ssdp() {
        val busca = "M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 2\r\nST: ssdp:all\r\n\r\n"
        val locais = ConcurrentHashMap<String, String>()
        ouvirMulticast(busca.toByteArray(), "239.255.255.250", 1900, 3500) { ip, texto ->
            val a = achado(ip)
            a.vivo = true; a.origens.add("UPnP")
            val cabecalho = { nome: String -> Regex("(?im)^$nome:\\s*(.+)$").find(texto)?.groupValues?.get(1)?.trim() }
            cabecalho("ST")?.let { if (it.contains("InternetGatewayDevice", true)) a.gatewayUpnp = true }
            cabecalho("SERVER")?.let { a.servicos.add("UPnP: ${it.take(60)}") }
            cabecalho("LOCATION")?.let { locais.putIfAbsent(ip, it) }
        }
        // A descrição do aparelho (XML) costuma trazer fabricante e modelo.
        for ((ip, local) in locais) {
            val xml = baixar(local, 1500, 64 * 1024) ?: continue
            val a = achado(ip)
            tag(xml, "friendlyName")?.let { a.nome = a.nome ?: it }
            tag(xml, "manufacturer")?.let { a.fabricante = a.fabricante ?: it }
            tag(xml, "modelName")?.let { a.modelo = a.modelo ?: it }
            if (xml.contains("InternetGatewayDevice", true)) a.gatewayUpnp = true
        }
    }

    // ------------------------------------------------------------------ mDNS
    private val TIPOS_MDNS = listOf("_services._dns-sd._udp", "_http._tcp", "_ipp._tcp", "_printer._tcp", "_pdl-datastream._tcp",
        "_googlecast._tcp", "_airplay._tcp", "_rtsp._tcp", "_hap._tcp", "_smb._tcp", "_workstation._tcp", "_device-info._tcp")

    private fun mdns() {
        // Pergunta de uma porta comum (não a 5353): os aparelhos respondem direto para o celular.
        val pergunta = Mdns.consulta(TIPOS_MDNS.map { "$it.local" })
        ouvirMulticast(pergunta, "224.0.0.251", 5353, 3500) { _, _ -> }
    }

    /** Envia para um grupo multicast e entrega cada resposta (IP de origem, texto) até o prazo. */
    private fun ouvirMulticast(
        dados: ByteArray, grupo: String, porta: Int, prazoMs: Long, portaLocal: Int = 0,
        aoReceber: (String, String) -> Unit,
    ) {
        DatagramSocket(portaLocal).use { socket ->
            socket.soTimeout = 400
            val destino = InetSocketAddress(InetAddress.getByName(grupo), porta)
            repeat(2) { socket.send(DatagramPacket(dados, dados.size, destino)) }
            val buffer = ByteArray(9000)
            val fim = System.currentTimeMillis() + prazoMs
            while (System.currentTimeMillis() < fim) {
                val pacote = DatagramPacket(buffer, buffer.size)
                try { socket.receive(pacote) } catch (e: SocketTimeoutException) { continue }
                val ip = pacote.address?.hostAddress ?: continue
                if (porta == 5353) {
                    Mdns.ler(pacote.data.copyOf(pacote.length))?.let { r ->
                        val a = achado(ip)
                        a.vivo = true; a.origens.add("mDNS")
                        r.servicos.forEach { s -> a.servicos.add(s) }
                        r.nomeHost?.let { a.nome = a.nome ?: it }
                        r.instancias.firstOrNull()?.let { a.nome = a.nome ?: it }
                    }
                } else {
                    aoReceber(ip, String(pacote.data, 0, pacote.length, Charsets.UTF_8))
                }
            }
        }
    }

    // ------------------------------------------------------------------ página web do aparelho
    private suspend fun identificarPelaWeb() = coroutineScope {
        val limite = Semaphore(16)
        achados.values.filter { a -> PORTAS_WEB.any { it in a.portas } }.map { a ->
            async(Dispatchers.IO) {
                limite.withPermit {
                    val porta = PORTAS_WEB.first { it in a.portas }
                    val resposta = cabecalhoETitulo("http://${a.ip}:$porta/") ?: return@withPermit
                    val (servidor, titulo, realm) = resposta
                    listOfNotNull(servidor, titulo, realm).forEach { texto -> Fabricantes.detectar(texto)?.let { a.fabricante = a.fabricante ?: it } }
                    if (!titulo.isNullOrBlank()) a.servicos.add("Web: ${titulo.take(60)}")
                    if (!servidor.isNullOrBlank()) a.servicos.add("Servidor web: ${servidor.take(40)}")
                }
            }
        }.awaitAll()
    }

    private fun cabecalhoETitulo(url: String): Triple<String?, String?, String?>? = try {
        val c = URL(url).openConnection() as HttpURLConnection
        c.connectTimeout = 1500; c.readTimeout = 1500; c.instanceFollowRedirects = true
        c.setRequestProperty("User-Agent", "NexoCampo")
        val codigo = c.responseCode
        val servidor = c.getHeaderField("Server")
        val realm = c.getHeaderField("WWW-Authenticate")
        val corpo = runCatching {
            (if (codigo >= 400) c.errorStream else c.inputStream)?.use { ent -> String(ent.readNBytesCompat(32 * 1024), Charsets.UTF_8) }
        }.getOrNull() ?: ""
        c.disconnect()
        val titulo = Regex("(?is)<title[^>]*>(.*?)</title>").find(corpo)?.groupValues?.get(1)?.trim()?.replace(Regex("\\s+"), " ")
        Triple(servidor, titulo?.takeIf { it.isNotBlank() }, realm)
    } catch (e: Exception) { null }

    private fun baixar(url: String, tempoMs: Int, maximo: Int): String? = try {
        val c = URL(url).openConnection() as HttpURLConnection
        c.connectTimeout = tempoMs; c.readTimeout = tempoMs
        val texto = c.inputStream.use { String(it.readNBytesCompat(maximo), Charsets.UTF_8) }
        c.disconnect(); texto
    } catch (e: Exception) { null }

    private fun tag(xml: String, nome: String) =
        Regex("(?is)<$nome>(.*?)</$nome>").find(xml)?.groupValues?.get(1)?.trim()?.takeIf { it.isNotBlank() }

    private fun decodificar(texto: String) = runCatching { java.net.URLDecoder.decode(texto, "UTF-8") }.getOrDefault(texto)

    // ------------------------------------------------------------------ nomes e MAC
    private suspend fun nomesReversos() = coroutineScope {
        val limite = Semaphore(24)
        achados.values.filter { it.vivo && it.nome == null }.map { a ->
            async(Dispatchers.IO) {
                limite.withPermit {
                    withTimeoutOrNull(2000) {
                        runCatching { InetAddress.getByName(a.ip).canonicalHostName }.getOrNull()
                            ?.takeIf { it != a.ip }?.let { a.nome = it }
                    }
                }
            }
        }.awaitAll()
    }

    /** Em Android antigo (até o 9) a tabela ARP ainda é legível: aproveita o MAC quando der. */
    private fun lerArp() {
        runCatching {
            File("/proc/net/arp").readLines().drop(1).forEach { linha ->
                val c = linha.split(Regex("\\s+"))
                if (c.size >= 4 && c[3] != "00:00:00:00:00:00") achados[c[0]]?.mac = c[3].uppercase()
            }
        }
    }

    // ------------------------------------------------------------------ resultado
    private fun parcial(rede: RedeAtual): List<Dispositivo> {
        if (rede.ip != null) achado(rede.ip).vivo = true
        return achados.values.filter { it.vivo || it.portas.isNotEmpty() }
            .map { a -> paraDispositivo(a, rede) }
            .sortedBy { ipComoNumero(it.ip) }
    }

    private fun paraDispositivo(a: Achado, rede: RedeAtual): Dispositivo {
        val este = a.ip == rede.ip
        val fabricante = a.fabricante ?: a.modelo?.let { Fabricantes.detectar(it) } ?: a.nome?.let { Fabricantes.detectar(it) }
        return Dispositivo(
            ip = a.ip, tipo = Classificador.tipo(a.ip == rede.gateway, este, a.portas.toSet(), a.servicos.toSet(), fabricante, a.onvif, a.gatewayUpnp),
            nome = a.nome, fabricante = fabricante, modelo = a.modelo, portas = a.portas.toList().sorted(),
            servicos = a.servicos.toList(), mac = a.mac, origens = a.origens.toList(), esteCelular = este,
        )
    }

    private fun ipComoNumero(ip: String) = ip.split(".").fold(0L) { acc, p -> acc * 256 + (p.toLongOrNull() ?: 0) }
}

/** InputStream.readNBytes só existe a partir do Android 13. */
internal fun java.io.InputStream.readNBytesCompat(maximo: Int): ByteArray {
    val saida = java.io.ByteArrayOutputStream()
    val buffer = ByteArray(8192)
    while (saida.size() < maximo) {
        val n = read(buffer, 0, minOf(buffer.size, maximo - saida.size()))
        if (n < 0) break
        saida.write(buffer, 0, n)
    }
    return saida.toByteArray()
}
