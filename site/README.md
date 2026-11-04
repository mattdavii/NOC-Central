# Site institucional da MD Soluções Tecnológicas

Página única em `public/index.html`, servida pelo Caddy (`Caddyfile`) num serviço próprio
do Railway (`Dockerfile`, diretório raiz `site/`). Não depende da Central: se o NexoHub cair,
o site continua no ar.

## Variáveis do serviço no Railway
| Variável | Para quê |
|---|---|
| `SITE_WHATSAPP` | Número no formato internacional, só dígitos (ex.: `5583999990000`). Mostra o botão do WhatsApp. |
| `SITE_EMAIL` | E-mail de contato. Mostra o botão de e-mail. |
| `SITE_HUB_URL` | Endereço do NexoHub (padrão `https://nexopulse.mdsolucoes.eng.br`; depois do DNS, `https://hub.mdsolucoes.eng.br`). |

Trocar uma variável e reiniciar o serviço já muda o site.

## Domínios
- `www.mdsolucoes.eng.br` e `mdsolucoes.eng.br` → este serviço (o raiz redireciona para o www).
- `hub.mdsolucoes.eng.br` → serviço da Central (NexoHub). `/hub`, `/entrar` e `/login` no site levam para lá.

## Rodar localmente
`caddy run --config Caddyfile --adapter caddyfile` com `PORT=8080` e a pasta `public` em `/srv`
(ou `docker build -t site-md . && docker run -p 8080:8080 site-md`).
