package br.eng.mdsolucoes.nexocampo.ui

import android.graphics.BitmapFactory
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Computer
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.HelpOutline
import androidx.compose.material.icons.filled.Language
import androidx.compose.material.icons.filled.Memory
import androidx.compose.material.icons.filled.Print
import androidx.compose.material.icons.filled.Radar
import androidx.compose.material.icons.filled.Router
import androidx.compose.material.icons.filled.Sensors
import androidx.compose.material.icons.filled.SettingsInputAntenna
import androidx.compose.material.icons.filled.Smartphone
import androidx.compose.material.icons.filled.Speed
import androidx.compose.material.icons.filled.Storage
import androidx.compose.material.icons.filled.Tv
import androidx.compose.material.icons.filled.Videocam
import androidx.compose.material.icons.filled.WbSunny
import androidx.compose.material.icons.filled.Wifi
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.FileProvider
import br.eng.mdsolucoes.nexocampo.CampoViewModel
import br.eng.mdsolucoes.nexocampo.R
import br.eng.mdsolucoes.nexocampo.rede.Dispositivo
import br.eng.mdsolucoes.nexocampo.rede.Foto
import br.eng.mdsolucoes.nexocampo.rede.faixaDe
import br.eng.mdsolucoes.nexocampo.rede.qualidadeSinal
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

private data class Aba(val nome: String, val icone: ImageVector)

private val ABAS = listOf(
    Aba("Rede", Icons.Filled.Wifi), Aba("Testes", Icons.Filled.Speed),
    Aba("Varredura", Icons.Filled.Radar), Aba("Relatório", Icons.Filled.Description),
)

private fun f1(v: Double?) = v?.let { "%.1f".format(it).replace('.', ',') }
private fun f0(v: Double?) = v?.let { "%.0f".format(it) }

@Composable
fun App(vm: CampoViewModel, permissoesOk: Boolean, pedirPermissoes: () -> Unit) {
    var aba by rememberSaveable { mutableIntStateOf(0) }
    val avisos = remember { SnackbarHostState() }
    LaunchedEffect(vm.mensagem) {
        vm.mensagem?.let { avisos.showSnackbar(it); vm.mensagem = null }
    }
    LaunchedEffect(Unit) { vm.atualizarRede() }
    Scaffold(
        containerColor = Color.Transparent,
        modifier = Modifier.background(Cor.fundo).grade(),
        snackbarHost = { SnackbarHost(avisos) },
        topBar = {
            // Android 15 desenha o app atrás da barra de status (hora, bateria): o topo desce até abaixo dela.
            Column(Modifier.background(Cor.fundo.copy(alpha = 0.97f)).statusBarsPadding().padding(start = 18.dp, end = 18.dp, top = 10.dp, bottom = 10.dp)) {
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Image(painterResource(R.drawable.logo_nexocampo), contentDescription = "NexoCampo", modifier = Modifier.height(28.dp))
                    Spacer(Modifier.weight(1f))
                    val s = vm.situacao
                    Selo(s, when (s) {
                        "WI-FI", "CABO" -> Cor.ok
                        "DADOS MÓVEIS", "SEM REDE" -> Cor.atencao
                        "…" -> Cor.suave
                        else -> Cor.ciano
                    })
                }
                AvisoAtualizacao(vm)
            }
        },
        bottomBar = {
            NavigationBar(containerColor = Cor.fundo, tonalElevation = 0.dp) {
                ABAS.forEachIndexed { i, a ->
                    NavigationBarItem(
                        selected = aba == i, onClick = { aba = i },
                        icon = { Icon(a.icone, contentDescription = null) }, label = { Text(a.nome, fontSize = 11.sp) },
                        colors = NavigationBarItemDefaults.colors(
                            selectedIconColor = Cor.texto, indicatorColor = Cor.ciano.copy(alpha = 0.24f),
                            selectedTextColor = Cor.texto, unselectedIconColor = Cor.suave, unselectedTextColor = Cor.suave,
                        ),
                    )
                }
            }
        },
    ) { margens ->
        Box(Modifier.padding(margens).fillMaxSize()) {
            when (aba) {
                0 -> TelaRede(vm, permissoesOk, pedirPermissoes)
                1 -> TelaTestes(vm)
                2 -> TelaVarredura(vm)
                else -> TelaRelatorio(vm)
            }
        }
    }
}

@Composable
private fun Pagina(conteudo: LazyListScope.() -> Unit) {
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 4.dp, bottom = 24.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
        content = conteudo,
    )
}

@Composable
private fun campoCores() = OutlinedTextFieldDefaults.colors(
    focusedBorderColor = Cor.ciano, unfocusedBorderColor = Cor.borda, focusedLabelColor = Cor.ciano, unfocusedLabelColor = Cor.suave,
    cursorColor = Cor.ciano, focusedContainerColor = Cor.cartao2, unfocusedContainerColor = Cor.cartao2,
)

