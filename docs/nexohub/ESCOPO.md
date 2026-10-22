# NexoHub — escopo da versão 1

Status: **proposta para aprovação** · 20/10/2026 (rev. 2: Central de Acessos Nexo)

## 1. Objetivo

Transformar a Central atual (hoje "a NexoPulse") no **NexoHub**: o portal de acesso à Família Nexo, portal único da MD
onde cada cliente acessa, com um login só, todas as ferramentas Nexo dos serviços que
contratou. A NexoPulse passa a ser **um módulo** do Hub; NexoGuard e NexoReport entram
depois como novos módulos, sem refazer portal, login ou permissões.

Posicionamento: a MD Soluções Tecnológicas presta os serviços; as ferramentas Nexo são
tecnologia própria que eleva a qualidade desses serviços. O cliente vê a MD e o NexoHub.

## 2. O que existe hoje (ponto de partida)

- **Telas:** Operação (`/`), Sensores (`/sensores`), tela do sensor (`/sensor/<mac>`),
  Usuários (`/usuarios`), Servidor (`/saude`), Sala de Operações (`/tv`), Relatório
  (`/relatorio/<mac>`).
- **Perfis:** Administrador Master (MD), Cliente, Administrador Cliente e Operador Cliente
  (subusuários de um cliente). O isolamento por cliente já é feito no servidor
  (`enforce_tenant_scope`).
- **Por cliente:** nome, logo própria (`logo_url`), grupo de Telegram.
- **Barra superior:** marca NexoPulse e os links acima.

## 3. O que entra na v1

### 3.1 Módulos por cliente
- Novo cadastro de módulos: `pulse` (NexoPulse), `guard` (NexoGuard) e `report`
  (NexoReport), com ligado/desligado por cliente.
- **Só o Administrador Master** liga ou desliga módulos, na tela de Usuários (editar
  cliente).
- Migração: todo cliente existente começa com **NexoPulse ligado** (nada muda para quem
  já usa); Guard e Report desligados.
- Subusuários (Administrador/Operador Cliente) herdam os módulos do cliente.

### 3.2 Permissão por módulo (no servidor)
- Telas e APIs de um módulo só respondem se o módulo estiver ligado para o cliente de
  quem pede; senão, 403 (API) ou aviso “módulo não contratado” (tela). O Master acessa tudo.
- Vale também para APIs consumidas pelos sensores? **Não**: sensores continuam
  reportando normalmente; a regra é só para o portal.

### 3.3 Página inicial do Hub (`/`)
- **Cliente:** um card por módulo ligado, com o resumo:
  - NexoPulse: sites online/offline, diagnósticos ativos (com a causa), última queda e
    atalho para a Operação;
  - Guard e Report (quando ligados no futuro): resumo próprio de cada um.
- **Master (MD):** visão de todos os clientes — lista com nome, módulos contratados,
  sites online/offline e alertas ativos — com filtro e atalho para entrar na visão de
  cada cliente.
- Logo do cliente (já existente) no topo da página do cliente.

### 3.4 Navegação agrupada por módulo
```
Início | NexoPulse ▾ (Operação · Sensores* · Sala de Operações) | NexoGuard ▾ | NexoReport ▾ | Administração ▾ (Usuários · Servidor*)
* só Master
```
- Módulo desligado não aparece no menu do cliente (ver decisão D3).

### 3.5 Rotas e compatibilidade
- A Operação atual sai de `/` e vai para **`/pulse`**; `/` vira a página do Hub.
- Todas as outras URLs continuam iguais (`/sensor/<mac>`, `/tv`, `/sensores`,
  `/relatorio/<mac>`, APIs) — links salvos, relatórios e a Sala de Operações seguem
  funcionando.
- A Sala de Operações (`/tv`) não muda.

### 3.6 Central de Acessos Nexo
A tela de Usuários vira a **Central de Acessos**: o lugar único para administrar quem
acessa o quê em toda a Família Nexo. Modelo em dois níveis:

1. **Contrato (empresa → aplicativos):** quais aplicativos Nexo o cliente contratou.
   Só a MD (Master) define.
2. **Permissão (usuário → aplicativo → nível):** dentro do contratado, o que cada pessoa
   vê e faz. **Acesso efetivo = contratado ∩ liberado para o usuário.**

Níveis por aplicativo:
| Nível | Pode |
|---|---|
| **Administrador** | tudo no aplicativo, incluindo configurações (ex.: sensores, limites, ações remotas onde já é permitido) |
| **Operador** | acompanhar e agir no dia a dia (reconhecer alertas, renomear, pedir relatório) |
| **Leitura** | só consultar |
| **Sem acesso** | o aplicativo não aparece para o usuário |

Funcionalidades:
- **Empresas:** cadastro, logo, grupo de Telegram e **aplicativos contratados** (Master).
- **Usuários:** criar, editar, desativar, redefinir senha; para cada um, o nível em cada
  aplicativo contratado. **Perfis prontos** para agilizar (ex.: "Diretoria" = Report
  leitura; "Técnico" = Pulse e Guard operador; "Gestor de TI" = tudo administrador).
