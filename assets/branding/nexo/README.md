# Família Nexo — kit de marca

Kit oficial **proposto** das nove marcas da Família Nexo, ferramentas próprias da MD Soluções Tecnológicas. Ele foi recebido como `Kit_Nexo_Logos_Alta_Resolucao.zip` em 20/10/2026 e copiado sem alterações.

> **Status:** a NexoPulse do kit foi adotada pela Central (branch `feat/nexopulse-marca-kit`, aguardando aprovação), usando os [derivados](#derivados-da-nexopulse-usados-pela-central) abaixo. As outras oito marcas estão armazenadas e ainda não integradas. Os PNGs antigos de `static/brand/` e `static/icons/` continuam no repositório; voltar atrás é trocar as referências.

| Marca | Descrição |
|---|---|
| NexoPulse | Monitoramento e diagnóstico de redes |
| NexoHub | Portal único do cliente |
| NexoGuard | Segurança da rede |
| NexoReport | Relatórios inteligentes |
| NexoAsset | Inventário de TI |
| NexoWeb | Sites e sistemas online |
| NexoDesk | Chamados e suporte |
| NexoBackup | Backup gerenciado |
| NexoPower | Energia e nobreak |

## Estrutura

```
assets/branding/nexo/
├── README.md              este guia
├── manifesto.json         marcas, versões e resoluções (do kit)
├── paleta.json            cores e tipografia (do kit)
├── SHA256SUMS             hash dos 138 arquivos recebidos no kit (sha256sum -c SHA256SUMS)
├── logos/<slug>/          uma pasta por marca, 15 arquivos cada (abaixo)
├── derivados/nexopulse/  versões criadas a partir do kit para a Central (abaixo)
├── referencias/
│   └── painel-visual-aprovado.png   painel do conceito aprovado (referência, não é master)
└── previa/
    ├── index.html                   painel de aprovação (abre os arquivos reais do kit)
    ├── painel-previa.png            captura do painel
    └── comparacao-nexopulse.png     NexoPulse de produção x kit
```

`<slug>`: `nexopulse`, `nexohub`, `nexoguard`, `nexoreport`, `nexoasset`, `nexoweb`, `nexodesk`, `nexobackup` e `nexopower`.

### Arquivos de cada marca

| Arquivo | Formato | Para quê |
|---|---|---|
| `horizontal-claro.svg` | SVG, texto em contornos, viewBox 1320×270 | **Master** da marca horizontal sobre fundo claro |
| `horizontal-escuro.svg` | idem | Master sobre fundo escuro ou azul-marinho |
| `horizontal-claro-editavel.svg` / `horizontal-escuro-editavel.svg` | SVG com `<text>` em Inter Display | Edição de textos. Exige a fonte instalada; não usar em produção |
| `horizontal-claro-2048.png` / `horizontal-escuro-2048.png` | PNG RGBA, 2048 px de largura, fundo transparente | Documentos, apresentações, e-mail, onde SVG não serve |
| `simbolo-claro.svg` / `simbolo-escuro.svg` | SVG, viewBox 160×160 | Símbolo isolado (avatar, carimbo, cards) |
| `simbolo-claro-1024.png` / `simbolo-escuro-1024.png` | PNG RGBA 1024×1024, transparente | Símbolo em raster |
| `app-icon.svg` | SVG 256×256, quadrado arredondado azul-marinho | Ícone de aplicativo vetorial |
| `app-icon-{192,512,1024,2048}.png` | PNG RGBA quadrado, cantos transparentes | PWA (192/512), lojas e materiais (1024/2048) |

Todos os PNGs foram exportados dos SVGs. A validação comparou cada um com o SVG renderizado no Chrome: a diferença média máxima é de 0,33 numa escala de 0 a 255, só serrilhado de borda, ou seja, **não há ampliação de imagem pequena**.

## Paleta

| Cor | Hex | Uso |
|---|---|---|
| Azul-marinho | `#0B1B32` | "Nexo" na versão clara, fundo do ícone e fundos escuros |
| Ciano | `#08ABD0` | Início do gradiente |
| Verde | `#04B89C` | Fim do gradiente |
| Neutro | `#F3F7FB` | Fundo claro de páginas e painéis |

Cores de apoio do kit, em `paleta.json`:
- branco `#FFFFFF`, para "Nexo" na versão escura;
- `#566982`, para o descritor no claro;
- `#BDCCDC`, para o descritor no escuro;
- gradiente intermediário `#05B7BE`.

## Tipografia

- Os **SVGs finais têm o texto convertido em contornos** e não dependem de fonte instalada.
- As versões `-editavel` usam **Inter Display**, com Inter e sans-serif como reserva. Para editar, instale a fonte pelos canais oficiais; nenhum arquivo de fonte é distribuído aqui.
- O painel de referência menciona **Poppins**. O kit adotou Inter Display, e a tipografia não deve ser trocada sem uma proposta aprovada.

## Como usar

- **Claro ou escuro:** a versão `claro` vai sobre branco ou `#F3F7FB`; a `escuro` vai sobre `#0B1B32` ou fundos escuros. Não use a claro sobre fundo escuro, nem o contrário.
- **Proporção:** escale só proporcionalmente. Não distorça, não gire, não recolora e não aplique sombra ou contorno.
- **Área de respiro:** mantenha em volta da marca um espaço livre de, no mínimo, **metade da altura do símbolo**.
- **Tamanho mínimo:**
  - **Marca horizontal:** 440 px de largura. O descritor ("Monitoramento e diagnóstico de redes") ocupa 27 de 1320 unidades, então fica com uns 9 px nesse tamanho. Abaixo disso, use o símbolo ou uma versão sem descritor, que o kit ainda não tem.
  - **Símbolo:** 32 px.
  - **Ícone:** a partir de 48 px.
  - Em **16 px os ícones perdem a leitura** (veja `previa/painel-previa.png`); favicons pedem uma versão simplificada própria.
- **Formato:** o SVG é o master. Use PNG só onde SVG não é aceito.

### Na web

```html
<!-- Preferir <img>: todos os SVGs do kit usam os ids "nexoGrad" e "nexoGradText".
     Colados inline (<svg> dentro do HTML), dois logos na mesma página disputam o mesmo gradiente. -->
<img src="/static/brand/nexo/nexohub/horizontal-claro.svg" alt="NexoHub" width="264" height="54">
```

```css
.logo { background: url("/static/brand/nexo/nexohub/simbolo-escuro.svg") no-repeat center / contain; }
```

`assets/` **não é servido** pela Central (Flask serve `static/`). Na integração, copie para `static/brand/nexo/<slug>/` só os arquivos usados. Não aponte o app para `assets/`.

### PWA

```json
"icons": [
  { "src": "/static/brand/nexo/nexohub/app-icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any" },
  { "src": "/static/brand/nexo/nexohub/app-icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any" }
]
```

- Os ícones têm **cantos transparentes**, porque o quadrado arredondado já faz parte do desenho. Por isso servem como `purpose: "any"`, mas **não como `maskable`**: o Android recortaria a forma de novo e sobraria borda transparente. Para `maskable`, falta uma versão com fundo azul-marinho de ponta a ponta e o símbolo dentro da zona segura (80% centrais).
- A NexoPulse de produção já tem ícones maskable em `static/icons/`.

## Validação

```bash
python3 scripts/validar_marca_nexo.py --pixels
```

**Checagens essenciais** (só a biblioteca padrão; o CI roda pelo `tests/test_marca_nexo.py`):
- as 9 marcas do manifesto, com os 15 arquivos esperados e nenhum sobrando;
- SVG com XML válido e `viewBox`, sem script, `foreignObject`, eventos ou referências externas;
- o final sem `<text>`, e o editável com Inter Display;
- claro e escuro com a **mesma geometria**, só as cores mudando;
- PNG com assinatura e cabeçalho íntegros, RGBA de 8 bits e dimensões exatas;
- paleta oficial presente.

**Checagens de pixel** (com Pillow):
- horizontais e símbolos com cantos transparentes, entre 30% e 99% de fundo transparente e conteúdo visível;
- ícones com cantos transparentes e fundo `#0B1B32`.

**Resultado em 20/10/2026:** 700 checagens ok, nenhum erro. Também verificado:
- no Chrome, as 63 imagens do painel carregam;
- os 45 PNGs batem com os SVGs.

## Diferenças entre a NexoPulse de produção e o kit

Veja `previa/comparacao-nexopulse.png`. O kit é uma **reconstrução vetorial do conceito**, não uma cópia da marca atual:

| Aspecto | Produção (`static/brand/`) | Kit proposto |
|---|---|---|
| Formato | Só PNG (1200×364 e símbolo 512 px) | SVG master e PNGs de até 2048 px |
| Símbolo | Hexágono aberto, com lados grossos interrompidos e nós de tamanhos diferentes. O pulso atravessa o hexágono e tem um pico alto em degradê | Hexágono fechado de traço fino, seis nós iguais e um pulso menor dentro do hexágono |
| Tipografia | Sans geométrica bem pesada | Inter Display Bold, mais estreita |
| Cores | Azul-escuro `#082E56` (no PNG, por volta de `#02325B`), ciano `#05A9DC`, verde `#05A88C` | `#0B1B32`, `#08ABD0`, `#04B89C` |
| Descritor | Não tem | "Monitoramento e diagnóstico de redes" embutido em todas as horizontais |
| Versão escura | O PNG tem bordas serrilhadas e halo claro no "Nexo" | Vetorial, sem artefatos |

As cores mudam pouco, mas o símbolo e a tipografia mudam o suficiente para quem já conhece a marca perceber. Trocar a NexoPulse é uma decisão de marca, não só técnica.

## Derivados da NexoPulse usados pela Central

O kit não tinha três versões que a Central precisa. Elas foram geradas a partir dos masters, por `scripts/gerar_derivados_nexopulse.py` (Playwright com Chrome), sem redesenhar a marca:

| Master (`derivados/nexopulse/`) | O que é | Exportado para `static/brand/nexopulse/` |
|---|---|---|
| `horizontal-sem-descritor-{claro,escuro}.svg` | A horizontal do kit sem o descritor. O nome, em contornos idênticos aos do kit, fica centralizado na altura do símbolo, e o viewBox é recortado | `logo-claro.svg` e `logo-escuro.svg` (barra, login, relatório) |
| `favicon.svg` | Versão simplificada para 16 a 48 px: hexágono e pulso mais grossos, sem os nós, sobre o azul-marinho do ícone | `favicon.svg`, `favicon-{16,32,48}.png` e `favicon.ico` (com os três tamanhos) |
| `app-icon-maskable.svg` | O ícone do kit com fundo de ponta a ponta e o símbolo reduzido (escala 1,1), deixando os nós em 33% do centro, dentro da zona segura de 40% | `maskable-192.png`, `maskable-512.png` e `apple-touch-icon.png` (180) |

Também vão para `static/brand/nexopulse/`, copiados do kit sem alteração:
- `simbolo-{claro,escuro}.svg`, usados na Sala de Operações;
- `icon-192.png` e `icon-512.png`, que são o `app-icon` do kit, para o PWA com `purpose: any`.

Na Central:
- **Barra:** `logo-escuro.svg` no tema escuro e `logo-claro.svg` no claro.
- **Favicon:** SVG, com `.ico` de reserva e `apple-touch-icon`.
- **Manifesto PWA:** ícones `any` do kit e `maskable` derivados.

## O que falta para integrar nos aplicativos

1. ~~Aprovar a NexoPulse do kit~~: aprovada pelo responsável em 20/10/2026. A integração está no PR de `feat/nexopulse-marca-kit`.
2. ~~Versão sem descritor~~, ~~favicon simplificado~~ e ~~maskable~~ da NexoPulse: feitos (veja acima).
3. **As demais marcas:** quando cada aplicativo ganhar telas próprias, vão precisar dos mesmos três derivados. O gerador pode ser estendido para elas.
4. **Paleta da interface (opcional):** a Central ainda usa `#082E56`, `#05A9DC` e `#05A88C` nos botões e destaques, com `theme-color` `#082E56`. A diferença para o kit é pequena e pode ser alinhada depois.

## Relação com outros materiais do repositório

- `docs/familia-nexo/`: a primeira proposta de logos, gerada em 20/10/2026 com a gramática da NexoPulse de produção. Foi preservada como histórico.
- `static/brand/familia/` (só no branch `work/nexohub-inicio`, ainda não mesclado): símbolos dessa primeira proposta, usados no NexoHub em desenvolvimento.
- `static/brand/` e `static/icons/`: a marca NexoPulse **em produção**. Não foi tocada.
