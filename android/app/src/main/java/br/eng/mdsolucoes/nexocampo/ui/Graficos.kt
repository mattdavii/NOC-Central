package br.eng.mdsolucoes.nexocampo.ui

import android.graphics.Paint
import android.graphics.Typeface
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.res.ResourcesCompat
import br.eng.mdsolucoes.nexocampo.R
import br.eng.mdsolucoes.nexocampo.rede.Dispositivo
import br.eng.mdsolucoes.nexocampo.rede.RedeWifi
import br.eng.mdsolucoes.nexocampo.rede.qualidadeSinal
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.sin

@Composable
private fun pincelMono(tamanhoSp: Float, cor: Color): Paint {
    val contexto = LocalContext.current
    val densidade = contexto.resources.displayMetrics.scaledDensity
    return remember(tamanhoSp, cor) {
        Paint().apply {
            isAntiAlias = true
            textSize = tamanhoSp * densidade
            color = cor.toArgb()
            typeface = ResourcesCompat.getFont(contexto, R.font.mono_medium) ?: Typeface.MONOSPACE
        }
    }
}

private fun DrawScope.texto(p: Paint, t: String, x: Float, y: Float, centro: Boolean = false) = drawIntoCanvas {
    p.textAlign = if (centro) Paint.Align.CENTER else Paint.Align.LEFT
    it.nativeCanvas.drawText(t, x, y, p)
}

// ------------------------------------------------------------------ medidor de sinal
/** Arco de -100 a -30 dBm, do vermelho ao ciano, com o valor grande no centro. */
@Composable
fun MedidorSinal(dbm: Int?) {
    val fracao by animateFloatAsState((((dbm ?: -100) + 100) / 70f).coerceIn(0f, 1f), tween(600), label = "medidor")
    Box(Modifier.fillMaxWidth().height(178.dp), contentAlignment = Alignment.BottomCenter) {
        Canvas(Modifier.fillMaxWidth().height(178.dp)) {
            val espessura = 14.dp.toPx()
            val raio = min(size.width / 2 - 24.dp.toPx(), size.height - espessura - 18.dp.toPx())
            val centro = Offset(size.width / 2, size.height - 18.dp.toPx())
            val canto = Offset(centro.x - raio, centro.y - raio)
            val caixa = Size(raio * 2, raio * 2)
            drawArc(Cor.borda, 180f, 180f, false, canto, caixa, style = Stroke(espessura, cap = StrokeCap.Round))
            val pincel = Brush.sweepGradient(
                0f to Cor.critico, 0.5f to Cor.critico, 0.68f to Cor.atencao, 0.84f to Cor.ok, 1f to Cor.ciano, center = centro,
            )
            if (dbm != null) {
                drawArc(pincel, 180f, 180f * fracao, false, canto, caixa, style = Stroke(espessura, cap = StrokeCap.Round))
                val ang = Math.toRadians((180 + 180 * fracao).toDouble())
                val p = Offset(centro.x + raio * cos(ang).toFloat(), centro.y + raio * sin(ang).toFloat())
                drawCircle(Cor.fundo, 9.dp.toPx(), p)
                drawCircle(corDoSinal(dbm), 9.dp.toPx(), p, style = Stroke(4.dp.toPx()))
            }
            // marcas a cada 17,5 dBm
            for (i in 0..4) {
                val a = Math.toRadians((180 + 45 * i).toDouble())
                val r1 = raio + espessura / 2 + 4.dp.toPx(); val r2 = r1 + 7.dp.toPx()
                drawLine(Cor.apagado, Offset(centro.x + r1 * cos(a).toFloat(), centro.y + r1 * sin(a).toFloat()),
                    Offset(centro.x + r2 * cos(a).toFloat(), centro.y + r2 * sin(a).toFloat()), 2.dp.toPx())
            }
        }
        Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.padding(bottom = 6.dp)) {
            Row(verticalAlignment = Alignment.Bottom) {
                Text(dbm?.toString()?.replace("-", "−") ?: "—", fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 46.sp, lineHeight = 46.sp)
                Text(" dBm", fontFamily = Mono, fontSize = 15.sp, color = Cor.suave, modifier = Modifier.padding(bottom = 7.dp))
            }
            Text(if (dbm == null) "Sem leitura de Wi-Fi" else "Sinal ${qualidadeSinal(dbm).lowercase()}", color = corDoSinal(dbm),
                fontWeight = FontWeight.SemiBold, fontSize = 13.sp)
        }
    }
}

// ------------------------------------------------------------------ espectro
private data class Faixa(val fmin: Float, val fmax: Float, val rotulos: List<Pair<Int, Boolean>>)

