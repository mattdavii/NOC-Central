package br.eng.mdsolucoes.nexocampo.rede

/** O que o celular sabe da rede em que está conectado agora. */
data class RedeAtual(
    val tipo: String,                 // "Wi-Fi", "Cabo", "Dados móveis", "Sem rede"
    val ssid: String? = null,
    val bssid: String? = null,
    val sinalDbm: Int? = null,
    val frequenciaMhz: Int? = null,
    val canal: Int? = null,
    val velocidadeMbps: Int? = null,
    val padrao: String? = null,
    val seguranca: String? = null,
    val ip: String? = null,
    val prefixo: Int? = null,
    val gateway: String? = null,
    val dns: List<String> = emptyList(),
)

/** Uma rede Wi-Fi vista ao redor. */
data class RedeWifi(
    val ssid: String,
    val bssid: String,
    val sinalDbm: Int,
    val frequenciaMhz: Int,
    val canal: Int,
    val larguraMhz: Int,
    val seguranca: String,
    val conectada: Boolean,
) {
    val faixa: String get() = faixaDe(frequenciaMhz)
}

data class OcupacaoCanal(val faixa: String, val canal: Int, val redes: Int, val maisForteDbm: Int)

data class ResultadoPing(
    val nome: String,
    val alvo: String,
    val enviados: Int,
    val recebidos: Int,
    val minMs: Double?,
    val mediaMs: Double?,
    val maxMs: Double?,
    val erro: String? = null,
) {
    val perdaPct: Int get() = if (enviados == 0) 100 else ((enviados - recebidos) * 100 / enviados)
}

data class ResultadoDns(val nome: String, val ms: Long?, val enderecos: List<String>, val erro: String? = null)

data class ResultadoVelocidade(
    val downloadMbps: Double?,
    val uploadMbps: Double?,
    val latenciaMs: Double?,
    val erro: String? = null,
)

data class Dispositivo(
    val ip: String,
    val tipo: String,
    val nome: String?,
    val fabricante: String?,
    val modelo: String?,
    val portas: List<Int>,
    val servicos: List<String>,
    val mac: String?,
    val origens: List<String>,       // como foi encontrado: portas, ONVIF, UPnP, mDNS, resposta TCP
    val esteCelular: Boolean = false,
)

fun faixaDe(frequenciaMhz: Int): String = when {
    frequenciaMhz in 2400..2500 -> "2,4 GHz"
    frequenciaMhz in 4900..5900 -> "5 GHz"
    frequenciaMhz in 5925..7125 -> "6 GHz"
    else -> "?"
}

fun canalDe(frequenciaMhz: Int): Int = when {
    frequenciaMhz == 2484 -> 14
    frequenciaMhz in 2412..2472 -> (frequenciaMhz - 2407) / 5
    frequenciaMhz in 4900..5900 -> (frequenciaMhz - 5000) / 5
    frequenciaMhz in 5925..7125 -> (frequenciaMhz - 5950) / 5
    else -> 0
}

/** Leitura do sinal em palavras (o técnico entende sem decorar dBm). */
fun qualidadeSinal(dbm: Int?): String = when {
    dbm == null -> "—"
    dbm >= -55 -> "Excelente"
    dbm >= -67 -> "Bom"
    dbm >= -75 -> "Fraco"
    else -> "Muito fraco"
}
