package br.eng.mdsolucoes.nexocampo.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

/** Cores do NexoHub: azul-noite de fundo, ciano como acento, e as três cores de situação. */
object Cor {
    val fundo = Color(0xFF061529)
    val cartao = Color(0xFF0B2140)
    val borda = Color(0xFF1E3A5C)
    val texto = Color(0xFFE6F1FA)
    val suave = Color(0xFF9DB3CA)
    val ciano = Color(0xFF12B5E5)
    val ok = Color(0xFF0BB39A)
    val atencao = Color(0xFFE39B2D)
    val critico = Color(0xFFE05A5A)
}

@Composable
fun TemaNexoCampo(conteudo: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = Cor.ciano, onPrimary = Cor.fundo, background = Cor.fundo, onBackground = Cor.texto,
            surface = Cor.fundo, onSurface = Cor.texto, surfaceVariant = Cor.cartao, onSurfaceVariant = Cor.suave,
            secondaryContainer = Cor.borda, onSecondaryContainer = Cor.texto, outline = Cor.borda,
        ),
        content = conteudo,
    )
}
