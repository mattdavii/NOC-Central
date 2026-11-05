package br.eng.mdsolucoes.nexocampo.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ProvideTextStyle
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import br.eng.mdsolucoes.nexocampo.R

/**
 * Visual de instrumento de medição: azul-noite com grade de papel milimetrado, leituras em fonte mono,
 * e o gradiente ciano→verde da Família Nexo só nos destaques. As cores de situação dizem bom, atenção ou ruim.
 */
object Cor {
    val fundo = Color(0xFF071426)
    val cartao = Color(0xFF0C1E35)
    val cartao2 = Color(0xFF10263F)
    val borda = Color(0xFF183553)
    val texto = Color(0xFFE8F1FA)
    val suave = Color(0xFF8FA6BF)
    val apagado = Color(0xFF5F7894)
    val ciano = Color(0xFF08ABD0)
    val verde = Color(0xFF04B89C)
    val ok = Color(0xFF2BD9A8)
    val atencao = Color(0xFFF2B33D)
    val critico = Color(0xFFF2667A)
    val gradiente = Brush.horizontalGradient(listOf(ciano, verde))
}

val Inter = FontFamily(
    Font(R.font.inter_regular, FontWeight.Normal),
    Font(R.font.inter_medium, FontWeight.Medium),
    Font(R.font.inter_semibold, FontWeight.SemiBold),
    Font(R.font.inter_bold, FontWeight.Bold),
)

/** Números e leituras (dBm, IP, ms, Mbps): JetBrains Mono, a mesma do painel do sensor. */
val Mono = FontFamily(
    Font(R.font.mono_medium, FontWeight.Medium),
    Font(R.font.mono_bold, FontWeight.Bold),
)

private fun Typography.comFonte(f: FontFamily) = Typography(
    displayLarge = displayLarge.copy(fontFamily = f), displayMedium = displayMedium.copy(fontFamily = f),
    displaySmall = displaySmall.copy(fontFamily = f), headlineLarge = headlineLarge.copy(fontFamily = f),
    headlineMedium = headlineMedium.copy(fontFamily = f), headlineSmall = headlineSmall.copy(fontFamily = f),
    titleLarge = titleLarge.copy(fontFamily = f), titleMedium = titleMedium.copy(fontFamily = f),
    titleSmall = titleSmall.copy(fontFamily = f), bodyLarge = bodyLarge.copy(fontFamily = f),
    bodyMedium = bodyMedium.copy(fontFamily = f), bodySmall = bodySmall.copy(fontFamily = f),
    labelLarge = labelLarge.copy(fontFamily = f), labelMedium = labelMedium.copy(fontFamily = f),
    labelSmall = labelSmall.copy(fontFamily = f),
)

@Composable
fun TemaNexoCampo(conteudo: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = Cor.ciano, onPrimary = Cor.fundo, background = Cor.fundo, onBackground = Cor.texto,
            surface = Cor.fundo, onSurface = Cor.texto, surfaceVariant = Cor.cartao, onSurfaceVariant = Cor.suave,
            secondaryContainer = Cor.borda, onSecondaryContainer = Cor.texto, outline = Cor.borda, outlineVariant = Cor.borda,
        ),
        typography = Typography().comFonte(Inter),
    ) {
        ProvideTextStyle(TextStyle(fontFamily = Inter, color = Cor.texto), conteudo)
    }
}
