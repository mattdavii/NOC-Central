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
import br.eng.mdsolucoes.nexocampo.rede.AparelhoSensor
import br.eng.mdsolucoes.nexocampo.rede.DadosSensor
import br.eng.mdsolucoes.nexocampo.rede.SensorLocal
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
    /** O que a varredura do celular achou, já completado com o que o sensor do local conhece. */
    var dispositivos by mutableStateOf<List<Dispositivo>>(emptyList()); private set
    private var dispositivosApp: List<Dispositivo> = emptyList()

    // Sensor NexoPulse do local (painel local, sem internet)
    private val prefsPainel = app.getSharedPreferences("painel_sensor", android.content.Context.MODE_PRIVATE)
    var sensorDados by mutableStateOf<DadosSensor?>(null); private set
    var conectandoSensor by mutableStateOf(false); private set
    val painelUsuarioSalvo: String get() = prefsPainel.getString("usuario", "") ?: ""
    val painelSenhaSalva: String get() = prefsPainel.getString("senha", "") ?: ""
    /** IP do sensor achado na varredura (painel na porta 10000). */
    val sensorAchado: String? get() = dispositivos.firstOrNull { it.tipo == "Sensor NexoPulse" && !it.foraDaRede && 10000 in it.portas }?.ip
        ?: dispositivos.firstOrNull { 10000 in it.portas }?.ip
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

    // Central (NexoHub)
    private val central = CentralApi(app)
    var contaCentral by mutableStateOf(central.nome?.takeIf { central.token != null }); private set
    var precisaCodigo by mutableStateOf(false); private set
    var entrando by mutableStateOf(false); private set
    var empresas by mutableStateOf<List<CentralApi.Empresa>>(emptyList()); private set
    var empresaEscolhida by mutableStateOf<Int?>(null)
    var localEscolhido by mutableStateOf<String?>(null)
    var chamadoEscolhido by mutableStateOf<Int?>(null)
    var enviando by mutableStateOf(false); private set
    var numeroNaCentral by mutableStateOf<Int?>(null); private set

    // Atualização do app
    var novaVersao by mutableStateOf<Atualizador.Versao?>(null); private set
    var baixandoAtualizacao by mutableStateOf(false); private set
    var progressoAtualizacao by mutableStateOf(0f); private set
    var precisaLiberarInstalacao by mutableStateOf(false); private set
    private var apkBaixado: File? = null

    private var ultimaVerificacao = 0L

    /** Procura versão nova ao abrir e ao voltar para o app (no máximo a cada 2 minutos). */
    fun verificarAtualizacao() {
        val agora = System.currentTimeMillis()
        if (agora - ultimaVerificacao < 120_000 || baixandoAtualizacao) return
        ultimaVerificacao = agora
        viewModelScope.launch {
            val v = Atualizador.verificar(contexto)
            if (v != null) novaVersao = v
            else ultimaVerificacao = agora - 90_000   // falhou ou já em dia: tenta de novo em 30 s
        }
    }

    init {
        verificarAtualizacao()
        recarregarVisitas()
        if (contaCentral != null) carregarEmpresas()
    }

    // ------------------------------------------------------------------ Central
    fun entrarNaCentral(usuario: String, senha: String, codigo: String?) {
        if (entrando) return
        entrando = true
        viewModelScope.launch {
            when (val r = central.entrar(usuario, senha, codigo)) {
                is CentralApi.Entrada.Ok -> { contaCentral = r.nome; precisaCodigo = false; carregarEmpresas() }
                is CentralApi.Entrada.PrecisaCodigo -> { precisaCodigo = true; mensagem = r.mensagem }
                is CentralApi.Entrada.Erro -> mensagem = r.mensagem
            }
            entrando = false
        }
    }

    fun sairDaCentral() {
        viewModelScope.launch { central.sair(); contaCentral = null; empresas = emptyList(); empresaEscolhida = null }
    }

    fun carregarEmpresas() {
        viewModelScope.launch {
            try {
                empresas = central.empresas()
            } catch (e: CentralApi.Falha) {
                if (e.sairDaConta) contaCentral = null
                mensagem = e.message
            }
        }
    }

    fun escolherEmpresa(id: Int?) { empresaEscolhida = id; localEscolhido = null; chamadoEscolhido = null }

    fun enviarParaCentral() {
        val empresa = empresaEscolhida ?: run { mensagem = "Escolha a empresa da visita."; return }
        if (enviando) return
        enviando = true
        viewModelScope.launch {
            try {
                val l = levantamento()
                val pdf = withContext(Dispatchers.IO) { Visitas.salvar(contexto, l); Exportar.pdf(contexto, l) }
                numeroNaCentral = central.enviar(l, pdf, empresa, localEscolhido, chamadoEscolhido)
                mensagem = "Visita enviada para a Central (nº $numeroNaCentral)" + (chamadoEscolhido?.let { " e anexada ao chamado #$it" } ?: "") + "."
            } catch (e: CentralApi.Falha) {
                if (e.sairDaConta) contaCentral = null
                mensagem = e.message
            } catch (e: Exception) {
                mensagem = "Não foi possível enviar: ${e.message}"
            } finally {
                enviando = false
            }
        }
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
        varrendo = true; progresso = 0f; dispositivosApp = emptyList(); dispositivos = mesclar(emptyList(), sensorDados)
        viewModelScope.launch {
            val lista = Varredura(contexto).rodar(r) { p, etapa, parcial ->
                viewModelScope.launch(Dispatchers.Main) { progresso = p; etapaVarredura = etapa; dispositivosApp = parcial; dispositivos = mesclar(parcial, sensorDados) }
            }
            dispositivosApp = lista; dispositivos = mesclar(lista, sensorDados); progresso = 1f; etapaVarredura = ""
            varrendo = false; varreduraFeita = true
            salvar()
        }
    }

    // ------------------------------------------------------------------ Sensor do local
    fun conectarSensor(ip: String, usuario: String, senha: String, lembrar: Boolean) {
        if (conectandoSensor || ip.isBlank()) return
        conectandoSensor = true
        viewModelScope.launch {
            try {
                val painel = SensorLocal(ip.trim())
                painel.entrar(usuario.trim(), senha)
                val d = painel.ler()
                if (lembrar) prefsPainel.edit().putString("usuario", usuario.trim()).putString("senha", senha).apply()
                else prefsPainel.edit().clear().apply()
                sensorDados = d
                dispositivos = mesclar(dispositivosApp, d)
                mensagem = "Dados do sensor${d.nome?.let { " $it" } ?: ""}: ${d.aparelhos.size} aparelhos, ${d.quedas} queda(s) em ${d.dias} dias."
                salvar()
            } catch (e: SensorLocal.Falha) {
                mensagem = e.message
            } finally {
                conectandoSensor = false
            }
        }
    }

    fun desconectarSensor() {
        sensorDados = null
        dispositivos = mesclar(dispositivosApp, null)
        salvar()
    }

    /**
     * Junta a varredura do celular com o inventário do sensor: pelo IP (ou MAC), o aparelho ganha MAC, marca do
     * registro IEEE, modelo e o apelido dado na Central; os que só o sensor conhece entram também (os que saíram
     * da rede, marcados como fora da rede). O tipo do celular só é trocado quando ele não sabia dizer.
     */
    private fun mesclar(app: List<Dispositivo>, s: DadosSensor?): List<Dispositivo> {
        if (s == null) return app.filter { "sensor" !in it.origens || it.origens.size > 1 }.map { it.copy(origens = it.origens - "sensor") }
        val fracos = setOf("Não identificado", "Aparelho com página web", "Servidor / Linux")
        val usados = mutableSetOf<AparelhoSensor>()
        val juntos = app.map { d ->
            val a = s.aparelhos.firstOrNull { it.ip == d.ip } ?: s.aparelhos.firstOrNull { it.mac != null && it.mac.equals(d.mac, true) }
            if (a == null) d else {
                usados += a
                d.copy(
                    mac = d.mac ?: a.mac, fabricante = a.marca ?: d.fabricante, modelo = d.modelo ?: a.modelo,
                    nome = a.apelido ?: d.nome ?: a.nome,
                    tipo = if (d.tipo in fracos) SensorLocal.tipoDoApp(a) ?: d.tipo else d.tipo,
                    origens = (d.origens + "sensor").distinct(),
                )
            }
        }
        val ips = juntos.map { it.ip }.toSet()
        val extras = s.aparelhos.filter { it !in usados && it.ip != null && it.ip !in ips }.map { a ->
            Dispositivo(
                ip = a.ip!!, tipo = SensorLocal.tipoDoApp(a) ?: a.tipo ?: "Não identificado", nome = a.apelido ?: a.nome,
                fabricante = a.marca, modelo = a.modelo, portas = emptyList(), servicos = emptyList(), mac = a.mac,
                origens = listOf("sensor"), foraDaRede = a.status == "offline",
            )
        }
        return (juntos + extras).sortedBy { d -> d.ip.split(".").fold(0L) { acc, p -> acc * 256 + (p.toLongOrNull() ?: 0) } }
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
        pings = pings, dns = dns, velocidade = velocidade, dispositivos = dispositivos, ambientes = ambientes, sensor = sensorDados,
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
        dispositivos = emptyList(); dispositivosApp = emptyList(); sensorDados = null; varreduraFeita = false
        local = ""; observacoes = ""; fotos = emptyList(); numeroNaCentral = null
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
            dispositivos = l.dispositivos; dispositivosApp = l.dispositivos; sensorDados = l.sensor
            varreduraFeita = l.dispositivos.isNotEmpty(); fotos = l.fotos
            numeroNaCentral = central.numeroEnviado(l.id)
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