- **Delegação:** o Administrador Cliente gerencia os usuários da própria empresa, sem
  poder conceder mais do que a empresa contratou nem mais do que ele mesmo tem.
- **Equipe MD:** usuários internos da MD com níveis próprios (Master e, se quiserem,
  "Operador MD", que acompanha todos os clientes sem poder mudar contratos).
- **Auditoria:** registro de logins (inclusive falhas), mudanças de permissão e de
  contrato, com quem fez e quando; consultável na Central de Acessos.
- **Segurança de acesso:** senha forte, bloqueio após tentativas (já existe para as ações
  remotas, estendido ao login), encerrar sessões de um usuário e **autenticação em 2
  etapas** (código no celular), que a empresa pode tornar obrigatória.
- **Login isolado no código:** a autenticação fica numa parte própria do código, para que
  no futuro dê para plugar um provedor externo (login com conta Microsoft/Google da
  empresa) sem reescrever o Hub.

Migração dos perfis atuais (ninguém perde acesso):
| Perfil atual | Vira |
|---|---|
| Administrador Master | Master (MD), acesso total |
| Cliente | Administrador da empresa, nível Administrador nos aplicativos contratados |
| Administrador Cliente | Administrador da empresa (delegado) |
| Operador Cliente | Operador nos aplicativos contratados |

### 3.7 Identidade
- Barra e login com **NexoHub** + "MD Soluções Tecnológicas" (ver decisão D1 sobre a logo).
- NexoPulse continua nomeando o módulo de rede dentro do Hub.
- Tema claro/escuro, PWA e logo do cliente continuam como hoje.

## 4. Fora da v1
- Funcionalidades do NexoGuard e do NexoReport (só a "vaga" de módulo é criada).
- Troca de domínio (ver decisão D2), login único com Google/Microsoft, app nativo,
  cobrança e contratos dentro do portal.
- Login com conta Microsoft/Google da empresa (fica preparado pelo login isolado).
- Convite por e-mail (exige serviço de e-mail; na v1 o usuário é criado com senha inicial).

## 5. Como vamos entregar (PRs pequenos, cada um com testes e CI)
1. **Contratos e permissões** — aplicativos por empresa, níveis por usuário, migração
   dos perfis atuais, regra de acesso efetivo nas telas/APIs, auditoria básica.
2. **Central de Acessos** — telas de empresas, usuários, perfis prontos e delegação.
3. **Página inicial e navegação** — `/` do Hub (cliente e Master), `/pulse`, menu por
   aplicativo, vitrine.
4. **Identidade** — logo e textos NexoHub na barra, login e manifesto do app.
5. **Autenticação em 2 etapas** e bloqueio de login por tentativas.

## 6. Testes e critérios de aceite
- Cliente com NexoPulse ligado vê o card e a Operação; com ele desligado recebe
  “módulo não contratado” e 403 nas APIs do módulo.
- Cliente **nunca** vê dados de outro cliente no Hub (resumos inclusive) — teste
  dedicado para os novos endpoints de resumo.
- Acesso efetivo = contratado ∩ liberado: usuário sem nível num aplicativo não o vê, e
  nenhum usuário acessa aplicativo não contratado pela empresa, mesmo com nível marcado.
- Administrador Cliente não consegue conceder mais do que a empresa contratou nem mais
  do que ele próprio tem.
- Perfis atuais migram sem perda de acesso.
- Toda mudança de permissão e de contrato aparece na auditoria.
- Master vê todos os clientes e liga/desliga módulos; ninguém mais consegue.
- Clientes existentes continuam com acesso idêntico ao de hoje depois da migração.
- URLs antigas (`/sensor/<mac>`, `/tv`, `/sensores`, `/relatorio/<mac>`) seguem
  funcionando; `/` leva ao Hub.
- Suíte completa verde em SQLite e PostgreSQL.

## 7. Riscos
- **Vazamento entre clientes** nos novos resumos → toda consulta passa pelo filtro de
  cliente existente + teste específico.
- **Hábito/links do `/`** → `/pulse` ganha atalho destacado no Hub; demais URLs intactas.
- **Sensores afetados** → nenhuma API dos sensores muda.

## 8. Decisões que preciso de você
- **D1 — Logo do NexoHub:** crio uma marca provisória no padrão Nexo (símbolo + "NexoHub"
  na tipografia e cores da família) ou vocês têm/encomendam uma logo?
- **D2 — Endereço:** manter `nexopulse.up.railway.app` por enquanto, ou já usar um
  domínio da MD (ex.: `hub.<domínio da MD>`)? Domínio próprio exige acesso ao DNS.
- **D3 — Módulos não contratados:** ficam **ocultos** para o cliente, ou aparecem como
  **vitrine** ("Conheça o NexoGuard — fale com a MD")?
- **D4 — Visão do Master:** a MD também quer poder "ver como o cliente" (enxergar o Hub
  exatamente como um cliente vê), para suporte?
