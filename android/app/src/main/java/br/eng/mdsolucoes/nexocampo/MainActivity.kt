package br.eng.mdsolucoes.nexocampo

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.runtime.mutableStateOf
import androidx.core.content.ContextCompat
import br.eng.mdsolucoes.nexocampo.ui.App
import br.eng.mdsolucoes.nexocampo.ui.TemaNexoCampo

class MainActivity : ComponentActivity() {

    private val vm: CampoViewModel by viewModels()
    private val permissoesOk = mutableStateOf(false)

    private val permissoes = buildList {
        add(Manifest.permission.ACCESS_FINE_LOCATION)
        add(Manifest.permission.ACCESS_COARSE_LOCATION)
        if (Build.VERSION.SDK_INT >= 33) add(Manifest.permission.NEARBY_WIFI_DEVICES)
    }.toTypedArray()

    private val pedir = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        permissoesOk.value = temPermissao()
        vm.atualizarRede()
    }

    private fun temPermissao() =
        ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        permissoesOk.value = temPermissao()
        if (!permissoesOk.value) pedir.launch(permissoes)
        setContent {
            TemaNexoCampo {
                App(vm, permissoesOk.value) { pedir.launch(permissoes) }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        permissoesOk.value = temPermissao()
        vm.atualizarRede()
        vm.aoVoltar()
    }
}
