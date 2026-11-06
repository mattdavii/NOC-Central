package br.eng.mdsolucoes.nexocampo.rede

/** Marca a partir de um texto do aparelho (título da página, cabeçalho Server, UPnP, ONVIF). */
object Fabricantes {

    private val MARCAS = listOf(
        "hikvision" to "Hikvision", "hik-connect" to "Hikvision", "ezviz" to "EZVIZ (Hikvision)",
        "dahua" to "Dahua", "intelbras" to "Intelbras", "uniview" to "Uniview", "axis" to "Axis",
        "xiongmai" to "Xiongmai (XMEye/iCSee)", "xmeye" to "Xiongmai (XMEye/iCSee)", "icsee" to "Xiongmai (XMEye/iCSee)",
        "jfss" to "Xiongmai (XMEye/iCSee)", "giga" to "Giga Security", "tecvoz" to "Tecvoz", "jfl" to "JFL",
        "mikrotik" to "MikroTik", "routeros" to "MikroTik", "ubiquiti" to "Ubiquiti", "unifi" to "Ubiquiti", "airos" to "Ubiquiti",
        "tp-link" to "TP-Link", "tplink" to "TP-Link", "omada" to "TP-Link", "mercusys" to "Mercusys", "d-link" to "D-Link",
        "tenda" to "Tenda", "multilaser" to "Multilaser", "huawei" to "Huawei", "zte" to "ZTE", "fiberhome" to "FiberHome",
        "nokia" to "Nokia", "sagemcom" to "Sagemcom", "askey" to "Askey", "mitrastar" to "MitraStar", "parks" to "Parks",
        "furukawa" to "Furukawa", "cisco" to "Cisco", "aruba" to "Aruba", "ruckus" to "Ruckus", "fortinet" to "Fortinet",
        "fortigate" to "Fortinet", "pfsense" to "pfSense", "opnsense" to "OPNsense", "starlink" to "Starlink",
        "synology" to "Synology", "qnap" to "QNAP",
        "hp " to "HP", "hewlett" to "HP", "laserjet" to "HP", "epson" to "Epson", "brother" to "Brother", "canon" to "Canon",
        "samsung" to "Samsung", "lg " to "LG", "sony" to "Sony", "philips" to "Philips", "roku" to "Roku", "chromecast" to "Google",
        "sungrow" to "Sungrow", "growatt" to "Growatt", "fronius" to "Fronius", "solis" to "Solis", "goodwe" to "GoodWe",
        "deye" to "Deye", "sma " to "SMA", "weg" to "WEG", "canadian solar" to "Canadian Solar", "huawei sun" to "Huawei (solar)",
        "schneider" to "Schneider Electric", "siemens" to "Siemens", "moxa" to "Moxa", "advantech" to "Advantech",
        "raspberry" to "Raspberry Pi", "nexopulse" to "NexoPulse (sensor da MD)",
    )

    fun detectar(texto: String): String? {
        val t = " ${texto.lowercase()} "
        return MARCAS.firstOrNull { t.contains(it.first) }?.second
    }
}
