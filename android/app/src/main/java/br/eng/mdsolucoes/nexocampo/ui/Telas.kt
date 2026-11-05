package br.eng.mdsolucoes.nexocampo.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
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
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import br.eng.mdsolucoes.nexocampo.CampoViewModel
import br.eng.mdsolucoes.nexocampo.rede.Dispositivo
import br.eng.mdsolucoes.nexocampo.rede.WifiAoRedor
import br.eng.mdsolucoes.nexocampo.rede.faixaDe
import br.eng.mdsolucoes.nexocampo.rede.qualidadeSinal

private data class Aba(val nome: String, val icone: ImageVector)

private val ABAS = listOf(
    Aba("Rede", Icons.Filled.Wifi), Aba("Testes", Icons.Filled.Speed),
    Aba("Varredura", Icons.Filled.Radar), Aba("Relatório", Icons.Filled.Description),
)

@Composable
fun App(vm: CampoViewModel, permissoesOk: Boolean, pedirPermissoes: () -> Unit) {
    var aba by rememberSaveable { mutableIntStateOf(0) }
    val avisos = remember { SnackbarHostState() }
    LaunchedEffect(vm.mensagem) {
        vm.mensagem?.let { avisos.showSnackbar(it); vm.mensagem = null }
    }
    LaunchedEffect(Unit) { vm.atualizarRede() }
    Scaffold(
        containerColor = Cor.fundo,
        snackbarHost = { SnackbarHost(avisos) },
        bottomBar = {
            NavigationBar(containerColor = Cor.cartao) {
                ABAS.forEachIndexed { i, a ->
                    NavigationBarItem(
                        selected = aba == i, onClick = { aba = i },
                        icon = { Icon(a.icone, contentDescription = null) }, label = { Text(a.nome) },
                        colors = NavigationBarItemDefaults.colors(
                            selectedIconColor = Cor.fundo, indicatorColor = Cor.ciano,
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
private fun Pagina(titulo: String, subtitulo: String, conteudo: androidx.compose.foundation.lazy.LazyListScope.() -> Unit) {
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Column(Modifier.padding(bottom = 4.dp)) {
                Text(titulo, color = Cor.texto, fontSize = 24.sp, fontWeight = FontWeight.Bold)
                Text(subtitulo, color = Cor.suave, fontSize = 14.sp)
            }
        }
        conteudo()
    }
}

@Composable
private fun BotaoPrincipal(texto: String, ativo: Boolean = true, aoClicar: () -> Unit) {
    Button(
        onClick = aoClicar, enabled = ativo, modifier = Modifier.fillMaxWidth().height(50.dp),
        colors = ButtonDefaults.buttonColors(containerColor = Cor.ciano, contentColor = Cor.fundo),
        shape = RoundedCornerShape(12.dp),
    ) { Text(texto, fontWeight = FontWeight.SemiBold, fontSize = 15.sp) }
}

// ------------------------------------------------------------------ Rede
@Composable
private fun TelaRede(vm: CampoViewModel, permissoesOk: Boolean, pedirPermissoes: () -> Unit) {
    val r = vm.rede
    Pagina("Rede", "Onde o celular está conectado e as redes Wi-Fi ao redor.") {
        if (!permissoesOk) item {
            Aviso("O Android só mostra o nome do Wi-Fi e as redes ao redor com a permissão de localização.")
            Spacer(Modifier.height(8.dp))
            OutlinedButton(onClick = pedirPermissoes, modifier = Modifier.fillMaxWidth()) { Text("Dar permissão") }
        }
        item {
            Cartao {
                Text(r?.tipo ?: "Lendo…", color = Cor.suave, fontSize = 13.sp)
                Text(r?.ssid ?: (if (r?.tipo == "Wi-Fi") "Wi-Fi (nome oculto)" else r?.tipo ?: "—"),
                    color = Cor.texto, fontSize = 22.sp, fontWeight = FontWeight.Bold)
                if (r != null && r.tipo == "Wi-Fi" && r.ssid == null) {
                    Text("Ligue a localização do celular para ver o nome da rede.", color = Cor.atencao, fontSize = 13.sp)
                }
                val sinal = r?.sinalDbm
                if (sinal != null) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Ponto(corDoSinal(sinal))
                        Text("${qualidadeSinal(sinal)} · $sinal dBm", color = corDoSinal(sinal), fontWeight = FontWeight.SemiBold)
                    }
                }
                Spacer(Modifier.height(4.dp))
                if (r != null && r.tipo == "Wi-Fi") {
                    Linha("Canal", r.canal?.let { "$it · ${r.frequenciaMhz?.let(::faixaDe) ?: ""}" })
                    Linha("Velocidade do link", r.velocidadeMbps?.let { "$it Mbps" })
                    Linha("Padrão", r.padrao)
                    Linha("Segurança", r.seguranca)
                    Linha("Roteador (BSSID)", r.bssid)
                }
                Linha("IP do celular", r?.ip?.let { "$it/${r?.prefixo ?: "?"}" })
                Linha("Gateway", r?.gateway)
                Linha("DNS", r?.dns?.joinToString(", ")?.ifBlank { null })
                OutlinedButton(onClick = { vm.atualizarRede() }, modifier = Modifier.fillMaxWidth()) { Text("Atualizar") }
            }
        }
        item {
            Cartao("Wi-Fi ao redor") {
                BotaoPrincipal(if (vm.buscandoWifi) "Buscando…" else "Buscar redes", ativo = !vm.buscandoWifi && permissoesOk) { vm.buscarWifi() }
                if (vm.wifiBuscado && !vm.wifiAtualizado) {
                    Aviso("O Android limita as buscas de Wi-Fi (4 a cada 2 minutos). Mostrando a última busca; tente de novo em instantes.")
                }
                if (vm.wifiBuscado && vm.wifi.isEmpty()) Text("Nenhuma rede encontrada.", color = Cor.suave)
                vm.melhorCanal24()?.let { (canal, motivo) ->
                    Column(Modifier.fillMaxWidth().background(Cor.ok.copy(alpha = 0.12f), RoundedCornerShape(10.dp)).padding(12.dp)) {
                        Text("Melhor canal em 2,4 GHz: $canal", color = Cor.ok, fontWeight = FontWeight.SemiBold)
                        Text(motivo, color = Cor.suave, fontSize = 13.sp)
                    }
                }
                if (vm.wifi.any { it.faixa == "2,4 GHz" }) GraficoCanais24(vm)
            }
        }
        items(vm.wifi, key = { it.bssid + it.frequenciaMhz }) { w ->
            Row(
                Modifier.fillMaxWidth().background(Cor.cartao, RoundedCornerShape(12.dp)).padding(horizontal = 14.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Ponto(corDoSinal(w.sinalDbm))
                Spacer(Modifier.width(10.dp))
                Column(Modifier.weight(1f)) {
                    Text((if (w.conectada) "● " else "") + w.ssid, color = if (w.conectada) Cor.ciano else Cor.texto, fontWeight = FontWeight.Medium)
                    Text("${w.faixa} · canal ${w.canal} · ${w.larguraMhz} MHz · ${w.seguranca}", color = Cor.suave, fontSize = 12.sp)
                }
                Text("${w.sinalDbm} dBm", color = corDoSinal(w.sinalDbm), fontSize = 13.sp)
            }
        }
    }
}

/** Quantas redes há em cada canal de 2,4 GHz (1 a 13), com destaque nos canais 1, 6 e 11. */
@Composable
private fun GraficoCanais24(vm: CampoViewModel) {
    val ocupacao = WifiAoRedor.ocupacao(vm.wifi).filter { it.faixa == "2,4 GHz" }.associateBy { it.canal }
    val maximo = (ocupacao.values.maxOfOrNull { it.redes } ?: 1).coerceAtLeast(1)
    Text("Redes por canal (2,4 GHz)", color = Cor.suave, fontSize = 13.sp)
    Row(Modifier.fillMaxWidth().height(90.dp), horizontalArrangement = Arrangement.spacedBy(4.dp), verticalAlignment = Alignment.Bottom) {
        for (c in 1..13) {
            val n = ocupacao[c]?.redes ?: 0
            Column(Modifier.weight(1f).fillMaxHeight(), verticalArrangement = Arrangement.Bottom, horizontalAlignment = Alignment.CenterHorizontally) {
                if (n > 0) Text("$n", color = Cor.texto, fontSize = 10.sp)
                Box(
                    Modifier.fillMaxWidth().height((60f * n / maximo).coerceAtLeast(2f).dp)
                        .background(if (c in setOf(1, 6, 11)) Cor.ciano else Cor.borda, RoundedCornerShape(topStart = 3.dp, topEnd = 3.dp)),
                )
                Text("$c", color = Cor.suave, fontSize = 10.sp)
            }
        }
    }
}

// ------------------------------------------------------------------ Testes
@Composable
private fun TelaTestes(vm: CampoViewModel) {
    Pagina("Testes", "Ping ao roteador e à internet, DNS e velocidade.") {
        item {
            BotaoPrincipal(if (vm.testando) vm.etapaTeste.ifBlank { "Testando…" } else "Rodar os testes", ativo = !vm.testando) { vm.rodarTestes() }
            if (vm.testando) LinearProgressIndicator(Modifier.fillMaxWidth().padding(top = 8.dp), color = Cor.ciano, trackColor = Cor.borda)
        }
        vm.velocidade?.let { v ->
            item {
                Cartao("Velocidade") {
                    if (v.erro != null && v.downloadMbps == null) Text("Não deu para medir: ${v.erro}", color = Cor.critico)
                    else Row(Modifier.fillMaxWidth()) {
                        Numero("Download", v.downloadMbps?.let { "%.1f".format(it) }, "Mbps", Modifier.weight(1f))
                        Numero("Upload", v.uploadMbps?.let { "%.1f".format(it) }, "Mbps", Modifier.weight(1f))
                        Numero("Resposta", v.latenciaMs?.let { "%.0f".format(it) }, "ms", Modifier.weight(1f))
                    }
                    Text("Medido contra a Cloudflare, com 4 conexões por cerca de 8 s em cada sentido.", color = Cor.suave, fontSize = 12.sp)
                }
            }
        }
        if (vm.pings.isNotEmpty()) item {
            Cartao("Ping") {
                vm.pings.forEach { p ->
                    val cor = when {
                        p.recebidos == 0 -> Cor.critico
                        p.perdaPct > 0 || (p.mediaMs ?: 0.0) > 150 -> Cor.atencao
                        else -> Cor.ok
                    }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Ponto(cor); Spacer(Modifier.width(10.dp))
                        Column(Modifier.weight(1f)) {
                            Text(p.nome, color = Cor.texto, fontWeight = FontWeight.Medium)
                            Text("${p.alvo} · perda ${p.perdaPct}%", color = Cor.suave, fontSize = 12.sp)
                        }
                        Text(p.mediaMs?.let { "%.0f ms".format(it) } ?: "sem resposta", color = cor, fontWeight = FontWeight.SemiBold)
                    }
                }
            }
        }
        if (vm.dns.isNotEmpty()) item {
            Cartao("DNS") {
                vm.dns.forEach { d -> Linha(d.nome, d.ms?.let { "$it ms" } ?: "não resolveu", if (d.ms == null) Cor.critico else Cor.texto) }
            }
        }
    }
}

@Composable
private fun Numero(rotulo: String, valor: String?, unidade: String, modifier: Modifier = Modifier) {
    Column(modifier) {
        Text(rotulo, color = Cor.suave, fontSize = 12.sp)
        Row(verticalAlignment = Alignment.Bottom) {
            Text(valor ?: "—", color = Cor.texto, fontSize = 26.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.width(4.dp))
            Text(unidade, color = Cor.suave, fontSize = 12.sp, modifier = Modifier.padding(bottom = 4.dp))
        }
    }
}

// ------------------------------------------------------------------ Varredura
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun TelaVarredura(vm: CampoViewModel) {
    Pagina("Varredura", "Os aparelhos da rede local e o que cada um parece ser.") {
        item {
            BotaoPrincipal(if (vm.varrendo) "Varrendo…" else "Varrer a rede (${vm.enderecosParaVarrer()} endereços)", ativo = !vm.varrendo) { vm.rodarVarredura() }
            if (vm.varrendo) {
                LinearProgressIndicator(progress = { vm.progresso }, modifier = Modifier.fillMaxWidth().padding(top = 8.dp), color = Cor.ciano, trackColor = Cor.borda)
                Text(vm.etapaVarredura, color = Cor.suave, fontSize = 12.sp, modifier = Modifier.padding(top = 4.dp))
            }
        }
        if (vm.dispositivos.isNotEmpty()) item {
            val porTipo = vm.dispositivos.groupingBy { it.tipo }.eachCount().entries.sortedByDescending { it.value }
            Cartao("${vm.dispositivos.size} aparelhos encontrados") {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    porTipo.forEach { Etiqueta("${it.value} · ${it.key}", Cor.texto) }
                }
                Text("Sem o MAC (bloqueado pelo Android), marca e tipo vêm das portas, ONVIF, UPnP, mDNS e da página de cada aparelho.",
                    color = Cor.suave, fontSize = 12.sp)
            }
        }
        if (vm.varreduraFeita && vm.dispositivos.size <= 1) item {
            Aviso("Só o celular apareceu. A rede pode isolar os aparelhos do Wi-Fi (isolamento de clientes, comum em Wi-Fi de visitantes).")
        }
        items(vm.dispositivos, key = { it.ip }) { d -> ItemDispositivo(d) }
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
    Column(
        Modifier.fillMaxWidth().background(Cor.cartao, RoundedCornerShape(12.dp)).clickable { aberto = !aberto }
            .padding(horizontal = 14.dp, vertical = 12.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(iconeDo(d.tipo), contentDescription = null, tint = if (d.esteCelular) Cor.ciano else Cor.texto, modifier = Modifier.size(26.dp))
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(d.tipo, color = Cor.texto, fontWeight = FontWeight.Medium)
                val detalhe = listOfNotNull(d.nome, d.fabricante, d.modelo).distinct().joinToString(" · ")
                if (detalhe.isNotBlank()) Text(detalhe, color = Cor.suave, fontSize = 12.sp)
            }
            Text(d.ip, color = Cor.ciano, fontSize = 13.sp)
        }
        if (aberto) {
            Spacer(Modifier.height(8.dp))
            Linha("Portas abertas", d.portas.joinToString(", ").ifBlank { null })
            d.mac?.let { Linha("MAC", it) }
            Linha("Encontrado por", d.origens.joinToString(", ").ifBlank { null })
            d.servicos.forEach { Text("• $it", color = Cor.suave, fontSize = 12.sp) }
        }
    }
}

// ------------------------------------------------------------------ Relatório
@Composable
private fun TelaRelatorio(vm: CampoViewModel) {
    Pagina("Relatório", "Junte o que foi medido e envie em PDF ou JSON.") {
        item {
            Cartao("Visita") {
                OutlinedTextField(vm.local, { vm.local = it }, label = { Text("Local") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(vm.tecnico, { vm.tecnico = it }, label = { Text("Técnico") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(vm.observacoes, { vm.observacoes = it }, label = { Text("Observações") }, minLines = 3, modifier = Modifier.fillMaxWidth())
            }
        }
        item {
            Cartao("O que vai no relatório") {
                Linha("Rede do celular", if (vm.rede != null) "lida" else "não lida")
                Linha("Wi-Fi ao redor", if (vm.wifi.isEmpty()) "não buscado" else "${vm.wifi.size} redes")
                Linha("Testes", if (vm.pings.isEmpty()) "não rodados" else "ping, DNS e velocidade")
                Linha("Aparelhos", if (vm.dispositivos.isEmpty()) "não varrido" else "${vm.dispositivos.size} encontrados")
            }
        }
        item {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                BotaoPrincipal("Enviar o PDF") { vm.exportar(pdf = true, json = false) }
                OutlinedButton(onClick = { vm.exportar(pdf = true, json = true) }, modifier = Modifier.fillMaxWidth()) { Text("Enviar PDF e JSON") }
                Text("O JSON tem os mesmos dados em formato para importar ou entregar a uma IA.", color = Cor.suave, fontSize = 12.sp)
                Text("NexoCampo ${vm.versao} · MD Soluções Tecnológicas", color = Cor.suave, fontSize = 12.sp,
                    modifier = Modifier.padding(top = 12.dp))
            }
        }
    }
}