// ------------------------------------------------------------------ Rede
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun TelaRede(vm: CampoViewModel, permissoesOk: Boolean, pedirPermissoes: () -> Unit) {
    val r = vm.rede
    var faixa by rememberSaveable { mutableStateOf("2,4 GHz") }
    var nomeAmbiente by rememberSaveable { mutableStateOf("") }
    Pagina {
        if (!permissoesOk) item {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Aviso("O Android só mostra o nome do Wi-Fi e as redes ao redor com a permissão de localização.")
                BotaoSecundario("Dar permissão", Modifier.fillMaxWidth(), aoClicar = pedirPermissoes)
            }
        }
        vm.sensorDados?.takeIf { it.quedas > 0 }?.let { sd ->
            item {
                Aviso("O sensor deste local registrou ${sd.quedas} queda(s) nos últimos ${sd.dias} dias. Detalhes na aba Varredura.", Cor.atencao)
            }
        }
        item {
            Painel {
                MedidorSinal(if (r != null && r.tipo == "Wi-Fi") r.sinalDbm else null)
                Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(r?.ssid ?: (if (r?.tipo == "Wi-Fi") "Wi-Fi (nome oculto)" else r?.tipo ?: "Lendo…"),
                        fontSize = 20.sp, fontWeight = FontWeight.Bold)
                    val detalhe = listOfNotNull(r?.bssid, r?.padrao).joinToString(" · ")
                    if (detalhe.isNotBlank()) Rotulo(detalhe, modifier = Modifier.padding(top = 3.dp))
                    if (r != null && r.tipo == "Wi-Fi" && r.ssid == null) {
                        Text("Ligue a localização do celular para ver o nome da rede.", color = Cor.atencao, fontSize = 12.sp,
                            modifier = Modifier.padding(top = 4.dp))
                    }
                }
                GradeLeituras(buildList {
                    if (r != null && r.tipo == "Wi-Fi") {
                        add("CANAL" to r.canal?.let { "$it · ${r.frequenciaMhz?.let(::faixaDe) ?: ""}" })
                        add("LINK" to r.velocidadeMbps?.let { "$it Mbps" })
                    }
                    add("IP" to r?.ip?.let { "$it/${r?.prefixo ?: "?"}" })
                    add("GATEWAY" to r?.gateway)
                    if (r != null && r.tipo == "Wi-Fi") add("SEGURANÇA" to r.seguranca)
                    add("DNS" to r?.dns?.firstOrNull())
                })
            }
        }
        // Caça-sinal e mapa de cobertura
        item {
            Painel {
                CabecaPainel("Caça-sinal", if (vm.cacaSinal) "AO VIVO" else null)
                Text("Ande pelo local olhando o medidor: o bipe acelera quando o sinal melhora. Marque cada ambiente para o mapa de cobertura.",
                    color = Cor.suave, fontSize = 13.sp, lineHeight = 18.sp)
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    BotaoSecundario(if (vm.cacaSinal) "Parar" else "Começar", Modifier.weight(1f), ativo = r?.tipo == "Wi-Fi",
                        cor = if (vm.cacaSinal) Cor.atencao else Cor.texto) { vm.alternarCacaSinal() }
                    Rotulo("BIPE")
                    Switch(vm.bipe, { vm.bipe = it }, colors = SwitchDefaults.colors(checkedTrackColor = Cor.ciano, checkedThumbColor = Cor.fundo,
                        uncheckedTrackColor = Cor.cartao2, uncheckedBorderColor = Cor.borda, uncheckedThumbColor = Cor.suave))
                }
                if (vm.historicoSinal.size > 1) {
                    Serie(vm.historicoSinal.map { it?.toDouble() }, corDoSinal(r?.sinalDbm), Modifier.fillMaxWidth().height(56.dp),
                        minimo = -95.0, maximo = -30.0, area = true)
                    Row { Rotulo("ÚLTIMOS ${vm.historicoSinal.size} s", modifier = Modifier.weight(1f)); Rotulo("−95 a −30 dBm") }
                }
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(nomeAmbiente, { nomeAmbiente = it }, label = { Text("Ambiente (ex.: Portaria)") }, singleLine = true,
                        modifier = Modifier.weight(1f), colors = campoCores(), shape = RoundedCornerShape(12.dp))
                    BotaoSecundario("Marcar", ativo = nomeAmbiente.isNotBlank() && r?.tipo == "Wi-Fi") {
                        vm.marcarAmbiente(nomeAmbiente); nomeAmbiente = ""
                    }
                }
                vm.ambientes.forEach { a ->
                    Row(Modifier.fillMaxWidth().background(Cor.cartao2, RoundedCornerShape(10.dp)).padding(horizontal = 12.dp, vertical = 9.dp),
                        verticalAlignment = Alignment.CenterVertically) {
                        BarrasSinal(a.sinalDbm)
                        Spacer(Modifier.width(10.dp))
                        Column(Modifier.weight(1f)) {
                            Text(a.nome, fontWeight = FontWeight.Medium, fontSize = 14.sp)
                            Rotulo(listOfNotNull(a.ssid, a.canal?.let { "CH $it" }, qualidadeSinal(a.sinalDbm).uppercase()).joinToString(" · "))
                        }
                        Text(a.sinalDbm?.let { "$it dBm" } ?: "—", fontFamily = Mono, fontWeight = FontWeight.Bold, color = corDoSinal(a.sinalDbm), fontSize = 14.sp)
                        Icon(Icons.Filled.Close, "Remover", tint = Cor.apagado, modifier = Modifier.padding(start = 8.dp).size(18.dp).clickable { vm.removerAmbiente(a) })
                    }
                }
            }
        }
        // Espectro do Wi-Fi
        item {
            Painel {
                CabecaPainel("Espectro Wi-Fi", if (vm.wifiBuscado) "${vm.wifi.size} REDES" else null)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    listOf("2,4 GHz", "5 GHz", "6 GHz").filter { it != "6 GHz" || vm.wifi.any { w -> w.faixa == "6 GHz" } }.forEach { nome ->
                        Chip(nome, selecionado = faixa == nome, numero = vm.wifi.count { it.faixa == nome }.takeIf { vm.wifiBuscado }?.toString()) { faixa = nome }
                    }
                }
                if (vm.wifi.isNotEmpty()) Espectro(vm.wifi, faixa)
                BotaoPrincipal(if (vm.buscandoWifi) "Buscando redes…" else if (vm.wifiBuscado) "Buscar de novo" else "Buscar redes",
                    ativo = !vm.buscandoWifi && permissoesOk) { vm.buscarWifi() }
                if (vm.wifiBuscado && !vm.wifiAtualizado) {
                    Aviso("O Android limita as buscas de Wi-Fi (4 a cada 2 minutos). Mostrando a última busca; tente de novo em instantes.")
                }
                if (faixa == "2,4 GHz") vm.melhorCanal24()?.let { (canal, motivo) ->
                    Row(Modifier.fillMaxWidth().background(Cor.ok.copy(alpha = 0.08f), RoundedCornerShape(12.dp))
                        .border(1.dp, Cor.ok.copy(alpha = 0.25f), RoundedCornerShape(12.dp)).padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text("Canal menos disputado", fontSize = 13.sp)
                            Rotulo(motivo)
                        }
                        Text("CH $canal", fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 18.sp, color = Cor.ok)
                    }
                }
            }
        }
        items(vm.wifi.filter { it.faixa == faixa }, key = { it.bssid + it.frequenciaMhz }) { w ->
            Row(
                Modifier.fillMaxWidth().background(Cor.cartao, RoundedCornerShape(14.dp))
                    .border(1.dp, if (w.conectada) Cor.ciano.copy(alpha = 0.5f) else Cor.borda, RoundedCornerShape(14.dp))
                    .padding(horizontal = 14.dp, vertical = 11.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                BarrasSinal(w.sinalDbm)
                Spacer(Modifier.width(12.dp))
                Column(Modifier.weight(1f)) {
                    Text(w.ssid, color = if (w.conectada) Cor.ciano else Cor.texto, fontWeight = FontWeight.Medium, fontSize = 14.sp)
                    Rotulo("CH ${w.canal} · ${w.larguraMhz} MHz · ${w.seguranca.uppercase()}")
                }
                Text("${w.sinalDbm}", fontFamily = Mono, fontWeight = FontWeight.Bold, color = corDoSinal(w.sinalDbm), fontSize = 15.sp)
            }
        }
    }
}

