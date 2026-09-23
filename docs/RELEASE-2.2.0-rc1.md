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
- Segurança: verificação de propriedade nas rotas de sensor/usuário autenticadas, bloqueio de escalada de perfil, validação de registros filhos, origem de mutações, sessão HttpOnly/SameSite, Socket.IO da mesma origem e autenticado, limites de payload, hash de senha legado migrado no login, contas inativas recusadas, ping e consulta de serviços sem interpolação em shell. Não é uma auditoria de segurança externa.

## Compatibilidade e implantação

**Não definir NOC_SENSOR_API_KEY na Central enquanto houver agentes sem suporte à chave.** O modo de compatibilidade permanece igual ao anterior quando a variável não existe. Testes cobrem agentes sem versão/campos novos. Se a chave já estiver configurada, sua verificação continua ativa; esta versão não a remove.

Central e código Python do agente: **2.2.0-rc1**. Executável Windows publicado: **2.1.0**, mantido no endpoint `agent_versao` para não anunciar um binário inexistente. É preciso construir, testar e distribuir um novo EXE para atualizar instalações Windows empacotadas. Agentes antigos continuam aceitos.

Variáveis Central: DATABASE_URL, FLASK_SECRET_KEY persistente, TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID opcionais; ADMIN_SENHA_INICIAL é necessária somente se o banco ainda não contém admin. Não se imprime senha de bootstrap em logs. DISABLE_BACKGROUND_GUARDIAN=1 é exclusivo de testes; desliga também o worker Telegram. O Gunicorn foi atualizado porque a versão 20.1 dependia de pkg_resources removido de instalações recentes.

## Migração e rollback

A migração `2.2.0-rc1` é aplicada no startup, com registro em schema_migrations e lock transacional no PostgreSQL. Adiciona campos e tabelas telegram_outbox, telegram_status e location_migration_backup. Consolida tabelas e índices antes de servir requisições. Não exclui sensores nem altera credenciais.

Antes da migração, lat/lon existentes são copiados para location_migration_backup. O par histórico (-14.235,-51.925), usado como placeholder, é removido com tolerância para REAL do PostgreSQL. Outros pares válidos viram legacy. Se esse par tiver sido intencionalmente configurado, ele permanece na cópia de auditoria e pode ser reconfigurado. Fazer backup/snapshot do PostgreSQL no provedor antes de implantação; uma tag de código não é backup do banco.

Rollback de aplicação: reimplantar a referência `rollback/pre-2.2.0-rc1-*` criada antes do merge. Campos adicionais são compatíveis com a aplicação anterior; filas não serão consumidas pelo código antigo. Não remover colunas durante rollback. Coordenadas anteriores podem ser consultadas na tabela de backup; restaurá-las exige decisão específica para evitar recolocar placeholders. Para rollback completo, restaurar o snapshot do banco conforme as regras do provedor.

## Verificação

- Suíte pytest para Telegram (HTTP/API, erros, retry, dedup, restart, status), localização, compatibilidade e isolamento de clientes.
- Testes de interface/rota em Linux e simulações de Windows, CIDR, limites, estados ARP, ping sem shell.
- CI roda Python 3.11 com SQLite e PostgreSQL 16.
- Navegador Chromium: dashboard, sensor, usuários, desktop 1440px e mobile 390px; captura simulada e gravação no servidor.
- `/healthz` consulta o banco e expõe central_version; não basta um HTTP 200 da página de login para confirmar a versão.

## Limites operacionais restantes

Telegram não oferece idempotência de sendMessage: timeout após envio ou queda do worker após confirmação pode gerar duplicata. A prevenção é básica, não exactly-once. Após três falhas, consulte o diagnóstico, corrija a configuração e teste novamente; eventos encerrados não são reenviados automaticamente. O status reflete a última tentativa, não uma garantia de conectividade contínua. Teste real exige credenciais e confirmação do destinatário.

Modo legado sem chave deixa endpoints de ingestão sem autenticação; é risco conhecido mantido por compatibilidade, não habilitar a chave sem rollout. REST usa chave compartilhada quando ativada, não identidade individual por sensor. O agente local e a aplicação ainda possuem rotinas legadas com tratamento amplo de erros; revisão incremental não equivale a reescrita ou garantia de ausência de vulnerabilidades. CDN/mapa depende de serviços externos. Validar Windows em máquina física e Linux no Mini PC, além de testes simulados.

Referências: [Telegram Bot API](https://core.telegram.org/bots/api#making-requests), [Geolocation API](https://developer.mozilla.org/en-US/docs/Web/API/Geolocation/getCurrentPosition).

## Evidências da preparação

33 testes passaram localmente e nos dois bancos do [CI da branch](https://github.com/mattdavii/NOC-Central/actions/runs/35801166230), incluindo o startup Gunicorn. Chromium desktop e mobile foram verificados sem erros de JavaScript e sem overflow horizontal. Captura simulada de geolocalização e persistência de precisão funcionaram. A página de usuários exibiu o diagnóstico de Telegram não configurado no ambiente de teste.

O workflow publica a tag imutável `v2.2.0-rc1` somente após os testes da main passarem. Se a tag já existir, não a move. A validação do deploy real e a entrega Telegram com as credenciais de produção são etapas externas aos testes automatizados; não são inferidas a partir do CI.