private fun faixa(nome: String, redes: List<RedeWifi>): Faixa = when (nome) {
    "5 GHz" -> Faixa(5160f, 5850f, (listOf(36, 52, 100, 116, 132, 149, 165) + redes.map { it.canal }).distinct().sorted()
        .map { it to redes.any { r -> r.canal == it } })
    "6 GHz" -> Faixa(5925f, 7125f, redes.map { it.canal }.distinct().sorted().map { it to true })
    else -> Faixa(2400f, 2484f, (1..13).map { it to (it in setOf(1, 6, 11)) })
}

private fun frequenciaDoCanal(faixa: String, canal: Int): Float = when (faixa) {
    "5 GHz" -> 5000f + 5 * canal
    "6 GHz" -> 5950f + 5 * canal
    else -> if (canal == 14) 2484f else 2407f + 5 * canal
}

/** Cada rede como uma curva sobre o canal dela (largura real do canal); a conectada em destaque. */
@Composable
fun Espectro(redes: List<RedeWifi>, nomeFaixa: String) {
    val daFaixa = redes.filter { it.faixa == nomeFaixa }
    val f = faixa(nomeFaixa, daFaixa)
    val eixo = pincelMono(9f, Cor.apagado)
    val eixoForte = pincelMono(9f, Cor.suave)
    val nome = pincelMono(10f, Cor.texto)
    Canvas(Modifier.fillMaxWidth().height(170.dp)) {
        val esq = 30.dp.toPx(); val dir = size.width - 6.dp.toPx()
        val topo = 12.dp.toPx(); val base = size.height - 22.dp.toPx()
        fun x(freq: Float) = esq + (freq - f.fmin) / (f.fmax - f.fmin) * (dir - esq)
        fun y(dbm: Int) = topo + ((-30f - dbm) / 65f).coerceIn(0f, 1f) * (base - topo)
        for (d in listOf(-30, -50, -70, -90)) {
            drawLine(Cor.borda, Offset(esq, y(d)), Offset(dir, y(d)), 1f)
            texto(eixo, "$d", 0f, y(d) + 3.dp.toPx())
        }
        val ordenadas = daFaixa.sortedBy { it.conectada }   // a conectada por último, por cima
        for (r in ordenadas) {
            val centro = r.frequenciaMhz.toFloat()
            val meia = r.larguraMhz / 2f
            val pico = y(r.sinalDbm)
            val caminho = Path().apply {
                moveTo(x(centro - meia), base)
                quadraticBezierTo(x(centro), 2 * pico - base, x(centro + meia), base)
            }
            if (r.conectada) {
                drawPath(caminho, Brush.verticalGradient(listOf(Cor.ciano.copy(alpha = 0.55f), Cor.verde.copy(alpha = 0.04f)), topo, base))
                drawPath(caminho, Cor.ciano, style = Stroke(2.2.dp.toPx()))
                texto(nome, r.ssid.take(18), x(centro), pico - 6.dp.toPx(), centro = true)
            } else {
                val forte = r.sinalDbm >= -70
                if (forte) drawPath(caminho, Cor.suave.copy(alpha = 0.07f))
                drawPath(caminho, (if (forte) Cor.suave else Cor.apagado).copy(alpha = 0.9f), style = Stroke(1.3.dp.toPx()))
            }
        }
        drawLine(Cor.apagado, Offset(esq, base), Offset(dir, base), 1.5f)
        for ((canal, destaque) in f.rotulos) {
            val px = x(frequenciaDoCanal(nomeFaixa, canal))
            if (px in esq..dir) texto(if (destaque) eixoForte else eixo, "$canal", px, size.height - 6.dp.toPx(), centro = true)
        }
    }
}

// ------------------------------------------------------------------ radar
fun corDoTipo(tipo: String): Color = when (tipo) {
    "Este celular" -> Cor.texto
    "Câmera / DVR / NVR" -> Cor.atencao
    "Roteador / gateway", "Roteador", "MikroTik", "Rádio / ponto de acesso", "Starlink" -> Cor.ciano
    "Não identificado" -> Cor.apagado
    else -> Cor.ok
}