// ------------------------------------------------------------------ Testes
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun TelaTestes(vm: CampoViewModel) {
    val v = vm.velocidade
    val alvos = vm.alvosPing()
    var alvo by rememberSaveable { mutableIntStateOf(0) }
    Pagina {
        item {
            Painel {
                CabecaPainel("Velocidade", "CLOUDFLARE · 4 CONEXÕES")
                val aoVivo = vm.velocidadeAoVivo
                val subindo = vm.testando && vm.etapaTeste.contains("upload")
                Row(Modifier.fillMaxWidth()) {
                    NumeroGrande("↓ DOWNLOAD", if (vm.testando && !subindo && aoVivo != null) f0(aoVivo) else f0(v?.downloadMbps), "Mbps", Modifier.weight(1f))
                    NumeroGrande("↑ UPLOAD", if (subindo && aoVivo != null) f0(aoVivo) else f0(v?.uploadMbps), "Mbps", Modifier.weight(1f))
                }
                if ((v?.amostrasDownload?.size ?: 0) > 2) {
                    Serie(v!!.amostrasDownload, Cor.ciano, Modifier.fillMaxWidth().height(56.dp), minimo = 0.0, area = true)
                }
                if (v?.latenciaMs != null) Rotulo("RESPOSTA DO SERVIDOR ${f0(v.latenciaMs)} ms")
                if (v?.erro != null && v.downloadMbps == null) Aviso("Não deu para medir: ${v.erro}", Cor.critico)
                if (vm.testando) {
                    LinearProgressIndicator(Modifier.fillMaxWidth(), color = Cor.ciano, trackColor = Cor.borda)
                    Rotulo(vm.etapaTeste.uppercase())
                }
                BotaoPrincipal(if (vm.testando) "Testando…" else if (vm.pings.isEmpty()) "Rodar os testes" else "Rodar de novo", ativo = !vm.testando) { vm.rodarTestes() }
            }
        }
        if (vm.pings.isNotEmpty()) item {
            Painel {
                CabecaPainel("Ping", "5 PACOTES")
                vm.pings.forEach { p ->
                    val cor = corDoPing(p.mediaMs, p.perdaPct)
                    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(vertical = 2.dp)) {
                        Column(Modifier.weight(1f)) {
                            Text(p.nome, fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                            Rotulo("${p.alvo} · PERDA ${p.perdaPct}%")
                        }
                        Serie(p.tempos.map { it }, cor, Modifier.width(80.dp).height(24.dp))
                        Text(p.mediaMs?.let { "${f0(it)} ms" } ?: "sem resposta", fontFamily = Mono, fontWeight = FontWeight.Bold, color = cor,
                            fontSize = 15.sp, modifier = Modifier.width(78.dp), textAlign = androidx.compose.ui.text.style.TextAlign.End)
                    }
                }
            }
        }
        if (vm.dns.isNotEmpty()) item {
            Painel {
                CabecaPainel("DNS")
                vm.dns.forEach { d ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(d.nome, fontSize = 14.sp, modifier = Modifier.weight(1f))
                        Text(d.ms?.let { "$it ms" } ?: "não resolveu", fontFamily = Mono, fontWeight = FontWeight.Bold,
                            color = if (d.ms == null) Cor.critico else if (d.ms > 200) Cor.atencao else Cor.ok, fontSize = 14.sp)
                    }
                }
            }
        }
        item {
            val c = vm.pingContinuo
            Painel {
                CabecaPainel("Ping contínuo", if (vm.pingContinuoAtivo) "AO VIVO" else c?.let { "${it.enviados} s" })
                Text("Uma resposta por segundo, para pegar a instabilidade que aparece de vez em quando.", color = Cor.suave, fontSize = 13.sp)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    alvos.forEachIndexed { i, (nome, _) ->
                        Chip(nome, selecionado = alvo == i) { if (!vm.pingContinuoAtivo) alvo = i }
                    }
                }
                if (c != null) {
                    Row(verticalAlignment = Alignment.Bottom) {
                        val ultimo = c.tempos.lastOrNull()
                        Text(if (c.tempos.isEmpty()) "—" else ultimo?.let { f0(it) } ?: "×", fontFamily = Mono, fontWeight = FontWeight.Bold,
                            fontSize = 34.sp, color = corDoPing(ultimo))
                        Text(" ms · ${c.alvo}", fontFamily = Mono, fontSize = 13.sp, color = Cor.suave, modifier = Modifier.padding(bottom = 6.dp))
                    }
                    Serie(c.tempos.takeLast(120), Cor.ciano, Modifier.fillMaxWidth().height(64.dp), minimo = 0.0, area = true)
                    GradeLeituras(listOf("PERDA" to "${c.perdaPct}% (${c.perdidos})", "MÉDIA" to c.mediaMs?.let { "${f0(it)} ms" },
                        "MÁXIMA" to c.maxMs?.let { "${f0(it)} ms" }, "OSCILAÇÃO" to c.jitterMs?.let { "${f1(it)} ms" }))
                }
                val (nome, ip) = alvos.getOrElse(alvo) { alvos.first() }
                BotaoSecundario(if (vm.pingContinuoAtivo) "Parar" else "Começar ping até $nome", Modifier.fillMaxWidth(),
                    cor = if (vm.pingContinuoAtivo) Cor.atencao else Cor.texto) { vm.alternarPingContinuo(nome, ip) }
            }
        }
    }
}

