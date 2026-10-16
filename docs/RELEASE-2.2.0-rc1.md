# NOC Central 2.2.0-rc1

## Arquitetura e mudanças

Mantidos Flask, templates Jinja, PostgreSQL/SQLite e os endpoints v2. Sem troca de framework. `notifications.py` separa transporte e fila Telegram; `location.py` concentra validação; `database.py` executa migrações idempotentes e interrompe o startup em falha real. O worker de notificações usa o banco existente e uma thread; não requer Redis/Celery. Manter um worker Gunicorn como no Procfile atual.

- Telegram: outbox persistente, lease atômico, até três tentativas em falhas de rede/5xx/429, respeito ao retry_after (até 24h), deduplicação por cliente+mensagem por cinco minutos, limpeza de entregas finalizadas após sete dias. Falhas permanentes não são repetidas. Teste administrativo imediato faz uma tentativa e mostra a resposta; eventos operacionais usam a fila. Status persiste origem, último sucesso, última falha, categoria e HTTP status. Tokens nunca entram em logs de erro.
- Configuração parcial do cliente produz diagnóstico e não usa silenciosamente credenciais master. Sem configuração própria, o master continua sendo fallback. Token inválido, chat inacessível, bot bloqueado/permissões, rate limit e rede são diferenciados conforme a resposta do Telegram. Inicie o bot no privado ou adicione-o ao grupo e confira a permissão de postagem em canais.
- Localização: navegador com enableHighAccuracy, captura sob HTTPS, precisão fornecida pelo dispositivo, timeout e erros visíveis; coordenadas ou clique no mapa; remoção explícita. Um computador sem GPS pode continuar fornecendo posição aproximada. Capture fisicamente no local do sensor e confira a precisão.
- `latitude`, `longitude`, `accuracy_m`, `location_source`, `location_updated_at` persistem; `lat/lon` continuam sincronizados para clientes legados. Coordenadas zero são válidas. Fontes: browser, manual, ip, legacy. Localizações migradas não recebem data de captura inventada.
- Fallback por IP: opt-in `NOC_IP_GEOLOCATION=1` no sensor, consulta HTTPS ao ipwho.is durante o startup, sem precisão inventada. Esse provedor recebe o IP público do sensor. Não usa o IP do operador e não substitui browser/manual. Desativado por padrão. Sem resultado o sensor fica sem localização.
- Telemetria: interface da rota padrão Linux/Windows, IP/máscara/CIDR, MAC da interface, link, RX/TX em Mbps da interface. Intervalo dos contadores usa relógio monotônico. Sem máscara não se inventa /24. IPv6 não entra na descoberta IPv4. Redes grandes são limitadas a um segmento local e ao máximo configurado (64–2048 hosts, padrão 512). Vizinhos sem ICMP não são classificados como offline; FAILED/INCOMPLETE são desconhecidos. L2 continua uma heurística.
- UI: tema corporativo compartilhado, navegação Host/Rede Local/WAN/Dispositivos/Incidentes/Diagnósticos/Configurações, versões visíveis, layout móvel, localização e status Telegram. Percentuais fixos sem medição removidos. Cache offline de dashboard retirado para não apresentar dados antigos como atuais. Tiles públicos OpenStreetMap substituem o provedor que exigia chave.
- Segurança: verificação de propriedade nas rotas de sensor/usuário autenticadas, bloqueio de escalada de perfil, validação de registros filhos, origem de mutações, CSRF nas mutações do navegador, cabeçalhos de segurança, sessão HttpOnly/SameSite/Secure, Socket.IO da mesma origem e autenticado, limites de payload, hash de senha legado migrado no login, contas inativas recusadas e correção de injeção de HTML no painel TV. Ping e consulta de serviços não interpolam entrada em shell. Não é uma auditoria de segurança externa.
- Autenticação dos sensores: cada Linux ou Windows pode usar uma chave individual, gerada na tela de Sensores e exibida uma única vez. Somente SHA-256 da chave aleatória é persistido. Uma chave individual fica limitada ao MAC/ID informado pelo próprio agente, inclusive nos registros filhos de alvos, energia e serviços. Revogação e rotação são administrativas.

