package br.eng.mdsolucoes.nexocampo

import android.app.Application
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.Build
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import br.eng.mdsolucoes.nexocampo.rede.Ambiente
import br.eng.mdsolucoes.nexocampo.rede.Dispositivo
import br.eng.mdsolucoes.nexocampo.rede.Exportar
import br.eng.mdsolucoes.nexocampo.rede.Foto
import br.eng.mdsolucoes.nexocampo.rede.Levantamento
import br.eng.mdsolucoes.nexocampo.rede.PingContinuo
import br.eng.mdsolucoes.nexocampo.rede.RedeAtual
import br.eng.mdsolucoes.nexocampo.rede.RedeAtualLeitor
import br.eng.mdsolucoes.nexocampo.rede.RedeWifi
import br.eng.mdsolucoes.nexocampo.rede.ResultadoDns
import br.eng.mdsolucoes.nexocampo.rede.ResultadoPing
import br.eng.mdsolucoes.nexocampo.rede.ResultadoVelocidade
import br.eng.mdsolucoes.nexocampo.rede.Testes
import br.eng.mdsolucoes.nexocampo.rede.Varredura
import br.eng.mdsolucoes.nexocampo.rede.Visitas
import br.eng.mdsolucoes.nexocampo.rede.WifiAoRedor
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.util.UUID

class CampoViewModel(app: Application) : AndroidViewModel(app) {

    private val contexto get() = getApplication<Application>()

    val versao: String = runCatching {
        val pm = contexto.packageManager
        if (Build.VERSION.SDK_INT >= 33) pm.getPackageInfo(contexto.packageName, android.content.pm.PackageManager.PackageInfoFlags.of(0)).versionName
        else @Suppress("DEPRECATION") pm.getPackageInfo(contexto.packageName, 0).versionName
    }.getOrNull() ?: "0.2"

    // Visita em andamento
    var idVisita by mutableStateOf(UUID.randomUUID().toString()); private set
    private var criadaEm = System.currentTimeMillis()

    // Rede e Wi-Fi
    var rede by mutableStateOf<RedeAtual?>(null); private set
    var wifi by mutableStateOf<List<RedeWifi>>(emptyList()); private set
    var wifiAtualizado by mutableStateOf(true); private set
    var buscandoWifi by mutableStateOf(false); private set
    var wifiBuscado by mutableStateOf(false); private set

    // Caça-sinal e ambientes
    var cacaSinal by mutableStateOf(false); private set
    var bipe by mutableStateOf(true)
    var historicoSinal by mutableStateOf<List<Int?>>(emptyList()); private set
    var ambientes by mutableStateOf<List<Ambiente>>(emptyList()); private set
    private var jobCaca: Job? = null
    private var jobBipe: Job? = null
    private var tom: ToneGenerator? = null

    // Testes
    var pings by mutableStateOf<List<ResultadoPing>>(emptyList()); private set
    var dns by mutableStateOf<List<ResultadoDns>>(emptyList()); private set
    var velocidade by mutableStateOf<ResultadoVelocidade?>(null); private set
    var velocidadeAoVivo by mutableStateOf<Double?>(null); private set
    var testando by mutableStateOf(false); private set
    var etapaTeste by mutableStateOf(""); private set
    var pingContinuo by mutableStateOf<PingContinuo?>(null); private set
    var pingContinuoAtivo by mutableStateOf(false); private set
    private var jobPing: Job? = null

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
    var fotos by mutableStateOf<List<Foto>>(emptyList()); private set
    var visitas by mutableStateOf<List<Visitas.Resumo>>(emptyList()); private set
    var mensagem by mutableStateOf<String?>(null)

    // Atualização do app
    var novaVersao by mutableStateOf<Atualizador.Versao?>(null); private set
    var baixandoAtualizacao by mutableStateOf(false); private set
    var progressoAtualizacao by mutableStateOf(0f); private set
    var precisaLiberarInstalacao by mutableStateOf(false); private set
    private var apkBaixado: File? = null

    init {
        viewModelScope.launch { novaVersao = Atualizador.verificar(contexto) }
        recarregarVisitas()
    }

    /** O que está acontecendo agora, para o selo do topo. */
    val situacao: String
        get() = when {
            varrendo -> "VARRENDO"
            testando -> "TESTANDO"
            cacaSinal -> "CAÇA-SINAL"
            pingContinuoAtivo -> "PING CONTÍNUO"
            else -> when (rede?.tipo) {
                "Wi-Fi" -> "WI-FI"; "Cabo" -> "CABO"; "Dados móveis" -> "DADOS MÓVEIS"; null -> "…"; else -> "SEM REDE"
            }
        }

