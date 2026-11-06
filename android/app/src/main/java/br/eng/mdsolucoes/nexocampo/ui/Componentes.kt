package br.eng.mdsolucoes.nexocampo.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp

/** Grade de papel milimetrado no fundo (24 dp), quase invisível. */
fun Modifier.grade(): Modifier = drawBehind {
    val passo = 24.dp.toPx()
    val cor = Cor.ciano.copy(alpha = 0.045f)
    var x = 0f
    while (x < size.width) { drawLine(cor, Offset(x, 0f), Offset(x, size.height), 1f); x += passo }
    var y = 0f
    while (y < size.height) { drawLine(cor, Offset(0f, y), Offset(size.width, y), 1f); y += passo }
}

@Composable
fun Painel(modifier: Modifier = Modifier, conteudo: @Composable ColumnScope.() -> Unit) {
    Column(
        modifier
            .fillMaxWidth()
            .background(Cor.cartao.copy(alpha = 0.94f), RoundedCornerShape(18.dp))
            .border(1.dp, Cor.borda, RoundedCornerShape(18.dp))
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
        content = conteudo,
    )
}

/** Título do painel, com um rótulo técnico opcional à direita. */
@Composable
fun CabecaPainel(titulo: String, rotulo: String? = null) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(titulo, fontSize = 16.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
        if (rotulo != null) Rotulo(rotulo)
    }
}

@Composable
fun Rotulo(texto: String, cor: Color = Cor.suave, modifier: Modifier = Modifier) {
    Text(texto, fontFamily = Mono, fontWeight = FontWeight.Medium, fontSize = 11.sp, color = cor, letterSpacing = 0.02.em, modifier = modifier)
}

/** Uma leitura: rótulo pequeno em cima, valor em mono embaixo. */
@Composable
fun Leitura(rotulo: String, valor: String?, modifier: Modifier = Modifier, cor: Color = Cor.texto, grande: Boolean = false) {
    Column(modifier) {
        Rotulo(rotulo)
        Text(valor ?: "—", fontFamily = Mono, fontWeight = if (grande) FontWeight.Bold else FontWeight.Medium,
            fontSize = if (grande) 22.sp else 14.sp, color = cor, modifier = Modifier.padding(top = 3.dp))
    }
}

/** Grade de leituras em 2 colunas, com linhas finas entre as células. */
@Composable
fun GradeLeituras(itens: List<Pair<String, String?>>) {
    Column(
        Modifier.fillMaxWidth().background(Cor.borda, RoundedCornerShape(12.dp)).padding(1.dp),
        verticalArrangement = Arrangement.spacedBy(1.dp),
    ) {
        itens.chunked(2).forEachIndexed { i, par ->
            val ultimo = i == (itens.size - 1) / 2
            val forma = when {
                i == 0 && ultimo -> RoundedCornerShape(11.dp)
                i == 0 -> RoundedCornerShape(topStart = 11.dp, topEnd = 11.dp)
                ultimo -> RoundedCornerShape(bottomStart = 11.dp, bottomEnd = 11.dp)
                else -> RoundedCornerShape(0.dp)
            }
            Row(Modifier.fillMaxWidth().background(Cor.borda, forma), horizontalArrangement = Arrangement.spacedBy(1.dp)) {
                par.forEach { (r, v) ->
                    Leitura(r, v, Modifier.weight(1f).background(Cor.cartao, forma).padding(horizontal = 12.dp, vertical = 10.dp))
                }
                if (par.size == 1) Spacer(Modifier.weight(1f).background(Cor.cartao))
            }
        }
    }
}

@Composable
fun BotaoPrincipal(texto: String, modifier: Modifier = Modifier, ativo: Boolean = true, aoClicar: () -> Unit) {
    Box(
        modifier.fillMaxWidth().height(52.dp).alpha(if (ativo) 1f else 0.55f)
            .background(Cor.gradiente, RoundedCornerShape(14.dp))
            .clickable(enabled = ativo, onClick = aoClicar),
        contentAlignment = Alignment.Center,
    ) { Text(texto, color = Color(0xFF04121F), fontWeight = FontWeight.Bold, fontSize = 15.sp) }
}

@Composable
fun BotaoSecundario(texto: String, modifier: Modifier = Modifier, ativo: Boolean = true, cor: Color = Cor.texto, aoClicar: () -> Unit) {
    Box(
        modifier.height(44.dp).alpha(if (ativo) 1f else 0.5f)
            .border(1.dp, Cor.borda, RoundedCornerShape(12.dp))
            .background(Cor.cartao2, RoundedCornerShape(12.dp))
            .clickable(enabled = ativo, onClick = aoClicar).padding(horizontal = 14.dp),
        contentAlignment = Alignment.Center,
    ) { Text(texto, color = cor, fontWeight = FontWeight.SemiBold, fontSize = 14.sp, textAlign = TextAlign.Center) }
}

/** Selo do topo: o que o app está fazendo agora. */
@Composable
fun Selo(texto: String, cor: Color) {
    Row(
        Modifier.border(1.dp, cor.copy(alpha = 0.4f), RoundedCornerShape(50)).padding(horizontal = 10.dp, vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Box(Modifier.size(6.dp).background(cor, CircleShape))
        Text(texto, fontFamily = Mono, fontWeight = FontWeight.Medium, fontSize = 11.sp, color = cor)
    }
}

@Composable
fun Chip(texto: String, selecionado: Boolean = false, numero: String? = null, aoClicar: (() -> Unit)? = null) {
    Row(
        Modifier
            .background(if (selecionado) Cor.ciano.copy(alpha = 0.18f) else Cor.cartao, RoundedCornerShape(50))
            .border(1.dp, if (selecionado) Cor.ciano.copy(alpha = 0.6f) else Cor.borda, RoundedCornerShape(50))
            .then(if (aoClicar != null) Modifier.clickable(onClick = aoClicar) else Modifier)
            .padding(horizontal = 11.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (numero != null) {
            Text(numero, fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 12.sp, color = Cor.ciano)
            Spacer(Modifier.width(5.dp))
        }
        Text(texto, fontSize = 12.sp, color = if (selecionado) Cor.texto else Cor.texto.copy(alpha = 0.9f))
    }
}

@Composable
fun Aviso(texto: String, cor: Color = Cor.atencao) {
    Row(
        Modifier.fillMaxWidth().background(cor.copy(alpha = 0.10f), RoundedCornerShape(12.dp))
            .border(1.dp, cor.copy(alpha = 0.25f), RoundedCornerShape(12.dp)).padding(12.dp),
    ) { Text(texto, color = cor, fontSize = 13.sp, lineHeight = 18.sp) }
}

fun corDoSinal(dbm: Int?): Color = when {
    dbm == null -> Cor.suave
    dbm >= -60 -> Cor.ok
    dbm >= -70 -> Cor.ciano
    dbm >= -78 -> Cor.atencao
    else -> Cor.critico
}

fun corDoPing(ms: Double?, perdaPct: Int = 0): Color = when {
    ms == null -> Cor.critico
    perdaPct > 0 || ms > 150 -> Cor.atencao
    else -> Cor.ok
}