@Composable
private fun NumeroGrande(rotulo: String, valor: String?, unidade: String, modifier: Modifier = Modifier) {
    Column(modifier) {
        Rotulo(rotulo)
        Row(verticalAlignment = Alignment.Bottom) {
            Text(valor ?: "—", fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 38.sp, lineHeight = 40.sp)
            Text(" $unidade", fontFamily = Mono, fontSize = 13.sp, color = Cor.suave, modifier = Modifier.padding(bottom = 6.dp))
        }
    }
}

// ------------------------------------------------------------------ Varredura
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun TelaVarredura(vm: CampoViewModel) {
    val r = vm.rede
    val total = vm.enderecosParaVarrer()
    Pagina {
        item {
            Painel {
                Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) { Radar(vm.dispositivos, vm.varrendo) }
                val rede = r?.ip?.let { ip -> ip.substringBeforeLast('.') + ".0/" + ((r?.prefixo ?: 24).coerceIn(24, 30)) } ?: "—"
                Row { Rotulo(rede, modifier = Modifier.weight(1f)); Rotulo(if (vm.varrendo) "${(vm.progresso * total).toInt()} / $total" else "${vm.dispositivos.size} APARELHOS") }
                Box(Modifier.fillMaxWidth().height(4.dp).clip(RoundedCornerShape(2.dp)).background(Cor.borda)) {
                    Box(Modifier.fillMaxWidth(if (vm.varreduraFeita && !vm.varrendo) 1f else vm.progresso).height(4.dp).background(Cor.gradiente))
                }
                if (vm.varrendo) Rotulo(vm.etapaVarredura.uppercase())
                BotaoPrincipal(if (vm.varrendo) "Varrendo…" else if (vm.varreduraFeita) "Varrer de novo" else "Varrer a rede ($total endereços)",
                    ativo = !vm.varrendo) { vm.rodarVarredura() }
            }
        }
        item { PainelSensor(vm) }
        if (vm.dispositivos.isNotEmpty()) item {
            val porTipo = vm.dispositivos.groupingBy { it.tipo }.eachCount().entries.sortedByDescending { it.value }
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                porTipo.forEach { Chip(it.key.lowercase(), numero = "${it.value}") }
            }
        }
        if (vm.varreduraFeita && vm.dispositivos.size <= 1) item {
            Aviso("Só o celular apareceu. A rede pode isolar os aparelhos do Wi-Fi (isolamento de clientes, comum em Wi-Fi de visitantes).")
        }
        items(vm.dispositivos, key = { it.ip }) { d -> ItemDispositivo(d) }
        if (vm.dispositivos.isNotEmpty()) item {
            Rotulo("SEM O MAC (BLOQUEADO PELO ANDROID), TIPO E MARCA VÊM DAS PORTAS, ONVIF, UPNP, MDNS E DA PÁGINA DE CADA APARELHO.",
                modifier = Modifier.padding(top = 4.dp))
        }
    }
}

