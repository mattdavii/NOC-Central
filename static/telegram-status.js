async function refreshTelegramStatus(id, element) {
    try {
        const response=await fetch('/api/v2/status_telegram/'+id);
        if(!response.ok) throw new Error('Não foi possível consultar o status.');
        const data=await response.json();
        const date=value=>value?new Date(value*1000).toLocaleString():'nenhum';
        element.textContent=`Telegram: ${data.status} · destino: ${data.origem}\nÚltimo envio: ${date(data.last_success)} · última falha: ${date(data.last_failure)}${data.result && data.result.erro?'\n'+data.result.erro:''}`;
    } catch(error) {element.textContent=error.message;}
}
document.querySelectorAll('[data-telegram-status]').forEach(element=>{refreshTelegramStatus(element.dataset.telegramStatus,element);setInterval(()=>refreshTelegramStatus(element.dataset.telegramStatus,element),15000);});
