package br.eng.mdsolucoes.nexocampo.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

@Composable
fun Cartao(titulo: String? = null, modifier: Modifier = Modifier, conteudo: @Composable ColumnScope.() -> Unit) {
    Column(
        modifier
            .fillMaxWidth()
            .background(Cor.cartao, RoundedCornerShape(14.dp))
            .border(1.dp, Cor.borda, RoundedCornerShape(14.dp))
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        if (titulo != null) Text(titulo, color = Cor.texto, fontWeight = FontWeight.SemiBold, fontSize = 16.sp)
        conteudo()
    }
}

@Composable
fun Linha(rotulo: String, valor: String?, corValor: Color = Cor.texto) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(rotulo, color = Cor.suave, fontSize = 14.sp)
        Spacer(Modifier.width(12.dp))
        Text(valor ?: "—", color = corValor, fontSize = 14.sp, fontWeight = FontWeight.Medium)
    }
}

@Composable
fun Ponto(cor: Color) {
    Box(Modifier.size(9.dp).background(cor, CircleShape))
}

@Composable
fun Etiqueta(texto: String, cor: Color = Cor.suave) {
    Row(
        Modifier.border(1.dp, Cor.borda, RoundedCornerShape(50)).padding(horizontal = 10.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) { Text(texto, color = cor, fontSize = 12.sp) }
}

@Composable
fun Aviso(texto: String, cor: Color = Cor.atencao) {
    Row(
        Modifier
            .fillMaxWidth()
            .background(cor.copy(alpha = 0.12f), RoundedCornerShape(10.dp))
            .padding(12.dp),
    ) { Text(texto, color = cor, fontSize = 13.sp) }
}

fun corDoSinal(dbm: Int?): Color = when {
    dbm == null -> Cor.suave
    dbm >= -67 -> Cor.ok
    dbm >= -75 -> Cor.atencao
    else -> Cor.critico
}