private fun iconeDo(tipo: String): ImageVector = when (tipo) {
    "Este celular" -> Icons.Filled.Smartphone
    "Roteador / gateway", "Roteador", "MikroTik" -> Icons.Filled.Router
    "Câmera / DVR / NVR" -> Icons.Filled.Videocam
    "Impressora" -> Icons.Filled.Print
    "Computador", "Servidor / Linux" -> Icons.Filled.Computer
    "TV / mídia" -> Icons.Filled.Tv
    "Inversor / datalogger solar" -> Icons.Filled.WbSunny
    "Equipamento industrial (Modbus)", "Automação / IoT" -> Icons.Filled.Memory
    "Rádio / ponto de acesso", "Starlink" -> Icons.Filled.SettingsInputAntenna
    "Armazenamento (NAS)" -> Icons.Filled.Storage
    "Sensor NexoPulse" -> Icons.Filled.Sensors
    "Aparelho com página web" -> Icons.Filled.Language
    else -> Icons.Filled.HelpOutline
}

@Composable
private fun ItemDispositivo(d: Dispositivo) {
    var aberto by remember { mutableStateOf(false) }
    val cor = corDoTipo(d.tipo)
    Column(
        Modifier.fillMaxWidth().background(Cor.cartao, RoundedCornerShape(14.dp)).border(1.dp, Cor.borda, RoundedCornerShape(14.dp))
            .clickable { aberto = !aberto }.padding(12.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(40.dp).background(cor.copy(alpha = 0.12f), RoundedCornerShape(12.dp)), contentAlignment = Alignment.Center) {
                Icon(iconeDo(d.tipo), contentDescription = null, tint = cor, modifier = Modifier.size(22.dp))
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                val titulo = listOfNotNull(d.tipo, d.fabricante?.takeIf { it !in d.tipo }).joinToString(" · ")
                Text(titulo, fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                val detalhe = listOfNotNull(d.modelo, d.nome).distinct().joinToString(" · ")
                if (detalhe.isNotBlank()) Text(detalhe, color = Cor.suave, fontSize = 12.sp)
            }
            Column(horizontalAlignment = Alignment.End) {
                Text(".${d.ip.substringAfterLast('.')}", fontFamily = Mono, fontWeight = FontWeight.Bold,
                    color = if (d.foraDaRede) Cor.apagado else Cor.ciano, fontSize = 14.sp)
                when {
                    d.foraDaRede -> Rotulo("FORA DA REDE", Cor.atencao)
                    d.portas.isNotEmpty() -> Rotulo(d.portas.take(3).joinToString(" "))
                    "sensor" in d.origens -> Rotulo("VIA SENSOR", Cor.ciano)
                }
            }
        }
        if (aberto) {
            Spacer(Modifier.height(10.dp))
            GradeLeituras(listOfNotNull("IP" to d.ip, "PORTAS" to d.portas.joinToString(", ").ifBlank { "nenhuma" },
                d.mac?.let { "MAC" to it }, "ACHADO POR" to d.origens.joinToString(", ").ifBlank { null }))
            d.servicos.forEach { Text("• $it", color = Cor.suave, fontSize = 12.sp, modifier = Modifier.padding(top = 4.dp)) }
        }
    }
}

