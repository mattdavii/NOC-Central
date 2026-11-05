package br.eng.mdsolucoes.nexocampo.rede

import android.content.Context
import org.json.JSONObject
import java.io.File

/** Visitas guardadas no próprio celular: uma pasta por visita, com o JSON e as fotos. */
object Visitas {

    data class Resumo(val id: String, val local: String, val quando: Long, val aparelhos: Int, val redes: Int, val fotos: Int, val ambientes: Int)

    private fun pasta(contexto: Context) = File(contexto.filesDir, "visitas").apply { mkdirs() }
    fun pastaDa(contexto: Context, id: String) = File(pasta(contexto), id).apply { mkdirs() }
    fun foto(contexto: Context, id: String, arquivo: String) = File(pastaDa(contexto, id), arquivo)

    fun salvar(contexto: Context, l: Levantamento) {
        if (l.vazio) return
        val destino = File(pastaDa(contexto, l.id), "visita.json")
        val temporario = File(destino.parentFile, "visita.json.tmp")
        temporario.writeText(LevantamentoJson.paraJson(l).toString())
        temporario.renameTo(destino)
    }

    fun listar(contexto: Context): List<Resumo> = pasta(contexto).listFiles().orEmpty().mapNotNull { dir ->
        runCatching {
            val o = JSONObject(File(dir, "visita.json").readText())
            Resumo(o.getString("id"), o.optString("local"), o.optLong("quando_ms"), o.optJSONArray("dispositivos")?.length() ?: 0,
                o.optJSONArray("wifi_ao_redor")?.length() ?: 0, o.optJSONArray("fotos")?.length() ?: 0, o.optJSONArray("ambientes")?.length() ?: 0)
        }.getOrNull()
    }.sortedByDescending { it.quando }

    fun abrir(contexto: Context, id: String, versaoApp: String): Levantamento? = runCatching {
        LevantamentoJson.deJson(JSONObject(File(pastaDa(contexto, id), "visita.json").readText()), versaoApp)
    }.getOrNull()

    fun excluir(contexto: Context, id: String) { File(pasta(contexto), id).deleteRecursively() }
}