## Compatibilidade e implantação

O rollout individual não quebra agentes antigos. Mantenha `NOC_SENSOR_AUTH_REQUIRED=0` (padrão) na Central, gere uma chave por sensor e configure essa chave como `NOC_SENSOR_API_KEY` no respectivo Linux ou Windows. Agentes ainda sem chave continuam aceitos nessa fase. Uma chave apresentada, mas incorreta, é sempre rejeitada.

Somente depois de confirmar todos os sensores na tela administrativa, definir `NOC_SENSOR_AUTH_REQUIRED=1` na Central. `/healthz` publica apenas esse estado booleano, nunca os segredos. A antiga chave compartilhada configurada como `NOC_SENSOR_API_KEY` na Central continua aceita para migração, mas não deve ser adotada em novas instalações. Defini-la na Central exige chave imediatamente e, portanto, ainda pode interromper agentes legados.

Central e código Python do agente: **2.2.0-rc1**. Executável Windows publicado: **2.1.0**, mantido no endpoint `agent_versao` para não anunciar um binário inexistente. É preciso construir, testar e distribuir um novo EXE para atualizar instalações Windows empacotadas. Agentes antigos continuam aceitos.

No Windows atualizado, `agente_v2.py` e o futuro EXE leem a chave individual da variável de ambiente `NOC_SENSOR_API_KEY`, assim como no Linux. Em PowerShell elevado, a configuração persistente é `[Environment]::SetEnvironmentVariable('NOC_SENSOR_API_KEY', 'CHAVE_DA_CENTRAL', 'Machine')`; em seguida, reinicie o serviço, a tarefa agendada ou o processo do agente. O EXE 2.1.0 atual deve ser substituído e validado antes de ativar a exigência global. A Central não anuncia auto-update enquanto não houver URL HTTPS e SHA-256 configurados para um artefato Windows testado.

Variáveis Central: DATABASE_URL, FLASK_SECRET_KEY persistente, `NOC_SENSOR_AUTH_REQUIRED=0` durante o rollout, TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID opcionais; ADMIN_SENHA_INICIAL é necessária somente se o banco ainda não contém admin. Não se imprime senha de bootstrap em logs. DISABLE_BACKGROUND_GUARDIAN=1 é exclusivo de testes; desliga também o worker Telegram. O Gunicorn foi atualizado porque a versão 20.1 dependia de pkg_resources removido de instalações recentes.

## Migração e rollback

As migrações `2.2.0-rc1` e `2.2.0-sensor-auth` são aplicadas no startup, com registro em schema_migrations e lock transacional no PostgreSQL. Adicionam campos e as tabelas telegram_outbox, telegram_status, location_migration_backup e sensor_credentials. Consolida tabelas e índices antes de servir requisições. Não exclui sensores nem ativa obrigatoriedade de credenciais.

Antes da migração, lat/lon existentes são copiados para location_migration_backup. O par histórico (-14.235,-51.925), usado como placeholder, é removido com tolerância para REAL do PostgreSQL. Outros pares válidos viram legacy. Se esse par tiver sido intencionalmente configurado, ele permanece na cópia de auditoria e pode ser reconfigurado. Fazer backup/snapshot do PostgreSQL no provedor antes de implantação; uma tag de código não é backup do banco.

Rollback de aplicação: reimplantar a referência `rollback/pre-2.2.0-rc1-*` criada antes do merge. Campos adicionais são compatíveis com a aplicação anterior; filas não serão consumidas pelo código antigo. Não remover colunas durante rollback. Coordenadas anteriores podem ser consultadas na tabela de backup; restaurá-las exige decisão específica para evitar recolocar placeholders. Para rollback completo, restaurar o snapshot do banco conforme as regras do provedor.

## Verificação

- Suíte pytest para Telegram (HTTP/API, erros, retry, dedup, restart, status), localização, compatibilidade e isolamento de clientes.
- Testes de interface/rota em Linux e simulações de Windows, CIDR, limites, estados ARP, ping sem shell.
- CI roda Python 3.11 com SQLite e PostgreSQL 16.
- Navegador Chromium: dashboard, sensor, usuários, desktop 1440px e mobile 390px; captura simulada e gravação no servidor.
- `/healthz` consulta o banco e expõe central_version e sensor_auth_required; não basta um HTTP 200 da página de login para confirmar a versão.