// ------------------------------------------------------------------ Relatório
@Composable
private fun TelaRelatorio(vm: CampoViewModel) {
    val contexto = LocalContext.current
    var fotoPendente by remember { mutableStateOf<File?>(null) }
    val camera = rememberLauncherForActivityResult(ActivityResultContracts.TakePicture()) { ok ->
        fotoPendente?.let { vm.fotoTirada(it, ok) }; fotoPendente = null
    }
    Pagina {
        item {
            Painel {
                CabecaPainel("Visita", SimpleDateFormat("dd/MM HH:mm", Locale("pt", "BR")).format(Date()))
                OutlinedTextField(vm.local, { vm.local = it }, label = { Text("Local") }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                    colors = campoCores(), shape = RoundedCornerShape(12.dp))
                OutlinedTextField(vm.tecnico, { vm.tecnico = it }, label = { Text("Técnico") }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                    colors = campoCores(), shape = RoundedCornerShape(12.dp))
                OutlinedTextField(vm.observacoes, { vm.observacoes = it }, label = { Text("Observações") }, minLines = 3, modifier = Modifier.fillMaxWidth(),
                    colors = campoCores(), shape = RoundedCornerShape(12.dp))
            }
        }
        item {
            Painel {
                CabecaPainel("Fotos", if (vm.fotos.isNotEmpty()) "${vm.fotos.size}" else null)
                vm.fotos.chunked(3).forEach { linha ->
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        linha.forEach { f -> Miniatura(vm, f, Modifier.weight(1f)) }
                        repeat(3 - linha.size) { Spacer(Modifier.weight(1f)) }
                    }
                }
                BotaoSecundario("Tirar foto", Modifier.fillMaxWidth()) {
                    val arquivo = vm.novaFoto()
                    fotoPendente = arquivo
                    camera.launch(FileProvider.getUriForFile(contexto, "${contexto.packageName}.arquivos", arquivo))
                }
            }
        }
        item {
            Painel {
                CabecaPainel("O que vai no relatório")
                GradeLeituras(listOf(
                    "REDE" to (vm.rede?.tipo ?: "não lida"),
                    "WI-FI AO REDOR" to if (vm.wifi.isEmpty()) "não buscado" else "${vm.wifi.size} redes",
                    "AMBIENTES" to if (vm.ambientes.isEmpty()) "nenhum" else "${vm.ambientes.size} marcados",
                    "TESTES" to if (vm.pings.isEmpty()) "não rodados" else "ping, DNS, velocidade",
                    "APARELHOS" to if (vm.dispositivos.isEmpty()) "não varrido" else "${vm.dispositivos.size}",
                    "PING CONTÍNUO" to (vm.pingContinuo?.let { "${it.enviados} s" } ?: "—"),
                ))
                BotaoPrincipal("Enviar o PDF") { vm.exportar(pdf = true, json = false) }
                BotaoSecundario("Enviar PDF e JSON", Modifier.fillMaxWidth()) { vm.exportar(pdf = true, json = true) }
                Rotulo("O JSON TEM OS MESMOS DADOS EM FORMATO PARA IMPORTAR OU ENTREGAR A UMA IA.")
            }
        }
        item { PainelCentral(vm) }
        item {
            Painel {
                CabecaPainel("Visitas salvas", if (vm.visitas.isNotEmpty()) "${vm.visitas.size}" else null)
                Text("Tudo o que você mede fica salvo neste celular, mesmo sem internet.", color = Cor.suave, fontSize = 13.sp)
                val formato = SimpleDateFormat("dd/MM/yyyy HH:mm", Locale("pt", "BR"))
                vm.visitas.forEach { s ->
                    val atual = s.id == vm.idVisita
                    Row(Modifier.fillMaxWidth().background(Cor.cartao2, RoundedCornerShape(12.dp))
                        .border(1.dp, if (atual) Cor.ciano.copy(alpha = 0.5f) else Cor.borda, RoundedCornerShape(12.dp))
                        .clickable { vm.abrirVisita(s.id) }.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(s.local.ifBlank { "Sem nome" } + if (atual) "  (aberta)" else "", fontWeight = FontWeight.Medium, fontSize = 14.sp)
                            Rotulo("${formato.format(Date(s.quando))} · ${s.aparelhos} APAR. · ${s.redes} REDES · ${s.fotos} FOTOS")
                        }
                        Icon(Icons.Filled.Close, "Excluir", tint = Cor.apagado, modifier = Modifier.size(20.dp).clickable { vm.excluirVisita(s.id) })
                    }
                }
                BotaoSecundario("Começar visita nova", Modifier.fillMaxWidth()) { vm.novaVisita() }
            }
        }
        item {
            Rotulo("NEXOCAMPO ${vm.versao} · MD SOLUÇÕES TECNOLÓGICAS", modifier = Modifier.padding(top = 4.dp))
        }
    }
}

@Composable
private fun Miniatura(vm: CampoViewModel, f: Foto, modifier: Modifier) {
    val arquivo = vm.arquivoDaFoto(f)
    val imagem by produceState<ImageBitmap?>(null, arquivo) {
        value = withContext(Dispatchers.IO) {
            runCatching { BitmapFactory.decodeFile(arquivo.path, BitmapFactory.Options().apply { inSampleSize = 8 })?.asImageBitmap() }.getOrNull()
        }
    }
    Box(modifier.aspectRatio(1f).clip(RoundedCornerShape(10.dp)).background(Cor.cartao2)) {
        imagem?.let { Image(it, contentDescription = f.legenda.ifBlank { "Foto" }, contentScale = ContentScale.Crop, modifier = Modifier.fillMaxSize()) }
        Box(Modifier.align(Alignment.TopEnd).padding(4.dp).size(24.dp).background(Color.Black.copy(alpha = 0.55f), RoundedCornerShape(12.dp))
            .clickable { vm.removerFoto(f) }, contentAlignment = Alignment.Center) {
            Icon(Icons.Filled.Close, "Remover foto", tint = Color.White, modifier = Modifier.size(14.dp))
        }
    }
}

// ------------------------------------------------------------------ Atualização
@Composable
private fun AvisoAtualizacao(vm: CampoViewModel) {
    val v = vm.novaVersao ?: return
    Column(
        Modifier.padding(top = 10.dp).fillMaxWidth().background(Cor.ciano.copy(alpha = 0.10f), RoundedCornerShape(14.dp))
            .border(1.dp, Cor.ciano.copy(alpha = 0.35f), RoundedCornerShape(14.dp)).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Versão ${v.nome} disponível", fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
            Rotulo("ATUALIZAÇÃO", Cor.ciano)
        }
        when {
            vm.baixandoAtualizacao -> LinearProgressIndicator(progress = { vm.progressoAtualizacao }, modifier = Modifier.fillMaxWidth(), color = Cor.ciano, trackColor = Cor.borda)
            vm.precisaLiberarInstalacao -> {
                Text("Na primeira vez, o Android pede para liberar o NexoCampo a instalar atualizações. Ligue a opção e volte para cá.",
                    color = Cor.suave, fontSize = 13.sp)
                BotaoPrincipal("Liberar instalação") { vm.liberarInstalacao() }
            }
            else -> BotaoPrincipal("Atualizar agora") { vm.atualizar() }
        }
    }
}

