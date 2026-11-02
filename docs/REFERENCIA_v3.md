# Amorino Escalas — Referência v3 (Junho 2026)

Documento de referência completo da aplicação, actualizado após as sessões de desenvolvimento de Junho 2026. Substitui `REFERENCIA_v2.md`.

App web para gerir escalas de turnos das 5 lojas Amorino do Porto e Gaia, com ~30 funcionários por semana, motor de sugestões baseado em histórico, sistema de trocas/doações, e painéis de supervisão.

---

## Índice

1. [Visão geral e estado actual](#1-visão-geral-e-estado-actual)
2. [Stack e arquitectura](#2-stack-e-arquitectura)
3. [Layout dos ficheiros](#3-layout-dos-ficheiros)
4. [Sistema de design (base.html)](#4-sistema-de-design-basehtml)
5. [Papéis e permissões](#5-papéis-e-permissões)
6. [Modelo de dados](#6-modelo-de-dados)
7. [Lojas e horários](#7-lojas-e-horários)
8. [Motor de sugestões](#8-motor-de-sugestões)
9. [Sistema de trocas e doações](#9-sistema-de-trocas-e-doações)
10. [Descargas](#10-descargas)
11. [Rotas HTTP](#11-rotas-http)
12. [Páginas (templates)](#12-páginas-templates)
13. [Segurança](#13-segurança)
14. [Desenvolvimento e testes](#14-desenvolvimento-e-testes)
15. [Glossário](#15-glossário)
16. [Resolução de problemas](#16-resolução-de-problemas)

---

## 1. Visão geral e estado actual

App Flask monolítica em produção interna na Amorino Porto/Gaia. Estado a Junho 2026:

- **Motor de sugestões com histórico e cobertura** — dois sinais: (1) quem preenche lacunas de cobertura (tarde/noite quando o turno da manhã já está preenchido), (2) que horas recomendar com base nos horários reais das semanas anteriores (evita sugerir 08:00-17:00 quando a loja já tem um 08:00-16:30).
- **Sistema de trocas/doações completo** — trocas directas e abertas; nas doações, cada candidatura segue imediatamente para a administração, que escolhe e aprova o destinatário. Só a recusa de uma troca aberta devolve as propostas ao autor; a recusa de uma doação fecha o pedido.
- **Painel do supervisor remodelado** — 6 tabs: Resumo (hoje), Cobertura/Semana (matriz lojas×dias), Respostas (disponibilidades), Descargas, Trocas (a decidir), Semanas.
- **Equipa redesenhada** — avatares com iniciais, paleta uniforme da marca, accento de estado lateral.
- **A Minha Escala** e **home do funcionário** mostram total de horas, barra de progresso do contrato, e strip Mon→Sun com estados (com turno, descarga, hoje, feriado, folga).
- **Eliminação de blocos de horário** — ignora uso em semanas passadas; só bloqueia se o bloco estiver em uso em semanas actuais ou futuras.
- **Cobertura da semana (tab Semana)** — por defeito mostra a semana actual (não a semana a construir), com selector para mudar.

---

## 2. Stack e arquitectura

- **Backend:** Python 3.12+ · Flask 3 · SQLAlchemy 2 (ORM declarativo)
- **Base de dados:** SQLite (`amorino.db`; configurável via `AMORINO_DB_PATH`)
- **Frontend:** Jinja2 server-rendered + JS vanilla (sem React, sem build step)
- **Auth:** sessões Flask com cookies `HttpOnly`, `SameSite=Lax`, `Secure` em produção
- **PINs:** PBKDF2-SHA256 (`werkzeug.security`). PINs legacy plaintext auto-migram no próximo login.
- **CSRF:** token por sessão, validado em todos os POST/PATCH/DELETE (excepto endpoints em `_CSRF_EXEMPT_ENDPOINTS`). O `base.html` monkeypatcha `fetch` para injectar o header `X-CSRF-Token` automaticamente.
- **iCal:** geração RFC 5545 manual + QR code via `qrcode` em modo SVG puro
- **PDFs:** ReportLab
- **Parser histórico:** `pdfplumber` (só em `import_history.py`, não em runtime)

---

## 3. Layout dos ficheiros

```
amorino/
├── app.py                   ~4166 linhas — Flask app, 56 rotas, toda a lógica de negócio
├── config.py                  123 linhas — LOJAS, HORARIOS_BASE, RANK_*, TROCA_*, SLOT_*
├── extensions.py                9 linhas — db = SQLAlchemy() (singleton)
├── models.py                  255 linhas — 11 classes db.Model
├── seed_data.py               109 linhas — dados de seed (funcionários e turnos iniciais)
├── import_history.py          ~400 linhas — script CLI para importar PDFs históricos
├── gerar_pdf.py               — gerador de PDF da escala (ReportLab)
├── helpers/
│   ├── __init__.py
│   ├── audit.py                32 linhas — audit(action, target, detail)
│   ├── auth.py                149 linhas — hash_pin, verify_pin, CSRF, rate-limit
│   ├── scoring.py             269 linhas — motor histórico: _load_prior_weeks, _score_candidate
│   └── time_helpers.py        212 linhas — week_*, shift_*, horario_to_slot, dia_label_to_idx
└── templates/
    ├── base.html               — layout global, design system, CSS tokens, nav
    ├── login.html
    ├── index.html              — home do funcionário (dashboard pessoal)
    ├── escala.html             ~2700 linhas — construtor de escala (página mais complexa)
    ├── disponibilidade.html    — funcionário submete disponibilidade semanal
    ├── equipa.html             — roster da equipa (supervisor + read-only para staff)
    ├── minha_escala.html       — escala pessoal do funcionário
    ├── trocas.html             — central de trocas/doações
    ├── supervisor_dashboard.html — painel do supervisor (6 tabs)
    ├── supervisor_conflitos.html
    ├── supervisor_disponibilidades.html
    ├── descargas.html          — gestão de descargas por loja/dia
    └── erro.html
```

### helpers/ em detalhe

- **`auth.py`** — `hash_pin`, `verify_pin`, `safe_pin_eq`, `login_rate_limited`, `login_record_failure`, `login_clear_attempts`, `get_csrf_token`, `csrf_token_from_request`
- **`time_helpers.py`** — `week_from_monday`, `next_mondays`, `week_codigo`, `week_label`, `horario_to_slot`, `_dia_label_to_idx`, `_shift_bounds`, `_shifts_overlap`, `_shift_hours`, `semana_start_date`, `semanas_chronological`. Sem dependências de Flask/DB excepto `semanas_chronological` (recebe `SemanaConfig` como argumento).
- **`scoring.py`** — `_load_prior_weeks` (retorna `affinity`, `pair_affinity`, `loja_day_counts`, `band_typical`), `_historical_affinity`, `_score_candidate`, `_score_best_slot_for_loja`, `p75_ceiling`. **Importa `db` e `models`** — requer app context.
- **`audit.py`** — só `audit(action, target, detail)`. Engole excepções para não quebrar o request.

Aliases com prefixo `_` existem para compatibilidade (`_shift_hours = shift_hours`, etc.). Código novo usa nomes sem prefixo.

---

## 4. Sistema de design (base.html)

Tokens CSS em `:root` usados em todos os templates:

```css
--brown: #2c1810        /* principal, títulos */
--brown2: #4a2e1b       /* secundário */
--orange: #c4862b       /* acções primárias */
--orange2: #e8a832      /* gradiente */
--bg: #f7f4f1           /* fundo da página */
--bg2: #ede8e3          /* fundo de cards secundários */
--bg3: #e2dbd3          /* fundo de elementos interiores */
--white: #ffffff
--text: #1c1109
--muted: #6b5545
--border: #d4c4b0
--border2: #c4b09a
--green: #2a6030  --green-bg: #e8f2ea
--red: #8a1f1f    --red-bg: #f5e8e8
--amber: #7a4e00  --amber-bg: #fff0d0
--blue: #1a3070   --blue-bg: #e8edf8
--r: 6px  --r2: 10px  --r3: 14px
--s1: (shadow suave)  --s2: (shadow média)
```

Classes utilitárias globais: `.btn` / `.btn-primary` (gradiente brown) / `.btn-orange` / `.btn-ghost` / `.btn-sm` / `.btn-danger`, `.card`, `.page-title`, `.nav-pill`, `.nav-link` / `.nav-badge`, `modal-*`, toast global (`toast(msg, isError)`), `openModal(id)` / `closeModal(id)`.

O `fetch` é monkeypatchado para injectar `X-CSRF-Token` automaticamente em todos os pedidos.

---

## 5. Papéis e permissões

Dois tipos de utilizador, controlados por `session['user_sigla']` e `session['supervisor']`.

### Funcionário (staff)
- `/` — dashboard pessoal (próximo turno, horas da semana, strip Mon→Sun, barra contrato)
- `/minha-escala` — escala pessoal completa com strip de semana, horas, barra de contrato
- `/disponibilidade` — submeter disponibilidade semanal
- `/escala` — ver escala geral (read-only)
- `/equipa` — ver equipa (sem horas individuais nem edição)
- `/trocas` — criar, responder e acompanhar trocas/doações

### Supervisor (ADMIN)
Tem acesso a tudo o acima, mais:
- `/supervisor` — painel principal (6 tabs)
- `/escala` em modo de edição — construtor de escala completo
- `/supervisor/disponibilidades` — ver e marcar disponibilidades
- `/supervisor/conflitos` — resolver conflitos
- `/equipa` em modo de edição — gerir equipa (add/edit/delete)
- Todas as rotas `/api/*` de escrita

**Regra de autenticação:** supervisor = `session['supervisor']` truthy E `session['user_sigla'] == 'ADMIN'` (constante `ADMIN_SIGLA`). Decoradores: `@supervisor_required`, `@login_required`.

---

## 6. Modelo de dados

### Turno
```
semana: str (FK semana_config.codigo)
funcionario: str (FK funcionarios.sigla)
dia: str  ex: "Seg 8"
loja: str  ex: "carmelitas"
horario: str  ex: "08:00-16:30"
```

### Funcionario
```
sigla: str PK  ex: "JFC"
nome: str
horas_contrato: int  (0 para turnos/avença)
tipo_contrato: str  "contrato" | "turnos"
rank: str  "trainee" | "staff" | "shift_supervisor" | "store_manager"
loja_gerida: str  ID da loja gerida, ou "" quando não atribuída (só store_manager)
pin: str  (hash PBKDF2 ou plaintext legacy)
ical_token: str
```

### Disponibilidade
```
semana: str
funcionario: str
dia: str
tipo: str  "disponivel" | "preferencia" | "indisponivel" | ""
lojas_pref: str  (JSON lista de loja IDs)
slots_pref: str  (JSON lista: "manha"|"tarde"|"noite")
slots_indisp: str
descarga_opt: bool  (opt-in independente para descarga)
```

### SemanaConfig
```
codigo: str PK  ex: "8-14jun"
label: str  ex: "8 a 14 de Junho"
dias_json: str  JSON lista de {label, date, feriado, feriado_nome}
ativa: bool  (semana a construir — só uma activa)
```
**Propriedades computadas** (via `_hydrate_semana`): `dias_list`, `day_labels`, `feriados`, `descarga_set`, `descarga_map`, `descarga_lojas_set`.

### Descarga
```
semana, funcionario, dia, loja, horario
```

### HorarioBloco / HorarioSupressed
Horários customizados adicionados e blocos base suprimidos por loja.

### TrocaPedido
```
de_sigla: str       (quem pede)
para_sigla: str     (colega da troca; nas doações, '' até à aprovação final)
turno_origem_id: int FK
turno_destino_id: int FK nullable  (NULL = doação; SET = troca)
motivo: str
estado: str  ver estados abaixo
resposta_colega, resposta_supervisor: str
tipo: str  "direto" | "aberto_doacao" | "aberto_troca"
origem_snapshot_json: str  (identidade do turno publicado para validar a doação)
```

**Estados de TrocaPedido:**
```
pendente_colega   → colega ainda não respondeu (pedido directo)
aberto            → pedido na central; nas doações, sem candidaturas activas
pendente_supervisor → troca aceite/escolhida ou doação com candidaturas, aguarda administração
aprovado          → transferência ou troca aprovada e publicada
negado            → administração recusou toda a doação ou a troca directa
recusado_colega   → colega recusou (pedido directo)
cancelado         → autor cancelou
expirado          → turno da doação começou, foi removido ou sofreu alterações incompatíveis
```

**Rejeição pela administração:**
Uma doação recusada fecha como `negado`, incluindo todas as candidaturas activas; uma troca directa também fecha como `negado`. Só uma troca aberta (`aberto_troca`) regressa a `aberto` para o autor escolher outra proposta: a pessoa recusada fica `vetado` e as outras propostas voltam de `rejeitada` a `pendente`.

### TrocaCandidatura
```
troca_id: int FK
sigla: str
comentario: str
turno_oferecido_id: int FK nullable
status: str  "pendente" | "aceite" | "rejeitada" | "retirada" | "vetado" | "negada" | "cancelada" | "expirada"
```
`vetado` = supervisor recusou especificamente esta pessoa para este pedido.
Nas doações, `aceite` identifica a aprovação final; `rejeitada` significa que outra candidatura foi aprovada. `negada`, `cancelada` e `expirada` acompanham o encerramento da doação. Retiradas anteriores conservam-se.

### LojaHeadcountTarget
```
loja: str
dia_idx: int  (0=Seg … 6=Dom)
target: int
```

### AuditLog
```
ts, user_sigla, action, target, detail
```
Append-only. Acções registadas: login/logout, turno_add/remove/move, troca_*, horario_add/delete/suppress, funcionario_*.

---

## 7. Lojas e horários

### Lojas (config.py → LOJAS)
| id | nome | sigla (short) |
|----|------|---------------|
| carmelitas | Carmelitas | CAR |
| clerigos | Clérigos | CL |
| norte | Norte Shopping | NS |
| gaia | Gaia | GAIA |
| mbs | Mercado Bom Sucesso | MBS |

### Slots de horário (horario_to_slot)
- `manha` — início < 12:00
- `tarde` — início 12:00–14:59
- `noite` — início ≥ 15:00
- `""` — especiais (ex: "Provas Práticas")

### Horários base (HORARIOS_BASE em config.py)
Cada loja tem uma lista ordenada de blocos de horário. Exemplos para Carmelitas:
`08:00-16:30`, `08:00-17:00 (+0,5)`, `Provas Práticas`, `10:00-18:30`, `12:30-21:00`, `13:00-19:00`, `13:00-22:00 (+0,5)`, `14:00-22:30`, `15:00-00:30 (+1)`, `16:00-00:30`, `16:00-01:30 (+1)`, `17:00-23:00`

Blocos podem ser adicionados (`HorarioBloco`) ou suprimidos (`HorarioSupressed`) por loja via UI do supervisor. A eliminação de um bloco só é bloqueada se estiver em uso em **semanas actuais ou futuras** — semanas passadas são ignoradas (histórico preservado).

---

## 8. Motor de sugestões

### Visão geral

O endpoint `/api/empties/<semana>/<path:dia>` devolve, para cada loja, a lista de pessoas sugeridas para preencher os lugares em falta. O motor combina 5 sinais.

### scoring.py — _load_prior_weeks

Carrega as últimas `HISTORICAL_LOOKBACK_WEEKS=12` semanas (mínimo `HISTORICAL_MIN_WEEKS=2` para activar). Retorna:

```python
{
  'n_weeks': int,
  'affinity': {(sigla, loja, weekday_idx, slot): score_0_1},
  'pair_affinity': {(siglaA, siglaB, loja, weekday_idx): score_0_1},
  'loja_day_counts': {(loja, weekday_idx): avg_people_per_week},
  'band_typical': {(loja, weekday_idx, horario_exact): avg_people_per_week}
}
```

`band_typical` é a chave nova (Junho 2026) — permite recomendar horários que correspondem ao padrão real da loja (ex: 1 opener 08:00-16:30 + 1 closer 14:00-22:30), em vez de sugerir near-duplicates.

### Scoring de candidatos (_score_candidate)

Escala de pontuação (aproximada):
```
prefere a loja/slot        +1000
quer trabalhar             +500
disponível                 +300
cobertura-gap fit          +0..+280  (sub-dominante: candidatos que cobrem slots a descoberto)
histórico/afinidade        +0..+250
workload adjustment        ±200
pair affinity              +0..+75
```

O sinal de **cobertura-gap fit** (Junho 2026): candidatos que podem trabalhar num slot sub-coberto (tarde/noite quando manhã já está cheia) sobem no ranking face a quem só recobraria um slot já preenchido.

### _recommend_horario

Escolhe o horário concreto a recomendar. Ranking interno:
1. Blocos reais (ignora especiais como "Provas Práticas")
2. Blocos abaixo do nível histórico típico (`band_typical`) — reconstrói o padrão da loja
3. Parte do dia menos coberta (`slot_count`)
4. Bloco específico menos coberto (`band_count`)
5. Best slot do candidato (tiebreak)
6. Ordem na lista base (estável)

Os contadores `band_count`/`slot_count` são mutados a cada sugestão dentro do mesmo batch, para que 3 sugestões se distribuam pelo dia em vez de empilharem no mesmo bloco.

---

## 9. Sistema de trocas e doações

### Tipos de pedido
- **Directo** (`tipo="direto"`) — A pede trocar dois turnos com B. Estado inicial: `pendente_colega`.
- **Doação aberta** (`tipo="aberto_doacao"`) — A publica o turno na central sem escolher colega. Estado inicial: `aberto`; a administração escolhe o destinatário na aprovação.
- **Troca aberta** (`tipo="aberto_troca"`) — A oferece troca à central; candidatos oferecem o seu turno. Estado inicial: `aberto`.

### Fluxo directo
```
A cria → pendente_colega
B aceita → pendente_supervisor → supervisor aprova → aprovado (turnos trocados)
                               → supervisor recusa → negado
B recusa → recusado_colega
A cancela → cancelado
```

### Fluxo de doação
```
A publica → aberto
Primeira candidatura → pendente_supervisor (para_sigla continua vazio)
Outras pessoas candidatam-se → mantém pendente_supervisor e visibilidade na Central
Todas as candidaturas retiradas → aberto
Administração escolhe e aprova candidatura → aprovado (só então transfere o turno)
Administração recusa toda a doação → negado
A cancela antes da aprovação → cancelado
Turno começa, é removido ou alterado de forma incompatível → expirado
```
O autor nunca escolhe o destinatário. A candidatura aprovada fica `aceite`; as outras activas ficam `rejeitada`. O turno mantém-se atribuído ao doador até à aprovação. A expiração usa o início do turno em `Europe/Lisbon`.

Selecção, validação final, transferência, resultados das candidaturas, publicação e auditoria usam uma única transação SQLite com `BEGIN IMMEDIATE`. Validam-se identidade publicada e rascunho, elegibilidade, sobreposições de turnos/descargas e a regra existente de mudança de loja. Avisos de disponibilidade, horas e descanso continuam consultivos. A publicação altera apenas o destinatário do turno autorizado; outros rascunhos ficam por publicar.

A migração idempotente remove nomeações antigas, repõe candidaturas interessadas para decisão administrativa e preserva o histórico concluído, sem aprovar transferências. Procedimento e detalhes: [DONATIONS.md](DONATIONS.md).

### Fluxo de troca aberta (`aberto_troca`)
```
A abre → aberto
B,C candidatam-se (TrocaCandidatura status="pendente")
A escolhe B → B.status="aceite", C.status="rejeitada", pedido→pendente_supervisor
Supervisor aprova → aprovado
Supervisor recusa B → pedido→aberto, B.status="vetado", C.status="pendente" (volta para A escolher)
A escolhe C → pendente_supervisor
Supervisor aprova → aprovado
```

### Endpoints chave
- `POST /api/trocas` — criar troca directa; chamadas antigas sem turno em troca publicam uma doação na Central, ignorando a nomeação
- `POST /api/trocas/abrir` — abrir pedido marketplace
- `POST /api/trocas/<tid>/responder` — colega aceita/recusa (directo)
- `POST /api/trocas/<tid>/candidatar` — candidatar-se; doações aceitam candidaturas em `aberto` e `pendente_supervisor` e entram logo na fila administrativa
- `POST /api/trocas/<tid>/escolher` — autor escolhe uma proposta de troca; doações devolvem 403
- `POST /api/trocas/<tid>/decidir` — administração aprova/recusa (`{aprovar: bool, nota: str}`); aprovar doação exige também `candidatura_id` pendente do próprio pedido, recusar fecha toda a doação
- `POST /api/trocas/<tid>/cancelar`, `POST /api/trocas/<tid>/retirar-candidatura`

### Serialização
`_serialize_troca(pedido, include_candidaturas=False)` devolve o dict completo usado pelo frontend. Inclui `is_doacao`, `is_aberto_troca`, `is_aberto_doacao`, `n_candidaturas`, `n_vetados`, `turno_origem`, `turno_destino`, nomes dos intervenientes.

`_serialize_candidatura(cand)` inclui `status_label`, `is_vetado`, hora local da candidatura e, para doações activas, `fatal`, `conflitos` e `avisos`. Administração e autor veem a lista completa; os restantes funcionários apenas a própria candidatura.

---

## 10. Descargas

### Modelo
`SemanaConfig.descarga_config` — JSON `{dia_label: {loja_id: "HH:MM-HH:MM"}}` — fonte de verdade para quais lojas têm descarga em cada dia e em que janela horária.

`Disponibilidade.descarga_opt` — boolean, opt-in independente do tipo de disponibilidade.

`Descarga` (tabela) — atribuições concretas: `(semana, funcionario, dia, loja, horario)`.

### Lógica
- `_descarga_config(semana)` — parse do JSON de config
- `_hydrate_semana(semana)` — popula `descarga_map`, `descarga_set`, `descarga_lojas_set`
- A janela horária por célula pode ser editada em modal; recusa se sobrepuser turno já atribuído

---

## 11. Rotas HTTP

### Páginas (GET)
| Rota | Acesso | Descrição |
|------|--------|-----------|
| `/` | funcionário | Home / dashboard pessoal |
| `/login` | público | Login |
| `/escala` | todos | Calendário geral; modo edição para supervisor |
| `/minha-escala` | funcionário | Escala pessoal |
| `/disponibilidade` | funcionário | Submeter disponibilidade |
| `/equipa` | todos | Roster da equipa |
| `/trocas` | todos | Central de trocas |
| `/supervisor` | supervisor | Painel (6 tabs) |
| `/supervisor/disponibilidades` | supervisor | Revisão de disponibilidades |
| `/supervisor/conflitos` | supervisor | Conflitos |
| `/escala/descargas` | supervisor | Gestão de descargas |

### API de escrita (POST/PATCH/DELETE)
| Rota | Método | Descrição |
|------|--------|-----------|
| `/api/turno/add` | POST | Adicionar turno |
| `/api/turno/remove/<tid>` | DELETE | Remover turno |
| `/api/turno/move/<tid>` | POST | Mover turno (loja/dia/horário) |
| `/api/funcionario/add` | POST | Adicionar funcionário |
| `/api/funcionario/delete/<sigla>` | DELETE | Remover funcionário |
| `/api/funcionario/update-horas` | PATCH | Actualizar horas contrato |
| `/api/funcionario/update-rank` | PATCH | Actualizar rank e, opcionalmente, loja_gerida numa só transação |
| `/api/funcionario/update-tipo` | PATCH | Mudar tipo contrato |
| `/api/funcionario/reset-pin/<sigla>` | POST | Revogar PIN, sessões e calendário; emitir convite privado de recuperação válido por 24 horas |
| `/api/funcionario/convite/<sigla>` | POST | Emitir convite privado de ativação, apenas para contas sem acesso |
| `/acesso` | GET/POST | Definir PIN com convite de uso único e CSRF; ver [procedimento de acesso](EMPLOYEE_ACCESS.md) |
| `/api/horario/add` | POST | Adicionar bloco de horário |
| `/api/horario/delete` | POST | Eliminar/suprimir bloco de horário |
| `/api/headcount-targets` | POST/PATCH | Definir alvos de pessoal |
| `/api/descarga/add` | POST | Atribuir descarga |
| `/api/descarga/remove/<did>` | DELETE | Remover atribuição de descarga |
| `/api/descarga/horario` | POST | Editar janela horária da descarga |
| `/api/trocas/<tid>/decidir` | POST | Supervisor aprova/recusa troca |
| `/api/copiar-semana` | POST | Copiar turnos entre semanas |
| `/supervisor/semana/nova` | POST | Criar nova semana |
| `/supervisor/ativar-semana/<codigo>` | POST | Activar semana |

### API de leitura (GET)
| Rota | Descrição |
|------|-----------|
| `/api/empties/<semana>/<dia>` | Sugestões para o dia (todos os alvos + suggested staff) |
| `/api/headcount-targets/suggest` | Sugestão de alvos baseada no histórico |
| `/api/workload/<semana>` | Carga de trabalho por funcionário |
| `/api/trocas/contagem` | Contagem de trocas pendentes |
| `/api/upcoming-weeks` | Próximas semanas |
| `/cal/<sigla>/<token>.ics` | Feed iCal pessoal |
| `/supervisor/exportar-pdf` · `…-individual/<sigla>` · `…-loja/<loja>` | Exportações PDF da escala |
| `/supervisor/exportar-horas-csv?semana=<codigo>` | Exportação de horas (CSV, folha de pagamento) — turnos+descargas, horas de feriado, total e diferença vs contrato; `;`/vírgula/BOM para Excel-PT |

---

## 12. Páginas (templates)

### base.html
Layout global. Blocos: `title`, `head`, `topbar_extra`, `content`, `scripts`. Inclui nav com links e badge `trocas_pendentes` (injectado via `@app.context_processor`).

### index.html (home do funcionário)
- Próximo turno ou estado da semana
- Stats strip: horas semana, turnos, disponibilidade
- **Horas da semana card** — barra de progresso `contract_hours` (nova, Junho 2026); cor verde normal / amber se acima do contrato; não aparece para `tipo=turnos`
- Strip Mon→Sun com estados de cada dia
- Widget iCal/QR

### minha_escala.html
- **Week summary novo (Junho 2026):** semana label, nº turnos (excl. descargas), total horas (excl. descargas), barra de progresso do contrato, strip Mon→Sun
- Strip: verde = turno, amber = só descarga, anel laranja = hoje, número vermelho = feriado, opacidade reduzida = passado
- Cards por turno com loja, horário, opções de troca

### supervisor_dashboard.html
6 tabs (JS auto-descoberta via `[data-tab]`/`[id="tab-*"]`):

1. **Resumo** — painel do dia de hoje (by loja, turnos, pessoas)
2. **Semana** (data-tab="cobertura") — matriz lojas×dias; verde/amber/vermelho/neutro; hoje destacado; selector de semana (`?cob=`); por defeito mostra semana actual (`get_semana_for_employee()`), não a semana a construir
3. **Respostas** — progresso de disponibilidades com badge de pendentes
4. **Descargas** — configuração por dia e loja com grid interactivo
5. **Trocas** — lista de pedidos `pendente_supervisor` com botões Aprovar/Recusar inline; rejeitar devolve ao autor com o colega vetado
6. **Semanas** — gestão de semanas (criar, activar, copiar)

### equipa.html
- Summary strip com accent colorido (verde/amber/vermelho/neutro por estado)
- Cards com avatar circular de iniciais (nome próprio + apelido), gradiente brown para contrato / gradiente orange para turnos
- Accent lateral: verde=ok, amber=abaixo, vermelho=acima, laranja=turnos
- Rank pills na paleta da marca (sem azuis off-brand)
- Modo de edição com controles inline (horas, rank, tipo, PIN reset, remover)
- Pesquisa live por nome/sigla

### escala.html
A página mais complexa (~2700 linhas). Dois modos: read-only (todos) e edição (supervisor).

Ambos usam setas de semana anterior/seguinte e intervalo de datas com ano. A navegação é calculada no backend por datas reais, limitada às semanas existentes autorizadas; funcionários só veem escalas publicadas. O parâmetro `semana` conserva a selecção, os filtros e os links da barra. As setas nos limites ficam desactivadas; navegar não cria, activa nem publica semanas.

Funcionalidades em modo edição:
- Grid de lojas com cards de turno drag-and-drop
- Painel "Por preencher" (Construir o dia) com sugestões do motor
- Gestão de blocos de horário por loja (add/suprimir)
- Targets de pessoal (modal)
- Pesquisa de funcionários com autocomplete
- Gestão de descargas (link)

### trocas.html
- Secções: A decidir (pedidos para o utilizador), Em curso, Central (marketplace aberto), Histórico
- Doações mostram "Aceitar turno" e, após candidatura, "A aguardar decisão da administração", com opção de retirada; o autor acompanha o estado e pode cancelar
- A fila administrativa apresenta todas as candidaturas activas, horas e conflitos/avisos, com "Escolher e aprovar" e recusa de toda a doação; o histórico identifica destinatário e decisão
- Trocas abertas reabertas mostram a nota da supervisão e a pessoa vetada; candidaturas `vetado` aparecem como "recusado/a pela supervisão" na lista do autor

---

## 13. Segurança

Detalhes completos em `SECURITY.md`. Resumo:

- Todos os POST/PATCH/DELETE exigem CSRF token válido (header `X-CSRF-Token` ou body `_csrf`)
- Rate limiting no login: X tentativas falhadas → lock de 5 minutos
- Sessões com TTL de 12h
- PINs guardados como hash PBKDF2-SHA256
- `@login_required` e `@supervisor_required` em todas as rotas relevantes
- `AuditLog` regista todas as acções sensíveis

---

## 14. Desenvolvimento e testes

### Correr a app localmente
```bash
AMORINO_DB_PATH=/tmp/dev.db python app.py
```

### Testes
```bash
AMORINO_DB_PATH=/tmp/test.db pytest tests/
```

### Padrões de teste (Flask test client)
```python
# Autenticação
with client.session_transaction() as s:
    s.update(user_sigla='ADMIN', supervisor=True, _csrf='t',
             login_at=datetime.now(timezone.utc).isoformat())

# Headers para POST
H = {"X-CSRF-Token": "t", "Content-Type": "application/json"}

# Ler JSON da resposta
data = response.get_json()   # NÃO .json() — é property, não method

# Templates em testes
app.app.jinja_loader.searchpath.insert(0, "templates")

# DB limpa para testes de lógica de semanas
Turno.query.delete(); SemanaConfig.query.delete(); db.session.commit()
```

### Notas sobre seed_db()
O `seed_db()` insere ~46 funcionários E algumas semanas por defeito com turnos e `LojaHeadcountTarget`. Testes que testam lógica de semanas (ex: `get_semana_for_employee`) devem limpar as tabelas relevantes antes.

### Verificação antes de entregar
```bash
python3 -m py_compile app.py helpers/scoring.py
# Balanço Jinja:
grep -c "{% if\|{% for\|{% block" template.html  # deve igualar endifs/endfors/endblocks
# JS:
node --check extracted_js.js  # strip {{ }} → null, {% %} → '' antes de verificar
```

---

## 15. Glossário

| Termo | Definição |
|-------|-----------|
| **Sigla** | Identificador único do funcionário: 2-4 letras maiúsculas. Ex: `JFC`, `BG`, `HGA` |
| **Loja** | Uma das 5 lojas: `carmelitas`, `clerigos`, `norte`, `gaia`, `mbs` |
| **Short / Sigla de loja** | Abreviatura: `CAR`, `CL`, `NS`, `GAIA`, `MBS` |
| **Horário** | Janela temporal: `'08:00-16:30'`, `'16:00-00:30 (+1)'`. `(+X)` = horas extra pagas |
| **Slot** | Categoria: `manha` (<12h), `tarde` (12–15h), `noite` (≥15h) |
| **Dia** | Label semana-relativo: `'Seg 8'`, `'Dom 14'` |
| **Semana** | `SemanaConfig` com `codigo` (ex: `'8-14jun'`) e `label` (ex: `'8 a 14 de Junho'`) |
| **Semana activa** | A semana com `ativa=True` — a que está a ser construída pelo supervisor |
| **Semana actual** | A semana que contém hoje — obtida via `get_semana_for_employee()` |
| **Avença / Turnos** | Funcionário sem contrato fixo (`tipo_contrato='turnos'`, `horas_contrato=0`) |
| **Contrato** | Funcionário com X horas/semana (`tipo_contrato='contrato'`) |
| **Headcount target** | Nº esperado de pessoas numa `(loja, dia_semana)` — tabela `LojaHeadcountTarget` |
| **Affinity** | Score 0..1 histórico de quão frequentemente uma pessoa trabalhou numa célula |
| **band_typical** | Média de pessoas/semana num horário exacto `(loja, weekday, horário_str)` |
| **slot_cov / band_count** | Contadores de cobertura actuais usados para distribuir sugestões pelo dia |
| **Troca directa** | A pede a B específico para trocar (`tipo='direto'`) |
| **Doação aberta** | A oferece o turno à central (`tipo='aberto_doacao'`) |
| **Troca aberta** | A e B trocam via marketplace (`tipo='aberto_troca'`) |
| **Vetado** | Colega recusado pela supervisão para um pedido específico — `TrocaCandidatura.status='vetado'` |
| **Descarga** | Período de recepção de mercadoria numa loja; funcionários podem fazer opt-in |
| **HorarioBloco** | Bloco de horário customizado adicionado a uma loja |
| **HorarioSupressed** | Bloco base suprimido (ocultado) pelo supervisor |
| **CSRF token** | Token por sessão para prevenir cross-site request forgery |
| **Audit log** | Tabela append-only com todas as acções sensíveis |

---

## 16. Resolução de problemas

### "Sem turnos esta semana" mas existem turnos
1. Verifica que `SemanaConfig.ativa=True` é a semana esperada
2. Verifica a sigla com que estás logado
3. Os turnos estão associados ao código de semana correcto?

### O motor de sugestões sugere horários errados (near-duplicates)
- Verifica `n_weeks` em `/api/headcount-targets/suggest` — se <2, sem histórico
- Com histórico: o `band_typical` guia as recomendações. Se o histórico tiver dados anómalos (ex: muitos openers), as sugestões reflectem isso
- Sem histórico: o motor cai em distribuição de cobertura pura (spread manhã/tarde/noite)

### Não consigo eliminar um bloco de horário
- Se diz "em uso em semanas actuais ou futuras": há turnos com esse bloco em semanas que ainda não acabaram. Remove esses turnos na escala primeiro.
- Se diz isso mas só há turnos passados: bug (não deve acontecer após Junho 2026)

### O tab "Semana" mostra dados da semana errada
- Por defeito mostra a semana que contém hoje (`get_semana_for_employee()`), não a semana activa
- Para ver a semana a construir, usa o selector no tab ou abre a semana activa explicitamente

### Rejeitar uma troca ficou como "negado" em vez de reabrir
- É o comportamento esperado para uma doação ou troca directa: a recusa fecha o pedido.
- Só uma troca aberta (`aberto_troca`) reabre para o autor escolher outra proposta. Ver [DONATIONS.md](DONATIONS.md).

### "database is locked" nos testes
- Outro processo tem a DB aberta: `lsof | grep amorino.db`
- Usa `AMORINO_DB_PATH=/tmp/novo_ficheiro_$$.db` para isolar completamente

### O CSRF está a falhar
- Pedidos fetch usam o monkeypatch automático do `base.html`? (Só funciona em páginas que extendem base.html)
- Chamadas directas à API precisam de `X-CSRF-Token` no header
- A sessão expirou? (TTL 12h)

### Não consigo fazer login
- Rate limited: espera 5 minutos ou reinicia o processo
- PIN errado: supervisor faz reset em `/equipa` (modo edição → Reset PIN)
- Sigla não existe: supervisor adiciona em `/equipa`

### Import de PDF histórico falhou
- "Could not determine week start date": o PDF não tem o formato CAEM standard (a primeira página deve ter a tabela de escala, não um cover)
- "unknown sigla XYZ": cria o funcionário na UI antes de re-correr o import

---

*Documento actualizado a Junho 2026. Para segurança específica, ver `SECURITY.md`. Para importar histórico, ver `IMPORT_HISTORY_README.md`.*