    // ------------------------------------------------------------------ Rede
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
            salvar()
        }
    }

    fun melhorCanal24() = WifiAoRedor.melhorCanal24(wifi, rede?.bssid)

    /** Medidor ao vivo: lê o sinal a cada segundo; com o bipe, bipa mais rápido quanto melhor o sinal. */
    fun alternarCacaSinal() {
        if (cacaSinal) { pararCacaSinal(); return }
        cacaSinal = true
        historicoSinal = emptyList()
        jobCaca = viewModelScope.launch {
            while (isActive) {
                atualizarRede()
                historicoSinal = (historicoSinal + rede?.sinalDbm).takeLast(90)
                delay(1000)
            }
        }
        jobBipe = viewModelScope.launch(Dispatchers.Default) {
            while (isActive) {
                val dbm = rede?.sinalDbm
                if (bipe && dbm != null) {
                    if (tom == null) tom = runCatching { ToneGenerator(AudioManager.STREAM_MUSIC, 70) }.getOrNull()
                    tom?.startTone(ToneGenerator.TONE_PROP_BEEP, 60)
                }
                // -40 dBm ou melhor: 4 bipes por segundo; -90 dBm: um a cada 1,5 s.
                val f = (((dbm ?: -95) + 90) / 50f).coerceIn(0f, 1f)
                delay((1500 - 1250 * f).toLong())
            }
        }
    }

    fun pararCacaSinal() {
        jobCaca?.cancel(); jobBipe?.cancel()
        cacaSinal = false
        tom?.release(); tom = null
    }

    fun marcarAmbiente(nome: String) {
        if (nome.isBlank()) return
        atualizarRede()
        val r = rede
        ambientes = ambientes + Ambiente(nome.trim(), r?.ssid, r?.bssid, r?.sinalDbm, r?.canal, System.currentTimeMillis())
        salvar()
    }

    fun removerAmbiente(a: Ambiente) { ambientes = ambientes - a; salvar() }

    // ------------------------------------------------------------------ Testes
    fun rodarTestes() {
        if (testando) return
        testando = true
        pings = emptyList(); dns = emptyList(); velocidade = null; velocidadeAoVivo = null
        viewModelScope.launch {
            atualizarRede()
            for ((nome, alvo) in alvosPing()) {
                etapaTeste = "Ping: $nome…"
                pings = pings + Testes.ping(nome, alvo)
            }
            for (nome in listOf("google.com", "nexopulse.up.railway.app")) {
                etapaTeste = "DNS: $nome…"
                dns = dns + Testes.dns(nome)
            }
            velocidade = Testes.velocidade(progresso = { etapaTeste = it }, aoVivo = { velocidadeAoVivo = it })
            velocidadeAoVivo = null
            etapaTeste = ""
            testando = false
            salvar()
        }
    }

    fun alvosPing(): List<Pair<String, String>> = buildList {
        rede?.gateway?.let { add("Gateway" to it) }
        add("Cloudflare" to "1.1.1.1")
        add("Google" to "8.8.8.8")
    }

    fun alternarPingContinuo(nome: String, alvo: String) {
        if (pingContinuoAtivo) {
            jobPing?.cancel(); pingContinuoAtivo = false; salvar(); return
        }
        pingContinuoAtivo = true
        pingContinuo = PingContinuo(nome, alvo, System.currentTimeMillis(), emptyList())
        jobPing = viewModelScope.launch {
            while (isActive) {
                val inicio = System.currentTimeMillis()
                val t = Testes.pingUma(alvo)
                pingContinuo = pingContinuo?.let { it.copy(tempos = (it.tempos + t).takeLast(3600)) }
                delay((1000 - (System.currentTimeMillis() - inicio)).coerceAtLeast(50))
            }
        }
    }

    // ------------------------------------------------------------------ Varredura
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
            salvar()
        }
    }

    // ------------------------------------------------------------------ Fotos
    /** Arquivo onde a câmera grava a próxima foto (fica na pasta da visita). */
    fun novaFoto(): File = Visitas.foto(contexto, idVisita, "foto-${System.currentTimeMillis()}.jpg")

    fun fotoTirada(arquivo: File, ok: Boolean) {
        if (ok && arquivo.length() > 0) { fotos = fotos + Foto(arquivo.name); salvar() } else arquivo.delete()
    }

    fun arquivoDaFoto(f: Foto): File = Visitas.foto(contexto, idVisita, f.arquivo)

    fun legendar(f: Foto, legenda: String) { fotos = fotos.map { if (it == f) it.copy(legenda = legenda) else it } }

    fun removerFoto(f: Foto) { arquivoDaFoto(f).delete(); fotos = fotos - f; salvar() }

    // ------------------------------------------------------------------ Visitas
    fun levantamento() = Levantamento(
        id = idVisita, local = local.trim(), tecnico = tecnico.trim(), observacoes = observacoes.trim(), criadaEm = criadaEm,
        quando = System.currentTimeMillis(), versaoApp = versao, rede = rede, wifi = wifi, melhorCanal24 = melhorCanal24(),
        pings = pings, dns = dns, velocidade = velocidade, dispositivos = dispositivos, ambientes = ambientes,
        pingContinuo = pingContinuo, fotos = fotos,
    )

    fun salvar() {
        val l = levantamento()
        viewModelScope.launch(Dispatchers.IO) {
            runCatching { Visitas.salvar(contexto, l) }
            withContext(Dispatchers.Main) { recarregarVisitas() }
        }
    }

    private fun recarregarVisitas() {
        viewModelScope.launch { visitas = withContext(Dispatchers.IO) { Visitas.listar(contexto) } }
    }

    private fun parar() {
        pararCacaSinal(); jobPing?.cancel(); pingContinuoAtivo = false
    }

    fun novaVisita() {
        salvar(); parar()
        idVisita = UUID.randomUUID().toString(); criadaEm = System.currentTimeMillis()
        wifi = emptyList(); wifiBuscado = false; ambientes = emptyList(); historicoSinal = emptyList()
        pings = emptyList(); dns = emptyList(); velocidade = null; pingContinuo = null
        dispositivos = emptyList(); varreduraFeita = false
        local = ""; observacoes = ""; fotos = emptyList()
        atualizarRede()
        mensagem = "Visita nova começada. A anterior ficou salva."
    }

    fun abrirVisita(id: String) {
        if (id == idVisita) return
        salvar(); parar()
        viewModelScope.launch {
            val l = withContext(Dispatchers.IO) { Visitas.abrir(contexto, id, versao) }
            if (l == null) { mensagem = "Não foi possível abrir essa visita."; return@launch }
            idVisita = l.id; criadaEm = l.criadaEm
            local = l.local; tecnico = l.tecnico; observacoes = l.observacoes
            rede = l.rede; wifi = l.wifi; wifiBuscado = l.wifi.isNotEmpty(); ambientes = l.ambientes
            pings = l.pings; dns = l.dns; velocidade = l.velocidade; pingContinuo = l.pingContinuo
            dispositivos = l.dispositivos; varreduraFeita = l.dispositivos.isNotEmpty(); fotos = l.fotos
            mensagem = "Visita aberta: ${l.local.ifBlank { "sem nome" }}. Para medir de novo, use as abas."
        }
    }

    fun excluirVisita(id: String) {
        viewModelScope.launch {
            withContext(Dispatchers.IO) { Visitas.excluir(contexto, id) }
            if (id == idVisita) { idVisita = UUID.randomUUID().toString(); fotos = emptyList() }
            recarregarVisitas()
        }
    }

    fun exportar(pdf: Boolean, json: Boolean) {
        viewModelScope.launch {
            val l = levantamento()
            val arquivos = withContext(Dispatchers.IO) {
                runCatching { Visitas.salvar(contexto, l) }
                buildList {
                    if (pdf) add(Exportar.pdf(contexto, l))
                    if (json) add(Exportar.json(contexto, l))
                }
            }
            recarregarVisitas()
            runCatching { Exportar.compartilhar(contexto, arquivos) }
                .onFailure { mensagem = "Não foi possível abrir o envio: ${it.message}" }
        }
    }

    // ------------------------------------------------------------------ Atualização
    fun atualizar() {
        val v = novaVersao ?: return
        apkBaixado?.let { if (it.exists()) { instalarBaixado(); return } }
        if (baixandoAtualizacao) return
        baixandoAtualizacao = true; progressoAtualizacao = 0f
        viewModelScope.launch {
            try {
                apkBaixado = Atualizador.baixar(contexto, v) { p -> progressoAtualizacao = p }
                instalarBaixado()
            } catch (e: Exception) {
                mensagem = "Não foi possível baixar a versão nova: ${e.message}"
            } finally {
                baixandoAtualizacao = false
            }
        }
    }

    private fun instalarBaixado() {
        val apk = apkBaixado ?: return
        if (!Atualizador.podeInstalar(contexto)) { precisaLiberarInstalacao = true; return }
        precisaLiberarInstalacao = false
        runCatching { Atualizador.instalar(contexto, apk) }.onFailure { mensagem = "Não foi possível abrir a instalação: ${it.message}" }
    }

    fun liberarInstalacao() = Atualizador.abrirPermissao(contexto)

    fun aoVoltar() {
        if (precisaLiberarInstalacao && Atualizador.podeInstalar(contexto)) instalarBaixado()
    }

    override fun onCleared() {
        parar()
        super.onCleared()
    }
}
