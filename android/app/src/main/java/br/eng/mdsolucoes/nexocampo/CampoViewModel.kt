package br.eng.mdsolucoes.nexocampo

import android.app.Application
import android.os.Build
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import br.eng.mdsolucoes.nexocampo.rede.Dispositivo
import br.eng.mdsolucoes.nexocampo.rede.Exportar
import br.eng.mdsolucoes.nexocampo.rede.Levantamento
import br.eng.mdsolucoes.nexocampo.rede.RedeAtual
import br.eng.mdsolucoes.nexocampo.rede.RedeAtualLeitor
import br.eng.mdsolucoes.nexocampo.rede.RedeWifi
import br.eng.mdsolucoes.nexocampo.rede.ResultadoDns
import br.eng.mdsolucoes.nexocampo.rede.ResultadoPing
import br.eng.mdsolucoes.nexocampo.rede.ResultadoVelocidade
import br.eng.mdsolucoes.nexocampo.rede.Testes
import br.eng.mdsolucoes.nexocampo.rede.Varredura
import br.eng.mdsolucoes.nexocampo.rede.WifiAoRedor
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class CampoViewModel(app: Application) : AndroidViewModel(app) {

    private val contexto get() = getApplication<Application>()

    val versao: String = runCatching {
        val pm = contexto.packageManager
        if (Build.VERSION.SDK_INT >= 33) pm.getPackageInfo(contexto.packageName, android.content.pm.PackageManager.PackageInfoFlags.of(0)).versionName
        else @Suppress("DEPRECATION") pm.getPackageInfo(contexto.packageName, 0).versionName
    }.getOrNull() ?: "0.1"

    // Rede e Wi-Fi
    var rede by mutableStateOf<RedeAtual?>(null); private set
    var wifi by mutableStateOf<List<RedeWifi>>(emptyList()); private set
    var wifiAtualizado by mutableStateOf(true); private set
    var buscandoWifi by mutableStateOf(false); private set
    var wifiBuscado by mutableStateOf(false); private set

    // Testes
    var pings by mutableStateOf<List<ResultadoPing>>(emptyList()); private set
    var dns by mutableStateOf<List<ResultadoDns>>(emptyList()); private set
    var velocidade by mutableStateOf<ResultadoVelocidade?>(null); private set
    var testando by mutableStateOf(false); private set
    var etapaTeste by mutableStateOf(""); private set

    // Varredura
    var dispositivos by mutableStateOf<List<Dispositivo>>(emptyList()); private set
    var varrendo by mutableStateOf(false); private set
    var progresso by mutableStateOf(0f); private set
    var etapaVarredura by mutableStateOf(""); private set
    var varreduraFeita by mutableStateOf(false); private set

    // Relatório
    var local by mutableStateOf("")
    var tecnico by mutableStateOf("")
    var observacoes by mutableStateOf("")
    var mensagem by mutableStateOf<String?>(null)

    fun atualizarRede() {
        rede = runCatching { RedeAtualLeitor.ler(contexto) }.getOrElse { RedeAtual(tipo = "Não foi possível ler") }
    }

    fun buscarWifi() {
        if (buscandoWifi) return
        buscandoWifi = true
        viewModelScope.launch {
            atualizarRede()
            val (lista, nova) = WifiAoRedor.buscar(contexto, rede?.bssid)
            wifi = lista; wifiAtualizado = nova; wifiBuscado = true
            buscandoWifi = false
        }
    }

    fun melhorCanal24() = WifiAoRedor.melhorCanal24(wifi, rede?.bssid)

    fun rodarTestes() {
        if (testando) return
        testando = true
        pings = emptyList(); dns = emptyList(); velocidade = null
        viewModelScope.launch {
            atualizarRede()
            val alvos = buildList {
                rede?.gateway?.let { add("Gateway (roteador)" to it) }
                add("Internet (Cloudflare)" to "1.1.1.1")
                add("Internet (Google)" to "8.8.8.8")
            }
            for ((nome, alvo) in alvos) {
                etapaTeste = "Ping: $nome…"
                pings = pings + Testes.ping(nome, alvo)
            }
            for (nome in listOf("google.com", "nexopulse.up.railway.app")) {
                etapaTeste = "DNS: $nome…"
                dns = dns + Testes.dns(nome)
            }
            velocidade = Testes.velocidade { etapaTeste = it }
            etapaTeste = ""
            testando = false
        }
    }

    fun enderecosParaVarrer(): Int {
        val p = (rede?.prefixo ?: 24).coerceIn(24, 30)
        return (1 shl (32 - p)) - 2
    }

    fun rodarVarredura() {
        if (varrendo) return
        atualizarRede()
        val r: RedeAtual = rede ?: return
        if (r.ip == null || r.tipo == "Dados móveis") {
            mensagem = "Conecte o celular ao Wi-Fi do local para varrer a rede."
            return
        }
        varrendo = true; progresso = 0f; dispositivos = emptyList()
        viewModelScope.launch {
            val lista = Varredura(contexto).rodar(r) { p, etapa, parcial ->
                viewModelScope.launch(Dispatchers.Main) { progresso = p; etapaVarredura = etapa; dispositivos = parcial }
            }
            dispositivos = lista; progresso = 1f; etapaVarredura = ""
            varrendo = false; varreduraFeita = true
        }
    }

    private fun levantamento() = Levantamento(
        local = local.trim(), tecnico = tecnico.trim(), observacoes = observacoes.trim(), quando = System.currentTimeMillis(),
        versaoApp = versao, rede = rede, wifi = wifi, melhorCanal24 = melhorCanal24(), pings = pings, dns = dns,
        velocidade = velocidade, dispositivos = dispositivos,
    )

    fun exportar(pdf: Boolean, json: Boolean) {
        viewModelScope.launch {
            val l = levantamento()
            val arquivos = withContext(Dispatchers.IO) {
                buildList {
                    if (pdf) add(Exportar.pdf(contexto, l))
                    if (json) add(Exportar.json(contexto, l))
                }
            }
            runCatching { Exportar.compartilhar(contexto, arquivos) }
                .onFailure { mensagem = "Não foi possível abrir o envio: ${it.message}" }
        }
    }
}
