package br.eng.mdsolucoes.nexocampo.rede

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.wifi.WifiInfo
import android.net.wifi.WifiManager
import android.os.Build
import java.net.Inet4Address

object RedeAtualLeitor {

    @Suppress("DEPRECATION")
    fun ler(contexto: Context): RedeAtual {
        val cm = contexto.getSystemService(ConnectivityManager::class.java)
        val rede = cm.activeNetwork ?: return RedeAtual(tipo = "Sem rede")
        val caps = cm.getNetworkCapabilities(rede)
        val lp = cm.getLinkProperties(rede)
        val tipo = when {
            caps == null -> "Desconhecida"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "Wi-Fi"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "Cabo"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "Dados móveis"
            else -> "Outra"
        }
        val endereco = lp?.linkAddresses?.firstOrNull { it.address is Inet4Address }
        val gateway = lp?.routes?.firstOrNull { it.isDefaultRoute && it.gateway is Inet4Address }?.gateway?.hostAddress
        val dns = lp?.dnsServers?.mapNotNull { it.hostAddress } ?: emptyList()
        var atual = RedeAtual(
            tipo = tipo, ip = endereco?.address?.hostAddress, prefixo = endereco?.prefixLength,
            gateway = gateway, dns = dns,
        )
        if (tipo == "Wi-Fi") {
            val wm = contexto.applicationContext.getSystemService(WifiManager::class.java)
            val info: WifiInfo? = wm?.connectionInfo
            if (info != null) {
                val ssid = info.ssid?.trim('"')?.takeUnless { it.isBlank() || it == "<unknown ssid>" }
                val bssid = info.bssid?.takeUnless { it == "02:00:00:00:00:00" }
                atual = atual.copy(
                    ssid = ssid, bssid = bssid, sinalDbm = info.rssi.takeIf { it > -127 },
                    frequenciaMhz = info.frequency.takeIf { it > 0 }, canal = canalDe(info.frequency).takeIf { it > 0 },
                    velocidadeMbps = info.linkSpeed.takeIf { it > 0 },
                    padrao = if (Build.VERSION.SDK_INT >= 30) padraoWifi(info.wifiStandard) else null,
                    seguranca = if (Build.VERSION.SDK_INT >= 31) segurancaAtual(info.currentSecurityType) else null,
                )
            }
        }
        return atual
    }

    private fun padraoWifi(padrao: Int): String? = when (padrao) {
        1 -> "Wi-Fi antigo (a/b/g)"
        4 -> "Wi-Fi 4 (n)"
        5 -> "Wi-Fi 5 (ac)"
        6 -> "Wi-Fi 6 (ax)"
        8 -> "Wi-Fi 7 (be)"
        else -> null
    }

    private fun segurancaAtual(tipo: Int): String? = when (tipo) {
        0 -> "Aberta"
        1 -> "WEP"
        2 -> "WPA/WPA2"
        3 -> "WPA2/WPA3 Enterprise"
        4 -> "WPA3"
        5 -> "WPA3 Enterprise"
        6 -> "Aberta (OWE)"
        else -> null
    }
}