/** Aparelhos como pontos num radar: o ângulo vem do IP, a distância do último número dele. */
@Composable
fun Radar(dispositivos: List<Dispositivo>, varrendo: Boolean) {
    val giro = rememberInfiniteTransition(label = "radar")
    val angulo by giro.animateFloat(0f, 360f, infiniteRepeatable(tween(2600, easing = LinearEasing)), label = "giro")
    Canvas(Modifier.fillMaxWidth(0.72f).aspectRatio(1f)) {
        val c = Offset(size.width / 2, size.height / 2)
        val r = size.minDimension / 2
        drawCircle(Brush.radialGradient(listOf(Cor.ciano.copy(alpha = 0.07f), Color.Transparent), c, r), r, c)
        for (k in 1..3) drawCircle(Cor.ciano.copy(alpha = 0.22f), r * k / 3f, c, style = Stroke(1f))
        drawLine(Cor.ciano.copy(alpha = 0.12f), Offset(c.x, c.y - r), Offset(c.x, c.y + r), 1f)
        drawLine(Cor.ciano.copy(alpha = 0.12f), Offset(c.x - r, c.y), Offset(c.x + r, c.y), 1f)
        if (varrendo) rotate(angulo, c) {
            drawArc(
                Brush.sweepGradient(0f to Color.Transparent, 0.8f to Color.Transparent, 1f to Cor.verde.copy(alpha = 0.55f), center = c),
                288f, 72f, true, Offset(c.x - r, c.y - r), Size(r * 2, r * 2),
            )
        }
        for (d in dispositivos) {
            val p = if (d.esteCelular) c else {
                val semente = d.ip.hashCode() and 0x7fffffff
                val ang = Math.toRadians((semente % 360).toDouble())
                val ultimo = d.ip.substringAfterLast('.').toIntOrNull() ?: 0
                val dist = r * (0.22f + 0.7f * ((ultimo * 37) % 100) / 100f)
                Offset(c.x + dist * cos(ang).toFloat(), c.y + dist * sin(ang).toFloat())
            }
            val cor = corDoTipo(d.tipo)
            drawCircle(cor.copy(alpha = 0.22f), 9.dp.toPx(), p)
            drawCircle(cor, 4.dp.toPx(), p)
        }
    }
}

// ------------------------------------------------------------------ séries
/** Linha fina de uma série (ping, sinal); null vira um corte com uma marca vermelha embaixo. */
@Composable
fun Serie(valores: List<Double?>, cor: Color, modifier: Modifier = Modifier, minimo: Double? = null, maximo: Double? = null, area: Boolean = false) {
    Canvas(modifier) {
        val ok = valores.filterNotNull()
        if (valores.size < 2 || ok.isEmpty()) return@Canvas
        val lo = minimo ?: (ok.minOrNull() ?: 0.0)
        val hi = (maximo ?: ok.maxOrNull() ?: 1.0).let { if (it - lo < 1e-6) lo + 1 else it }
        val passo = size.width / (valores.size - 1)
        fun y(v: Double) = (size.height - 2.dp.toPx()) - ((v - lo) / (hi - lo)).toFloat().coerceIn(0f, 1f) * (size.height - 4.dp.toPx())
        val linha = Path(); var aberto = false
        val preenchida = Path(); var inicioArea = 0f
        valores.forEachIndexed { i, v ->
            val x = i * passo
            if (v == null) {
                drawLine(Cor.critico, Offset(x, size.height - 4.dp.toPx()), Offset(x, size.height), 2.dp.toPx())
                if (aberto && area) { preenchida.lineTo((i - 1) * passo, size.height); preenchida.close() }
                aberto = false
            } else if (!aberto) {
                linha.moveTo(x, y(v)); aberto = true
                if (area) { preenchida.moveTo(x, size.height); preenchida.lineTo(x, y(v)); inicioArea = x }
            } else {
                linha.lineTo(x, y(v)); if (area) preenchida.lineTo(x, y(v))
            }
        }
        if (area && aberto) {
            preenchida.lineTo((valores.size - 1) * passo, size.height); preenchida.lineTo(inicioArea, size.height); preenchida.close()
        }
        if (area) drawPath(preenchida, Brush.verticalGradient(listOf(cor.copy(alpha = 0.42f), cor.copy(alpha = 0f))))
        drawPath(linha, cor, style = Stroke(1.8.dp.toPx(), cap = StrokeCap.Round))
    }
}

/** Quatro barrinhas de sinal, como no celular. */
@Composable
fun BarrasSinal(dbm: Int?, modifier: Modifier = Modifier.size(18.dp)) {
    val cheias = when {
        dbm == null -> 0; dbm >= -55 -> 4; dbm >= -67 -> 3; dbm >= -75 -> 2; else -> 1
    }
    val cor = corDoSinal(dbm)
    Canvas(modifier) {
        val w = size.width / 5.5f
        for (i in 0 until 4) {
            val h = size.height * (0.35f + 0.65f * i / 3f)
            drawRoundRect(if (i < cheias) cor else Cor.borda, Offset(i * w * 1.45f, size.height - h), Size(w, h),
                androidx.compose.ui.geometry.CornerRadius(1.5.dp.toPx()))
        }
    }
}
