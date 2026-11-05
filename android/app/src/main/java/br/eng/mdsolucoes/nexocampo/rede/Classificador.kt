package br.eng.mdsolucoes.nexocampo.rede

/** Tipo provável do aparelho pelo que ele mostrou. A ordem importa: o sinal mais forte vem primeiro. */
object Classificador {

    private val CAMERAS = setOf("Hikvision", "EZVIZ (Hikvision)", "Dahua", "Intelbras", "Uniview", "Axis",
        "Xiongmai (XMEye/iCSee)", "Giga Security", "Tecvoz", "JFL")
    private val SOLAR = setOf("Sungrow", "Growatt", "Fronius", "Solis", "GoodWe", "Deye", "SMA", "Canadian Solar", "Huawei (solar)")
    private val RADIOS = setOf("Ubiquiti", "TP-Link", "Mercusys", "Tenda", "D-Link", "Ruckus", "Aruba")

    fun tipo(
        eGateway: Boolean, esteCelular: Boolean, portas: Set<Int>, servicos: Set<String>,
        fabricante: String?, onvif: Boolean, gatewayUpnp: Boolean,
    ): String {
        val servicosTexto = servicos.joinToString(" ").lowercase()
        return when {
            esteCelular -> "Este celular"
            eGateway -> "Roteador / gateway"
            fabricante == "NexoPulse (sensor da MD)" -> "Sensor NexoPulse"
            onvif || 554 in portas || 37777 in portas || 34567 in portas || fabricante in CAMERAS ||
                servicosTexto.contains("rtsp") -> "Câmera / DVR / NVR"
            fabricante == "MikroTik" || 8291 in portas || 8728 in portas -> "MikroTik"
            fabricante in SOLAR -> "Inversor / datalogger solar"
            502 in portas -> "Equipamento industrial (Modbus)"
            9100 in portas || 631 in portas || servicosTexto.contains("impress") -> "Impressora"
            gatewayUpnp -> "Roteador"
            fabricante == "Starlink" -> "Starlink"
            servicosTexto.contains("cast") || servicosTexto.contains("airplay") || 8008 in portas || 8009 in portas -> "TV / mídia"
            fabricante in RADIOS -> "Rádio / ponto de acesso"
            fabricante == "Synology" || fabricante == "QNAP" -> "Armazenamento (NAS)"
            3389 in portas || 445 in portas || 139 in portas || servicosTexto.contains("computador") -> "Computador"
            1883 in portas || servicosTexto.contains("homekit") -> "Automação / IoT"
            22 in portas -> "Servidor / Linux"
            53 in portas && 80 in portas -> "Roteador"
            portas.any { it in setOf(80, 443, 8080, 8443) } -> "Aparelho com página web"
            else -> "Não identificado"
        }
    }
}
