package br.eng.mdsolucoes.nexocampo.rede

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.wifi.ScanResult
import android.net.wifi.WifiManager
import android.os.Build
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import kotlin.coroutines.resume

object WifiAoRedor {

    /** Pede uma busca nova; se o Android recusar (limite de 4 buscas a cada 2 minutos), usa a última. */
    suspend fun buscar(contexto: Context, bssidConectado: String?): Pair<List<RedeWifi>, Boolean> {
        val wm = contexto.applicationContext.getSystemService(WifiManager::class.java)
            ?: return emptyList<RedeWifi>() to false
        val nova = withTimeoutOrNull(12_000) {
            suspendCancellableCoroutine { cont ->
                val receptor = object : BroadcastReceiver() {
                    override fun onReceive(c: Context?, i: Intent?) {
                        runCatching { contexto.applicationContext.unregisterReceiver(this) }
                        if (cont.isActive) cont.resume(i?.getBooleanExtra(WifiManager.EXTRA_RESULTS_UPDATED, false) ?: false)
                    }
                }
                val filtro = IntentFilter(WifiManager.SCAN_RESULTS_AVAILABLE_ACTION)
                if (Build.VERSION.SDK_INT >= 33) {
                    contexto.applicationContext.registerReceiver(receptor, filtro, Context.RECEIVER_NOT_EXPORTED)
                } else {
                    contexto.applicationContext.registerReceiver(receptor, filtro)
                }
                cont.invokeOnCancellation { runCatching { contexto.applicationContext.unregisterReceiver(receptor) } }
                @Suppress("DEPRECATION")
                if (!wm.startScan()) {
                    runCatching { contexto.applicationContext.unregisterReceiver(receptor) }
                    if (cont.isActive) cont.resume(false)
                }
            }
        } ?: false
        val resultados = try { wm.scanResults } catch (e: SecurityException) { emptyList() }
        val redes = resultados.map { converter(it, bssidConectado) }
            .sortedByDescending { it.sinalDbm }
        return redes to nova
    }

    @Suppress("DEPRECATION")
    private fun converter(r: ScanResult, bssidConectado: String?): RedeWifi {
        val ssid = (if (Build.VERSION.SDK_INT >= 33) r.wifiSsid?.toString()?.trim('"') else r.SSID)
            ?.takeUnless { it.isBlank() } ?: "(rede oculta)"
        val largura = when (r.channelWidth) {
            ScanResult.CHANNEL_WIDTH_20MHZ -> 20
            ScanResult.CHANNEL_WIDTH_40MHZ -> 40
            ScanResult.CHANNEL_WIDTH_80MHZ -> 80
            ScanResult.CHANNEL_WIDTH_160MHZ, ScanResult.CHANNEL_WIDTH_80MHZ_PLUS_MHZ -> 160
            else -> if (Build.VERSION.SDK_INT >= 33 && r.channelWidth == ScanResult.CHANNEL_WIDTH_320MHZ) 320 else 20
        }
        return RedeWifi(
            ssid = ssid, bssid = r.BSSID ?: "", sinalDbm = r.level, frequenciaMhz = r.frequency,
            canal = canalDe(r.frequency), larguraMhz = largura, seguranca = seguranca(r.capabilities ?: ""),
            conectada = bssidConectado != null && r.BSSID.equals(bssidConectado, ignoreCase = true),
        )
    }

    private fun seguranca(cap: String): String = when {
        cap.contains("SAE") || cap.contains("WPA3") -> "WPA3"
        cap.contains("EAP") -> "WPA Enterprise"
        cap.contains("RSN") || cap.contains("WPA2") -> "WPA2"
        cap.contains("WPA") -> "WPA (antigo)"
        cap.contains("WEP") -> "WEP (inseguro)"
        cap.contains("OWE") -> "Aberta (OWE)"
        else -> "Aberta"
    }

    fun ocupacao(redes: List<RedeWifi>): List<OcupacaoCanal> =
        redes.groupBy { it.faixa to it.canal }
            .map { (chave, lista) -> OcupacaoCanal(chave.first, chave.second, lista.size, lista.maxOf { it.sinalDbm }) }
            .sortedWith(compareBy({ it.faixa }, { it.canal }))

    /**
     * Melhor canal de 2,4 GHz entre 1, 6 e 11 (os únicos que não se sobrepõem): soma a interferência das redes
     * a até 4 canais de distância, pesando pelo sinal. Devolve o canal e uma explicação curta.
     */
    fun melhorCanal24(redes: List<RedeWifi>, bssidConectado: String?): Pair<Int, String>? {
        val vizinhas = redes.filter { it.faixa == "2,4 GHz" && !it.bssid.equals(bssidConectado, ignoreCase = true) }
        if (vizinhas.isEmpty()) return null
        val pontos = listOf(1, 6, 11).associateWith { c ->
            vizinhas.filter { kotlin.math.abs(it.canal - c) <= 4 }.sumOf { (100 + it.sinalDbm).coerceAtLeast(1) }
        }
        val melhor = pontos.minByOrNull { it.value }!!
        val redesNele = vizinhas.count { kotlin.math.abs(it.canal - melhor.key) <= 4 }
        return melhor.key to "$redesNele rede(s) vizinha(s) interferem nele, contra " +
            pontos.filterKeys { it != melhor.key }.entries.joinToString(" e ") { e ->
                "${vizinhas.count { kotlin.math.abs(it.canal - e.key) <= 4 }} no canal ${e.key}"
            }
    }
}
