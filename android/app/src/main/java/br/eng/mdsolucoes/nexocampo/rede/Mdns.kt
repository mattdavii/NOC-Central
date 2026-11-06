package br.eng.mdsolucoes.nexocampo.rede

import java.io.ByteArrayOutputStream

/** O mínimo de DNS para perguntar por serviços mDNS e ler as respostas (RFC 6762/6763). */
object Mdns {

    class Resposta(val servicos: List<String>, val instancias: List<String>, val nomeHost: String?)

    private val NOMES_SERVICO = mapOf(
        "_http._tcp" to "Página web", "_ipp._tcp" to "Impressão (IPP)", "_printer._tcp" to "Impressão (LPD)",
        "_pdl-datastream._tcp" to "Impressão (9100)", "_googlecast._tcp" to "Chromecast / Google Cast",
        "_airplay._tcp" to "AirPlay", "_rtsp._tcp" to "Vídeo (RTSP)", "_hap._tcp" to "Casa inteligente (HomeKit)",
        "_smb._tcp" to "Compartilhamento de arquivos", "_workstation._tcp" to "Computador", "_device-info._tcp" to "Informações do aparelho",
    )

    fun consulta(nomes: List<String>): ByteArray {
        val saida = ByteArrayOutputStream()
        fun u16(v: Int) { saida.write((v shr 8) and 255); saida.write(v and 255) }
        u16(0); u16(0); u16(nomes.size); u16(0); u16(0); u16(0)
        for (nome in nomes) {
            for (rotulo in nome.split('.')) {
                val b = rotulo.toByteArray(Charsets.UTF_8)
                saida.write(b.size); saida.write(b)
            }
            saida.write(0)
            u16(12)          // PTR
            u16(0x8001)      // IN, pedindo resposta unicast
        }
        return saida.toByteArray()
    }

    fun ler(p: ByteArray): Resposta? {
        if (p.size < 12) return null
        fun u16(i: Int) = ((p[i].toInt() and 255) shl 8) or (p[i + 1].toInt() and 255)
        val perguntas = u16(4)
        val registros = u16(6) + u16(8) + u16(10)
        var i = 12
        try {
            repeat(perguntas) { i = pularNome(p, i) + 4 }
            val servicos = linkedSetOf<String>()
            val instancias = linkedSetOf<String>()
            var host: String? = null
            repeat(registros) {
                val (nome, depois) = lerNome(p, i)
                i = depois
                val tipo = u16(i); val tamanho = u16(i + 8)
                val dados = i + 10
                when (tipo) {
                    12 -> { // PTR
                        val (alvo, _) = lerNome(p, dados)
                        if (nome.startsWith("_services._dns-sd._udp")) {
                            val t = alvo.removeSuffix(".local")
                            servicos.add(NOMES_SERVICO[t] ?: t)
                        } else {
                            val t = nome.removeSuffix(".local")
                            servicos.add(NOMES_SERVICO[t] ?: t)
                            instancias.add(alvo.substringBefore("._"))
                        }
                    }
                    1 -> if (host == null && nome.endsWith(".local")) host = nome.removeSuffix(".local")
                    33 -> { // SRV: o nome do aparelho fica no alvo
                        val (alvo, _) = lerNome(p, dados + 6)
                        if (host == null && alvo.endsWith(".local")) host = alvo.removeSuffix(".local")
                    }
                }
                i = dados + tamanho
            }
            if (servicos.isEmpty() && instancias.isEmpty() && host == null) return null
            return Resposta(servicos.toList(), instancias.toList(), host)
        } catch (e: IndexOutOfBoundsException) {
            return null
        }
    }

    private fun pularNome(p: ByteArray, inicio: Int): Int {
        var i = inicio
        while (true) {
            val n = p[i].toInt() and 255
            if (n == 0) return i + 1
            if ((n and 0xC0) == 0xC0) return i + 2
            i += n + 1
        }
    }

    /** Nome com compressão de ponteiros; devolve (nome, posição depois do nome). */
    private fun lerNome(p: ByteArray, inicio: Int): Pair<String, Int> {
        val partes = mutableListOf<String>()
        var i = inicio
        var depois = -1
        var saltos = 0
        while (true) {
            val n = p[i].toInt() and 255
            if (n == 0) { if (depois < 0) depois = i + 1; break }
            if ((n and 0xC0) == 0xC0) {
                if (depois < 0) depois = i + 2
                i = ((n and 0x3F) shl 8) or (p[i + 1].toInt() and 255)
                if (++saltos > 20) break
                continue
            }
            partes.add(String(p, i + 1, n, Charsets.UTF_8))
            i += n + 1
        }
        return partes.joinToString(".") to depois
    }
}