// ------------------------------------------------------------------ Central (NexoHub)
@Composable
private fun PainelCentral(vm: CampoViewModel) {
    var usuario by rememberSaveable { mutableStateOf("") }
    var senha by remember { mutableStateOf("") }
    var codigo by remember { mutableStateOf("") }
    Painel {
        CabecaPainel("Enviar para a Central", vm.numeroNaCentral?.let { "ENVIADA · Nº $it" })
        if (vm.contaCentral == null) {
            Text("Entre com o seu usuário do NexoHub (equipe da MD). A visita vai para Visitas de campo e, se quiser, para um chamado.",
                color = Cor.suave, fontSize = 13.sp, lineHeight = 18.sp)
            OutlinedTextField(usuario, { usuario = it }, label = { Text("Usuário") }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                colors = campoCores(), shape = RoundedCornerShape(12.dp))
            OutlinedTextField(senha, { senha = it }, label = { Text("Senha") }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                visualTransformation = androidx.compose.ui.text.input.PasswordVisualTransformation(),
                keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = androidx.compose.ui.text.input.KeyboardType.Password),
                colors = campoCores(), shape = RoundedCornerShape(12.dp))
            if (vm.precisaCodigo) {
                OutlinedTextField(codigo, { codigo = it.filter(Char::isDigit).take(6) }, label = { Text("Código do autenticador") }, singleLine = true,
                    modifier = Modifier.fillMaxWidth(), colors = campoCores(), shape = RoundedCornerShape(12.dp),
                    keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = androidx.compose.ui.text.input.KeyboardType.Number))
            }
            BotaoPrincipal(if (vm.entrando) "Entrando…" else "Entrar", ativo = !vm.entrando && usuario.isNotBlank() && senha.isNotBlank()) {
                vm.entrarNaCentral(usuario, senha, codigo.ifBlank { null })
            }
        } else {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Rotulo("CONECTADO COMO")
                    Text(vm.contaCentral ?: "", fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                }
                Text("Sair", color = Cor.suave, fontSize = 13.sp, modifier = Modifier.clickable { vm.sairDaCentral() }.padding(8.dp))
            }
            val empresa = vm.empresas.firstOrNull { it.id == vm.empresaEscolhida }
            Seletor("Empresa", empresa?.nome, vm.empresas.map { it.id to it.nome }) { vm.escolherEmpresa(it) }
            if (empresa != null) {
                if (empresa.locais.isNotEmpty()) Seletor("Local monitorado (opcional)", empresa.locais.firstOrNull { it.first == vm.localEscolhido }?.second,
                    listOf<Pair<String?, String>>(null to "Nenhum") + empresa.locais) { vm.localEscolhido = it }
                if (empresa.chamados.isNotEmpty()) Seletor("Chamado aberto (opcional)", empresa.chamados.firstOrNull { it.first == vm.chamadoEscolhido }?.let { "#${it.first} · ${it.second}" },
                    listOf<Pair<Int?, String>>(null to "Nenhum") + empresa.chamados.map { it.first to "#${it.first} · ${it.second}" }) { vm.chamadoEscolhido = it }
            }
            if (vm.empresas.isEmpty()) BotaoSecundario("Carregar empresas", Modifier.fillMaxWidth()) { vm.carregarEmpresas() }
            BotaoPrincipal(if (vm.enviando) "Enviando…" else if (vm.numeroNaCentral != null) "Enviar de novo (atualiza)" else "Enviar para a Central",
                ativo = !vm.enviando && empresa != null) { vm.enviarParaCentral() }
            Rotulo("NO CHAMADO, A VISITA ENTRA COMO NOTA INTERNA DA MD, COM O PDF.")
        }
    }
}

/** Campo de escolha (abre a lista ao tocar). */
@Composable
private fun <T> Seletor(rotulo: String, valor: String?, opcoes: List<Pair<T, String>>, aoEscolher: (T) -> Unit) {
    var aberto by remember { mutableStateOf(false) }
    Box {
        Column(Modifier.fillMaxWidth().background(Cor.cartao2, RoundedCornerShape(12.dp)).border(1.dp, Cor.borda, RoundedCornerShape(12.dp))
            .clickable { aberto = true }.padding(horizontal = 14.dp, vertical = 10.dp)) {
            Rotulo(rotulo.uppercase())
            Text(valor ?: "Escolher…", color = if (valor == null) Cor.suave else Cor.texto, fontSize = 14.sp, modifier = Modifier.padding(top = 2.dp))
        }
        androidx.compose.material3.DropdownMenu(aberto, { aberto = false }, modifier = Modifier.background(Cor.cartao2)) {
            opcoes.forEach { (chave, nome) ->
                androidx.compose.material3.DropdownMenuItem(text = { Text(nome, fontSize = 14.sp) }, onClick = { aberto = false; aoEscolher(chave) })
            }
        }
    }
}