## Limites operacionais restantes

Telegram não oferece idempotência de sendMessage: timeout após envio ou queda do worker após confirmação pode gerar duplicata. A prevenção é básica, não exactly-once. Após três falhas, consulte o diagnóstico, corrija a configuração e teste novamente; eventos encerrados não são reenviados automaticamente. O status reflete a última tentativa, não uma garantia de conectividade contínua. Teste real exige credenciais e confirmação do destinatário.

Modo legado sem chave deixa endpoints de ingestão sem autenticação; é risco conhecido mantido somente para o rollout. Gerar credenciais não elimina esse risco até `NOC_SENSOR_AUTH_REQUIRED=1`. O agente local e a aplicação ainda possuem rotinas legadas com tratamento amplo de erros; revisão incremental não equivale a reescrita ou garantia de ausência de vulnerabilidades. CDN/mapa depende de serviços externos. Validar Windows em máquina física e Linux no Mini PC, além de testes simulados.

Referências: [Telegram Bot API](https://core.telegram.org/bots/api#making-requests), [Geolocation API](https://developer.mozilla.org/en-US/docs/Web/API/Geolocation/getCurrentPosition).

## Evidências da preparação

49 testes passam localmente, incluindo autenticação individual, revogação, isolamento entre sensores, compatibilidade legada, Telegram, localização e isolamento de clientes. O CI valida SQLite e PostgreSQL 16, compila os dois agentes Python e inicia o Gunicorn real. A execução do CI desta revisão deve ser registrada após o envio da branch.

O workflow publica a tag imutável `v2.2.0-rc1` somente após os testes da main passarem. Se a tag já existir, não a move. A validação do deploy real e a entrega Telegram com as credenciais de produção são etapas externas aos testes automatizados; não são inferidas a partir do CI.

## Correção de inicialização no Railway

O primeiro deploy de `612fbf7` falhou porque o worker gevent do Gunicorn importava `packaging`, ausente na instalação de produção. A dependência agora é explícita. O smoke test do CI cria um ambiente isolado contendo somente `requirements.txt`, evitando que dependências do pytest ocultem falhas de produção. Validação local: instalação limpa, `pip check`, inicialização real do Gunicorn e `/healthz` retornando `2.2.0-rc1`.

A tag publicada `v2.2.0-rc1` permanece imutável em `612fbf7`; a Central deve ser implantada pela `main` com esta correção posterior. O agente Linux da tag não é afetado. Não há nova migração nem alteração de autenticação nesta correção.

O hardening de plataforma e a autenticação individual posterior estão no commit imutável `e5537ed24da09add6b3b541e03fe89c1577a1344`. Para atualizar o agente Linux com esse conjunto, use esse SHA completo; não use apenas a tag anterior. A versão exibida continua `2.2.0-rc1` por compatibilidade, sem mover a tag publicada.

## Revisão de diagnóstico Telegram — 27/09/2026

Falhas de DNS, TLS, timeout e conexão agora têm códigos e orientações separados, sem registrar URLs ou tokens. Metadados `parameters`/`retry_after` malformados usam intervalo seguro de dois segundos e preservam a classificação original da falha; um erro permanente de permissão não volta para a fila por causa desses metadados. O limite continua em três tentativas para falhas transitórias.

Validação local: 43 testes passaram, incluindo dez cenários novos de erros de rede e respostas malformadas. Não há nova migração nem alteração de autenticação dos sensores. A entrega real permanece dependente do teste administrativo no destino configurado.

### Configuração de sessão no Railway

No serviço NOC-Central, em Variables, configurar uma `FLASK_SECRET_KEY` aleatória e permanente; pode ser gerada localmente com `python3 -c "import secrets; print(secrets.token_hex(32))"`. Manter esse valor fora do Git e do chat. Aplicar a configuração reinicia o serviço e invalida as sessões anteriores uma vez. Não alterar `NOC_SENSOR_API_KEY` durante essa operação. Depois do deploy, verificar `/healthz`, entrar novamente e usar o botão Testar Telegram.