// ------------------------------------------------------------------ Sensor NexoPulse do local
@Composable
private fun PainelSensor(vm: CampoViewModel) {
    val d = vm.sensorDados
    val achado = vm.sensorAchado
    var ip by rememberSaveable(achado) { mutableStateOf(achado ?: "") }
    var usuario by rememberSaveable { mutableStateOf(vm.painelUsuarioSalvo) }
    var senha by remember { mutableStateOf(vm.painelSenhaSalva) }
    var lembrar by rememberSaveable { mutableStateOf(vm.painelSenhaSalva.isNotEmpty()) }
    Painel {
        CabecaPainel("Sensor do local", d?.let { "LIDO ${SimpleDateFormat("HH:mm", Locale("pt", "BR")).format(Date(it.lidoEm))}" })
        if (d == null) {
            Text(
                when {
                    achado != null -> "Sensor NexoPulse encontrado em $achado. Com o login do painel local, o app pega o MAC e a marca dos aparelhos, os que saíram da rede e as quedas dos últimos dias. Funciona sem internet."
                    vm.varreduraFeita -> "Nenhum sensor apareceu nesta rede. Se houver um em outra faixa, digite o IP dele."
                    else -> "Se o local tem um sensor NexoPulse, a varredura acha ele. Ou digite o IP do sensor."
                }, color = Cor.suave, fontSize = 13.sp, lineHeight = 18.sp,
            )
            OutlinedTextField(ip, { ip = it.trim() }, label = { Text("IP do sensor") }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                colors = campoCores(), shape = RoundedCornerShape(12.dp),
                keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = androidx.compose.ui.text.input.KeyboardType.Uri))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(usuario, { usuario = it }, label = { Text("Usuário do painel") }, singleLine = true, modifier = Modifier.weight(1f),
                    colors = campoCores(), shape = RoundedCornerShape(12.dp))
                OutlinedTextField(senha, { senha = it }, label = { Text("Senha") }, singleLine = true, modifier = Modifier.weight(1f),
                    visualTransformation = androidx.compose.ui.text.input.PasswordVisualTransformation(),
                    keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = androidx.compose.ui.text.input.KeyboardType.Password),
                    colors = campoCores(), shape = RoundedCornerShape(12.dp))
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Lembrar neste celular", fontSize = 13.sp, color = Cor.suave, modifier = Modifier.weight(1f))
                Switch(lembrar, { lembrar = it }, colors = SwitchDefaults.colors(checkedTrackColor = Cor.ciano, checkedThumbColor = Cor.fundo,
                    uncheckedTrackColor = Cor.cartao2, uncheckedBorderColor = Cor.borda, uncheckedThumbColor = Cor.suave))
            }
            BotaoPrincipal(if (vm.conectandoSensor) "Lendo o sensor…" else "Usar dados do sensor",
                ativo = !vm.conectandoSensor && ip.isNotBlank() && usuario.isNotBlank() && senha.isNotBlank()) {
                vm.conectarSensor(ip, usuario, senha, lembrar)
            }
        } else {
            val fora = d.aparelhos.count { it.status == "offline" }
            GradeLeituras(listOf(
                "SENSOR" to (d.nome ?: d.ip), "VERSÃO" to d.versao,
                "APARELHOS" to "${d.aparelhos.size - fora} na rede · $fora fora", "QUEDAS ${d.dias} DIAS" to "${d.quedas}",
            ))
            if (d.ocorrencias.isEmpty()) {
                Text("Nenhuma queda registrada nos últimos ${d.dias} dias.", color = Cor.ok, fontSize = 13.sp)
            } else {
                Rotulo("ÚLTIMAS OCORRÊNCIAS")
                d.ocorrencias.take(8).forEach { e ->
                    val cor = when { e.gravidade == "OK" -> Cor.ok; e.queda || e.gravidade == "Crítica" -> Cor.critico; else -> Cor.atencao }
                    Row(Modifier.fillMaxWidth().background(Cor.cartao2, RoundedCornerShape(10.dp)).padding(10.dp), verticalAlignment = Alignment.Top) {
                        Box(Modifier.padding(top = 5.dp).size(8.dp).background(cor, RoundedCornerShape(4.dp)))
                        Spacer(Modifier.width(10.dp))
                        Column(Modifier.weight(1f)) {
                            Row {
                                Text(e.tipo, fontWeight = FontWeight.SemiBold, fontSize = 13.sp, modifier = Modifier.weight(1f))
                                Rotulo(e.quando)
                            }
                            Text(e.detalhes, color = Cor.suave, fontSize = 12.sp, lineHeight = 16.sp)
                        }
                    }
                }
                if (d.ocorrencias.size > 8) Rotulo("+${d.ocorrencias.size - 8} NO RELATÓRIO")
            }
            Rotulo("OS APARELHOS ABAIXO JÁ VÊM COM MAC, MARCA E OS QUE SAÍRAM DA REDE (PELO SENSOR).")
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                BotaoSecundario(if (vm.conectandoSensor) "Lendo…" else "Ler de novo", Modifier.weight(1f), ativo = !vm.conectandoSensor) {
                    vm.conectarSensor(d.ip, vm.painelUsuarioSalvo.ifBlank { usuario }, vm.painelSenhaSalva.ifBlank { senha }, vm.painelSenhaSalva.isNotEmpty())
                }
                BotaoSecundario("Desconectar", Modifier.weight(1f)) { vm.desconectarSensor() }
            }
        }
    }
}
