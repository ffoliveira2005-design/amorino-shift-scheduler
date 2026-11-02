# Amorino Escalas — Postura de Segurança

Documento descreve as defesas implementadas, decisões de design, e o que tu (operador) precisas de fazer para usar isto em produção em segurança.

## 🔑 Variáveis de ambiente OBRIGATÓRIAS em produção

| Variável | Para quê | Valor |
|----------|----------|-------|
| `AMORINO_SECRET_KEY` | Assina cookies de sessão e tokens CSRF | String aleatória longa (≥ 32 bytes hex). Gera com: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `AMORINO_SUPERVISOR_PIN` | PIN do supervisor | Um valor forte. **Com `AMORINO_HTTPS=1` a app RECUSA arrancar** se este PIN for fraco (lista negra, dígitos repetidos, sequências, ou `ADMIN`) — a mesma regra aplicada aos funcionários na ativação por convite. Gerar com: `python -c "import secrets; print(secrets.token_hex(8))"` |
| `AMORINO_HTTPS` | Ativa `Secure` cookies + HSTS | `1` apenas se a app está servida por HTTPS (com reverse proxy) |

**Sem o primeiro:** ao arrancar, a app loga um WARNING enorme. Cookies de sessão são vulneráveis a tampering se um atacante adivinhar a chave default.

**Sem o segundo:** o PIN `1234` é trivialmente quebrável.

**Sem o terceiro:** cookies viajam sem a flag `Secure`. Em HTTPS, alguém num wifi público pode roubar cookies se a HSTS não estiver ativa.

## ⚙️ Variáveis opcionais (default = a opção segura)

| Variável | Default | Quando mudar |
|----------|---------|--------------|
| `AMORINO_DEBUG` | desligado | **Nunca em produção.** `AMORINO_DEBUG=1` liga o debugger interativo do Werkzeug, que dá **execução de código arbitrário** a quem consiga provocar um traceback. Só para trabalho local. |
| `AMORINO_HOST` | `127.0.0.1` | Só se precisas do servidor de dev acessível fora da máquina. Em produção usa o `wsgi.py` (waitress/gunicorn), não o `app.run` do `app.py` — ver [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). Atrás de um proxy, manter em loopback. |
| `AMORINO_TRUST_PROXY` | desligado | `1` **apenas** se a app está mesmo atrás de um reverse proxy que reescreve `X-Forwarded-*`. Liga o `ProxyFix`: repõe o esquema, o host e o IP de origem reais, o que corrige o link de subscrição do calendário (que sem isto diz `http://`) e faz o rate limit contar o IP de quem chamou em vez do IP do proxy. Ligado sem proxy, qualquer pessoa forja os headers e o limite de tentativas por IP deixa de existir. Ver [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) §4. |
| `AMORINO_PORT` | `5000` | Porta do servidor de dev. |

## 🛡️ Defesas implementadas

### 1. Autenticação

- **PINs hashed com pbkdf2-sha256** (não plaintext). Hashes legacy ainda funcionam e são automaticamente upgradados no próximo login.
- **Contas sem PIN não autenticam e não podem ser reclamadas pela sigla.** O supervisor verifica a identidade e emite um convite privado em `/equipa`.
- **PIN mínimo de 4 caracteres**, com rejeição de valores triviais (`is_weak_pin`), na ativação/recuperação por convite. PINs existentes continuam válidos, incluindo valores legacy, e são atualizados para PBKDF2 ao autenticar.
- **Convites de 256 bits**, emitidos por supervisão com CSRF, válidos por 24 horas e utilizáveis uma única vez. A BD guarda apenas SHA-256 do segredo. Emitir outro convite invalida o anterior. A conclusão verifica novamente a expiração depois do hash do PIN e consome o convite por atualização condicional atómica.
- **Reset revoga imediatamente o PIN anterior, as sessões e o token de calendário.** `access_version` é validada em cada pedido de funcionário. O novo PIN só pode ser definido com o convite privado. Ações e alterações de credenciais são registadas na mesma transação, sem segredos. Procedimento: [docs/EMPLOYEE_ACCESS.md](docs/EMPLOYEE_ACCESS.md).
- **O PIN do supervisor obedece à MESMA regra.** É a conta de maior valor no
  sistema (leitura+escrita sobre os dados de toda a gente), por isso não faria
  sentido recusar `1234` a um funcionário e distribuí-lo como default do
  supervisor. Em postura de produção (`AMORINO_HTTPS=1`) a app **recusa
  arrancar** com um PIN de supervisor fraco; sem essa variável assume-se dev
  local e apenas avisa, para o default continuar a servir testes e execuções
  locais.
- **Rate limiting** de tentativas falhadas:
  - 8 tentativas em 5 minutos por (sigla, IP)
  - 5 tentativas em 5 minutos para a sigla `ADMIN` (mais restritivo)
  - 30 tentativas em 1 hora globalmente por sigla (mesmo de IPs diferentes)
  - O IP vem de `request.remote_addr`. `X-Forwarded-For` é controlado pelo
    cliente e **só** é lido com `AMORINO_TRUST_PROXY=1` — caso contrário bastava
    mudar o header a cada tentativa para anular o limite por IP.
  - Os contadores são em memória e **limitados** (`LOGIN_ATTEMPTS_MAX_KEYS`):
    as chaves vêm de input do atacante, por isso envelhecidas são descartadas e
    há um teto absoluto. Consultar o limite nunca cria uma entrada — só um
    falhanço registado cria.
  - **Nota:** por serem em memória, os contadores são por processo. Com vários
    workers o limite efetivo multiplica-se pelo nº de workers.
- **Mensagem de erro genérica** ("Sigla ou PIN incorreto") — não revela se a sigla existe.
- **A página de login não é cacheável** (`no-store`) — de outro modo uma cópia
  antiga podia ficar guardada no browser ou num proxy.
- **A página de login não expõe a lista de pessoal.** A sigla é escrita, não
  escolhida de uma lista. Antes, `/login` renderizava um picker com a sigla E o
  nome real de todos os funcionários, sem autenticação — o que (a) expunha dados
  pessoais de toda a equipa a qualquer pessoa que abrisse a página e (b) tornava
  a mensagem de erro genérica inútil, já que as siglas válidas eram oferecidas.
- **Constant-time PIN verification** (`hmac.compare_digest`) — não dá pistas por timing.
- **Sigla desconhecida ainda executa um hash** "fake" para igualar tempos de resposta.

### 2. Gestão de sessão

- **Session cookie:** `HttpOnly` (JS não lê), `SameSite=Lax` (não vai em cross-site POST), `Secure` quando `AMORINO_HTTPS=1`.
- **Lifetime de 12 horas**, enforced no servidor (não confiamos em `Max-Age` do cliente).
- **Rotação de session ID no login** — previne session fixation (atacante planta cookie, vítima loga in, atacante usa o mesmo cookie).
- **Validação por request** — se a sigla na sessão já não existe na BD (funcionário foi removido), a sessão é limpa.
- **`session.permanent = True`** para que o lifetime seja respeitado.
- **Um segundo cookie, `amorino_trocas_vistas`**, guarda quando a pessoa abriu a
  página Trocas pela última vez neste dispositivo (`SIGLA|timestamp UTC`), para
  o contador de novidades (troca aprovada, recusada, aceite…). Não é uma
  credencial nem assinado: o valor é do cliente e só entra nessa contagem, e um
  valor que nomeie outra pessoa é ignorado — alterá-lo só pode esconder ou
  mostrar as novidades da própria pessoa. `HttpOnly`, `SameSite=Lax`, e
  `Secure` com `AMORINO_HTTPS=1`, como o de sessão. A supervisão não o recebe.

### 3. CSRF (Cross-Site Request Forgery)

- **Token por sessão**, gerado com `secrets.token_urlsafe(32)`.
- **Validação em TODAS as requests state-changing** (POST/PUT/PATCH/DELETE) — exceto `/login`. `/acesso` cria uma sessão anónima e exige o respetivo token CSRF também antes da ativação.
- Aceita o token via:
  - Header `X-CSRF-Token` (preferido para JS)
  - Campo de formulário `csrf_token`
  - Campo em JSON body `csrf_token`
- **`base.html` injeta automaticamente** o token em todas as fetches via wrapper de `window.fetch`. Só faz isto para same-origin (nunca vaza o token cross-site).
  A origem é comparada através de `new URL(...).origin`, incluindo URLs relativas
  ao protocolo, objetos URL e Request. Prefixos parecidos com o domínio da
  aplicação não são considerados a mesma origem. O wrapper preserva os headers
  existentes de objetos Request. A regressão é executada em JavaScript com Node
  em `tests/js/fetch_policy.cjs`, chamada por `tests/test_basics.py`.
- **`SameSite=Lax` é uma 2ª camada** que bloqueia POST cross-site mesmo sem CSRF token.

### 4. Headers de segurança (em todas as respostas)

```
X-Content-Type-Options: nosniff
X-Frame-Options: DENY                       (bloqueia clickjacking)
Referrer-Policy: same-origin
Permissions-Policy: geolocation=(), microphone=(), camera=(), payment=(), usb=()
Content-Security-Policy: default-src 'self'; ...frame-ancestors 'none'...
Cache-Control: no-store, private, max-age=0  (em páginas HTML autenticadas)
Strict-Transport-Security: ...               (só com AMORINO_HTTPS=1)
```

CSP atual permite `'unsafe-inline'` em scripts e styles (legacy code base usa inline). Mais hardening possível: migrar para nonce-based CSP — TODO futuro. As fontes são **self-hosted** (`static/app-fonts.css`), por isso o CSP já **não** precisa de autorizar `fonts.googleapis.com` / `fonts.gstatic.com` — `style-src`/`font-src` são `'self'`. Bónus: deixa de vazar o IP de cada visitante para a Google (questão de RGPD na UE).

### 4b. `debug` desligado por omissão

`app.run()` corre com `debug=False` salvo se `AMORINO_DEBUG=1`. Isto evita expor a consola interativa do Werkzeug (= execução remota de código) caso a app seja arrancada diretamente. **Nunca** definas `AMORINO_DEBUG` em produção.

### 4c. Páginas de erro sem fugas

403/404/413/500 são servidos via `erro.html` (ou JSON em `/api/*`) por `@app.errorhandler` — o utilizador nunca vê a stack trace do Werkzeug. O handler de 500 faz `db.session.rollback()` antes de renderizar.

### 4b. XSS

- **Templates:** Jinja2 com autoescape ligado. Não há nenhum `|safe` nem
  `Markup()` em todo o `templates/` — todo o texto livre (nome, motivo, nota,
  comentário, respostas) é escapado pelo servidor.
- **DOM:** o que é construído em JS a partir de dados da API usa `textContent`
  para valores de texto livre. Em particular o autocomplete da `/escala` usa
  `textContent` para `nome` — com `innerHTML` um nome como
  `<img src=x onerror=...>` executava no browser de todos os funcionários que
  pesquisassem (XSS armazenado, plantado por quem tem o painel de supervisão).
- **`escHtml()` (`base.html`)** para os sítios onde uma template string é
  mesmo a forma mais clara de montar um bloco. Escapa `& < > " '`, o que cobre
  tanto a injeção de tags como a fuga de um valor de atributo
  (`" onmouseover=…`). Estavam por escapar: o `nome` na gaveta de resultados da
  pesquisa, o `horario` nos chips de sugestão e no detalhe de slot da `/escala`,
  `nome`/`horario` nos popovers do dashboard, e o `horario` nos seletores de
  troca da `/trocas` e `/minha-escala` — este último renderizado no browser de
  **todos** os funcionários. `nome` e `horario` são escritos no painel de
  supervisão, por isso o alcance é supervisão → equipa inteira.
- **Regra:** nunca concatenar dados da API em `innerHTML` sem `escHtml()`.
  O diálogo de calendário também escapa a sigla e os URLs usados em atributos;
  `tests/js/calendar_dialog.cjs` verifica esses valores e a recuperação do diálogo.
  Os campos de texto livre são `nome`, `texto` (nota de disponibilidade,
  escrita pelo próprio funcionário), `nota`, `horario`, `comentario` e `motivo`.
- **Handlers inline (`onclick="…"`):** o autoescape **não** protege um valor
  posto dentro de uma string JavaScript num atributo. O Jinja transforma `'` em
  `&#39;`, mas o browser descodifica a entidade **antes** de correr o JS, pelo
  que `onclick="f('{{ nome }}')"` com o nome `D'Almeida` é um erro de sintaxe —
  e com um nome construído, código. A Equipa fazia isto com o `nome` nos botões
  Reset PIN / Remover (que não funcionavam para ninguém com apóstrofo no nome);
  agora os botões passam `this` e a função lê sigla e nome do cartão.
  **Regra:** texto livre nunca entra num handler inline; só identificadores com
  conjunto de caracteres garantido pelo servidor (a `sigla`, o `id` da loja, e
  o `horario` — ver a secção 5).

### 5. Validação de input

- **Length caps** em todos os campos relevantes (sigla 10, nome 80, horario 60,
  nota 100, texto/nota de disponibilidade 200, motivo/comentário 300, etc.).
  O SQLite **não** impõe a largura da coluna, por isso o corte tem de ser feito
  no servidor: sem ele, a nota de disponibilidade aceitava tudo até ao teto do
  body (256 KB) e voltava a ser renderizada no painel de supervisão.
- **Whitelist de valores** para campos enumerados (loja, tipo_contrato, rank, etc.)
- **Caracteres permitidos** em sigla: alfanumérico + `.`, `-`, `_`
- **Caracteres permitidos** em `horario` (blocos novos, turnos novos e turnos
  movidos): letras (incluindo acentuadas), dígitos, espaço e `: + - , . ( )`.
  É o que um rótulo de turno precisa (`08:00-17:00 (+0,5)`, `Provas Práticas`)
  e nada que feche uma string ou abra uma tag. O `horario` é interpolado em
  handlers inline na grelha da `/escala`, por isso esta é a defesa que conta —
  sem ela, um apóstrofo num bloco partia todos os botões dessa linha.
  `/api/turno/move` passou também a validar a loja de destino e a cortar os
  campos como o `/api/turno/add`.
- **Sigla `ADMIN` reservada** — não permite criar funcionário com esse nome
- **Body cap:** `MAX_CONTENT_LENGTH = 256 KB`. Bodies maiores → 413.
- **Validação de referências**: ao adicionar turno, verifica que loja, semana, e funcionário existem.

### 6. Autorização

- **`@login_required`**: utilizador autenticado (employee ou supervisor).
- **`@supervisor_required`**: só ADMIN. Aos pedidos feitos por `fetch()` — todo
  o `/api/*` **e** os pedidos JSON para `/supervisor/*` (criar/ativar semana,
  grelha de descargas) — responde sempre JSON, nunca a página de login:
  **401** `sessao_expirada` sem sessão (a mesma resposta do `@login_required`,
  por isso o wrapper do `base.html` reencaminha para o login) e **403** com a
  sessão de um funcionário. Antes era 403 "Não autenticado" sem sessão — o
  supervisor com a sessão caducada via um erro sem caminho de volta — e os
  endpoints fora de `/api/` devolviam a página de login, que o `res.json()` não
  conseguia ler.
- **`@login_required`**: para endpoints `/api/*` devolve **401 JSON**
  (`{ok:false, sessao_expirada:true}`) em vez de redirect. Um redirect é
  respondido com a *página* de login; o `fetch()` segue-o em silêncio e o
  `await res.json()` de cada call site rebentava no HTML — o botão não fazia
  nada e ninguém percebia que a sessão de 12h tinha caducado. O wrapper de
  `fetch` no `base.html` trata o 401 num único sítio: avisa e reencaminha para
  `/login?next=<página atual>`. Páginas HTML continuam a redirecionar.
- **Verificação de existência** em cada request — se o funcionário foi removido, a sessão é invalidada.
- **Endpoints individuais** (e.g. `/supervisor/exportar-pdf-individual/<sigla>`) verificam que `sigla == current_user_sigla()` OR `is_supervisor()`.

### 6b. Trocas de turno

Quem pode fazer o quê (tudo verificado no servidor, não na interface):

| Ação | Quem |
|------|------|
| Pedir a um colega / publicar na central | Só o dono do turno. O turno do colega pedido em troca tem de ser dele. |
| Aceitar / recusar um pedido direto | Só o colega a quem foi pedido. |
| Cancelar / retirar da central | Só o autor, enquanto o pedido está aberto. |
| Candidatar-se a um pedido da central | Qualquer funcionário exceto o autor — e não quem a supervisão já recusou nesse pedido. O turno oferecido tem de ser do candidato. |
| Escolher uma candidatura de troca efetiva | Só o autor; `/escolher` recusa todas as doações com 403. |
| Escolher e aprovar uma doação | Só a supervisão, indicando uma `candidatura_id` ativa que pertença ao pedido. |
| Aprovar / recusar | Só a supervisão. |
| `/api/trocas/colegas?turno=` | Só o dono do turno (ou a supervisão). Lista a equipa com quem está ocupado nesse horário — informação que qualquer funcionário já vê na `/escala`. |

Integridade, igual em todos os passos (pedido, resposta, candidatura, escolha
e aprovação):

- **Um turno só pode estar num pedido aberto de cada vez** — como turno dado,
  como turno pedido em troca, ou oferecido numa candidatura ativa. Antes, o
  mesmo turno podia ir a dois colegas em simultâneo (ou a um colega e à
  central); os dois aceitavam e a segunda aprovação falhava com "o turno mudou
  de dono".
- **Turnos cujo dia já passou não entram em pedidos**, e os pedidos por
  responder expiram quando o dia passa.
- **Doações não atribuem destinatário antes da aprovação.** A primeira candidatura
  passa `aberto` a `pendente_supervisor`; ambos aceitam candidaturas e permanecem
  na Central. A última retirada repõe `aberto`. A administração escolhe e aprova
  numa ação, ou recusa toda a doação. O autor pode cancelar antes da decisão.
  O caminho legado de doação direta publica na Central e ignora a nomeação.
- **Decisões exigem JSON com `aprovar` booleano explícito.** Um valor omitido,
  texto como `"false"`, números ou um corpo que não seja objeto devolvem 400;
  não são convertidos numa aprovação ou recusa. A nota deve ser texto e a
  candidatura de doação deve ser um inteiro positivo, sem arredondamento ou
  conversão de booleanos. Pedidos inválidos não alteram a atribuição, os
  resultados das candidaturas, o histórico de decisões ou a versão publicada.
- **SQLite `BEGIN IMMEDIATE` antes das leituras decisivas**, em todas as mutações
  de trocas e na expiração, serializa aprovação, cancelamento, retirada e
  candidaturas duplicadas mesmo sem o bloqueio Python. Escolha, validação final,
  atribuição, resultados de todas as candidaturas, publicação seletiva e auditoria
  integram a mesma transação; uma falha de armazenamento reverte o conjunto.
- **Identidade publicada guardada no pedido**, incluindo data real, titular,
  loja, horário e nota. Doações expiram à hora de início em `Europe/Lisbon`, mesmo
  pendentes de administração, ou quando o turno é removido ou alterado. Datas e
  alterações do turno em rascunho também invalidam o pedido. A aplicação e a
  aprovação verificam elegibilidade e sobreposições no rascunho e na publicação;
  indisponibilidade, excesso de horas e seis dias de trabalho são avisos consultivos.
- **Sobreposições são recusadas no primeiro passo que as vê** (incluindo
  janelas de descarga), com a mesma regra que a aprovação aplica.

Privacidade:

- **O motivo de um pedido direto fica entre as duas pessoas e a supervisão.**
  Quando a supervisão recusava um pedido direto, ele era convertido num pedido
  público da central — motivo incluído — sem o autor o ter decidido. Agora um
  pedido direto recusado fecha. Um pedido da central é público por escolha de
  quem o publica, e o motivo com ele.
- **As candidaturas a um pedido da central** (quem se candidatou e o
  comentário que escreveu) só são devolvidas ao autor e à supervisão. O
  `/api/central` entregava-as a qualquer funcionário; cada um vê agora apenas a
  sua.

### 7. Audit log

Tabela `audit_log` regista ações sensíveis:
- `login_ok`, `login_fail`, `login_rate_limited`, `logout`
- `pin_set` (upgrade legacy), `pin_reset`, `enrollment_issued`, `enrollment_complete`, `enrollment_fail`, `enrollment_rate_limited`
- `escala_publicada`; `schedule_releases` guarda versões, responsável, instante UTC, motivo e snapshot autorizado; `schedule_changes` guarda alterações de preparação com valores antes/depois na transação da gravação.
- `funcionario_add`, `funcionario_delete`, `funcionario_rank`, `funcionario_horas`, `funcionario_tipo`
- `semana_ativar`, `semana_copy`, `descarga_set`
- `horario_add`, `horario_delete`, `horario_suppress`, `horario_restore`

Cada entrada tem: timestamp UTC, actor (sigla), IP, action, target, detail livre.

**Leitura:** não há UI (de propósito — se o painel for comprometido, o atacante não deve poder apagar o seu próprio rasto).

```bash
sqlite3 amorino.db "SELECT * FROM audit_log ORDER BY id DESC LIMIT 50;"
```

### 8. Open-redirect prevention

`safe_referrer()` e o `next` param em `/login` rejeitam:
- URLs absolutas (com `://`)
- Paths protocolo-relativos (`//attacker.com/`)
- Paths que contêm `\` — o browser normaliza `/\evil.com` para `//evil.com`,
  por isso começar por um único `/` não chega. O `next` sempre cobriu isto; o
  `safe_referrer()` não cobria, apesar de este documento descrever os dois da
  mesma maneira. Corrigido.

Só aceitam paths que começam com um único `/`.

A revisão de disponibilidades pode preservar pesquisa, filtro e pessoas abertas
através de campos limitados no formulário. O destino é sempre construído com
`url_for('supervisor_disponibilidades')`, na semana da resposta revista; não
aceita um URL de retorno. Os filtros são validados e as siglas abertas têm de
pertencer às respostas dessa semana.

### 8b. Enumeração de siglas no feed iCal

`/cal/<sigla>/<token>.ics` é público por necessidade (os clientes de calendário
não enviam cookies). Respondia **404** para uma sigla inexistente e **403** para
um token errado — o que permitia a qualquer pessoa, sem autenticação, testar
siglas e ler de volta quais são pessoal real com calendário. É a mesma
enumeração de roster contra a qual o `/login` foi endurecido. Agora **todos** os
casos devolvem o mesmo 404 com o mesmo corpo. O `.svg` já se comportava assim;
os dois têm de andar a par.

### 9. SQL injection

Todas as queries usam ORM (SQLAlchemy) ou parameterized SQL. O único `text()` ad-hoc (em migrations) usa apenas constantes do código, nunca input do utilizador.

## ⚠️ Não defendemos contra

- **Insider threat puro**: o supervisor com PIN tem acesso total — é por design.
- **Phishing**: se um utilizador escreve o PIN num site fake do "Amorino", o atacante tem o PIN. Defesa principal: educação dos funcionários + URL bookmarked.
- **Compromise do servidor**: se a máquina for invadida, todos os dados (incluindo audit log) ficam expostos. Defesa principal: hosting decente + backups.
- **Privilege escalation by code execution**: este código corre como um utilizador único — sem isolamento entre supervisores e funcionários ao nível do OS.
- **BREACH** (extrair o token CSRF medindo o tamanho de respostas comprimidas).
  As respostas HTML passaram a ser comprimidas com gzip (`/escala` são ~195 KB
  lidos ao telemóvel), e essas páginas trazem o token CSRF numa `<meta>` e
  refletem texto livre (`nome`, `nota`). Este é o mesmo trade-off que qualquer
  reverse proxy com `gzip on` já traria — a compressão passou a ser feita na
  app para não depender da configuração do proxy, não porque o risco seja novo.
  O que o torna impraticável aqui é o `SameSite=Lax`: o ataque precisa de
  muitos pedidos autenticados cross-origin com o tamanho da resposta a ser
  observado, e o cookie de sessão não acompanha pedidos cross-site que não
  sejam navegação de topo. Se algum dia o `SameSite` for relaxado, esta linha
  tem de ser reavaliada em conjunto.

## 🔄 Operação

### Mudar o PIN do supervisor

```bash
# Linux/Mac
export AMORINO_SUPERVISOR_PIN='novo-pin-seguro'

# Windows PowerShell
$env:AMORINO_SUPERVISOR_PIN = 'novo-pin-seguro'
```

Os funcionários NÃO são afetados — só o login como ADMIN.

### Reset de PIN de um funcionário

Via `/equipa`, verificar a identidade, emitir recuperação e entregar o segredo apenas à pessoa verificada. O PIN e as sessões anteriores deixam de funcionar de imediato. O convite expira em 24 horas; não deixa a conta reclamável. Seguir [EMPLOYEE_ACCESS.md](docs/EMPLOYEE_ACCESS.md).

### Investigar atividade suspeita

```sql
-- Tentativas de login falhadas nos últimos 7 dias
SELECT ts, actor, ip, action, target, detail
FROM audit_log
WHERE action LIKE 'login_%'
  AND ts > datetime('now', '-7 days')
ORDER BY ts DESC;

-- Eliminações de funcionários (alguma vez)
SELECT * FROM audit_log
WHERE action = 'funcionario_delete'
ORDER BY ts DESC;

-- Tudo o que o ADMIN fez hoje
SELECT * FROM audit_log
WHERE actor = 'ADMIN' AND date(ts) = date('now')
ORDER BY ts DESC;
```

### Limpar o audit log

Em princípio nunca — é a tua trilha forense. Se ficar gigante (improvável neste use case), arquiva-o:

```bash
sqlite3 amorino.db "SELECT * FROM audit_log WHERE ts < date('now','-90 days')" > archive-pre-90d.txt
sqlite3 amorino.db "DELETE FROM audit_log WHERE ts < date('now','-90 days')"
```

## 🧪 Testes de segurança

`tests/test_basics.py` inclui os testes específicos de segurança:

- CSRF: rejeita sem token, aceita com header, aceita com form/json, GET exempt
- Login: rate limit (default + admin), generic error, PIN hashing, legacy upgrade, contas vazias bloqueadas, convites com rejeição de PINs fracos, legacy-weak grandfathering, sigla enumeration prevention, timing
- Login: a página não expõe nomes nem siglas do pessoal, e não é cacheável
- Login: `X-Forwarded-For` forjado não contorna o rate limit
- Rate limit: os contadores mantêm-se limitados; consultar não cria entradas
- Deploy: `app.py` não arranca com `debug=True`
- Deploy: PIN de supervisor fraco impede o arranque com `AMORINO_HTTPS=1`
  (e um PIN forte arranca; em dev só avisa)
- XSS: o nome do funcionário não é injetado como markup no autocomplete
- XSS: `horario` com `'`, `"`, `<` ou `\` é recusado em blocos, turnos e
  movimentos (e rótulos reais continuam a passar); a Equipa não põe nomes
  dentro de handlers inline
- XSS (`tests/test_security_review.py`): `escHtml()` existe e cobre `& < > " '`;
  `nome`, `horario` e a nota de disponibilidade são escapados em todos os sítios
  que os põem em `innerHTML` (escala, dashboard, trocas, minha-escala)
- Input: a nota de disponibilidade é cortada a 200 caracteres no servidor
- iCal: token errado e sigla inexistente devolvem respostas idênticas
- Open redirect: `safe_referrer()` rejeita `/\evil.com`
- Sessão: `/api/*` devolve 401 JSON (não a página de login) e o `base.html`
  reage ao 401; páginas HTML continuam a redirecionar
- Sessão: os endpoints do supervisor respondem JSON a pedidos `fetch()` — 401
  `sessao_expirada` sem sessão, 403 com sessão de funcionário — incluindo os
  JSON fora de `/api/` (`/supervisor/semana/nova`)
- Trocas: `/api/trocas/colegas` só responde ao dono do turno; `/api/central`
  não devolve as candidaturas de outras pessoas
- Cookie `amorino_trocas_vistas`: `HttpOnly` + `SameSite=Lax`, não é enviado
  à supervisão, e um valor com a sigla de outra pessoa é ignorado
- Trocas (`tests/test_trocas_workflow.py`): turnos passados e turnos já
  prometidos noutro pedido são recusados; sobreposições são recusadas no
  pedido, na resposta e na candidatura; um pedido direto recusado pela
  supervisão fecha e não é publicado na central
- Headers: presença, cache-control no-store
- Session: HttpOnly cookie, lifetime, logout clear, deleted-funcionario invalidation
- Audit: pin_reset e funcionario_delete registados
- Authz: API 403 (não redirect), employee bloqueado em endpoints supervisor
- Input: oversized body 413, sigla inválida, ADMIN reservada
- Deploy (`tests/test_deployment.py`): `/healthz` responde sem sessão e não
  revela nada; os headers de segurança sobrevivem à compressão; o `ProxyFix` só
  liga com `AMORINO_TRUST_PROXY` e, ligado, devolve links de calendário `https://`

```bash
pytest tests/ -v
```

## 🌐 Rotas sem autenticação

A lista completa do que responde sem sessão. Qualquer rota nova aqui é uma
decisão de segurança, não uma conveniência.

| Rota | Porquê | O que não revela |
|------|--------|------------------|
| `/login` | Óbvio | Não lista nomes nem siglas do pessoal, e não é cacheável |
| `/acesso` | Ativação/recuperação com convite privado | Sigla sozinha não concede acesso; segredo expirável de uso único, CSRF anónimo, erros genéricos e limite de tentativas; sem segredos no URL |
| `/static/*` | Ficheiros da app | Nada específico de ninguém |
| `/logout` | Terminar sessão não pode exigir uma sessão válida | Limpa a sessão e redireciona; não confirma se havia alguém autenticado |
| `/supervisor/login`, `/supervisor/logout` | Compatibilidade com links antigos | São só redirects para `/login` e `/logout` |
| `/healthz` | Um monitor não tem sessão | Só `{"ok": true\|false}`. Sem versão, sem hostname, sem contagens, sem caminhos — testado em `test_healthz_discloses_nothing` |
| `/cal/<sigla>/<token>.ics` e `.svg` | Clientes de calendário não enviam cookies | O token **é** a credencial. Sigla desconhecida e token errado devolvem a mesma resposta, para o endpoint não poder ser percorrido à procura de siglas reais |

## Visão geral de gestão — 6 de Outubro de 2026

`GET /supervisor/visao-geral` usa `supervisor_required`, com as mesmas regras de
sessão e identidade do painel de supervisão. Não concede permissões a responsáveis
de loja nem a colaboradores. Os indicadores são calculados para a semana
selecionada; uma semana desconhecida devolve 404. Não altera escalas ou disponibilidades.
O ambiente de demonstração é ativado apenas pela configuração `AMORINO_DEMO`
do lançador local, com dados fictícios e autenticação normal; não existe bypass
de login nem uma rota pública para ligar o modo de demonstração.

Remover um colaborador também elimina as suas atribuições de descarga, cancela
pedidos de troca em curso e retira candidaturas ativas na central; pedidos
concluídos permanecem no histórico.

## Publicação e recuperação — 7 de Outubro de 2026

Funcionários, feeds com token, URLs diretos de turnos e PDFs individuais consultam apenas `published_turnos`, `published_descargas` e `published_semanas`. Não existe parâmetro que permita a um funcionário escolher o rascunho. As exportações da supervisão usam a versão publicada por omissão; `rascunho=1` é apenas para supervisão e os PDFs ficam identificados como rascunho. A mudança da semana ativa para disponibilidade não publica nada.

Publicar exige supervisão, CSRF, confirmação explícita, versão esperada e hash do rascunho revisto. Snapshot, projeções e versão são gravados atomicamente. A aprovação de uma troca publica apenas os turnos envolvidos; verifica conflitos tanto no rascunho como na escala autorizada. Uma doação não pode autorizar um turno com identidade ou data alterada em preparação. Leitores e escritores partilham um bloqueio de processo durante o pedido, para não apresentar componentes de versões diferentes. Isto exige a arquitetura documentada de **um processo**; a exclusão entre decisões de doações é adicionalmente garantida pela transação SQLite.

As setas de `/supervisor/escala` e `/escala` usam apenas a lista de semanas autorizada
para a sessão, ordenada pelas datas de calendário. Um `semana` desconhecido ou não
publicado para funcionários devolve 404. A navegação GET não cria, ativa nem publica
semanas e mantém os filtros no URL. Listas vazias ou de uma semana desativam as
setas sem expor semanas em rascunho.

A migração idempotente `donation_admin_review_v1` converte apenas doações em curso:
limpa a nomeação, repõe candidaturas antes escolhidas/preteridas e conserva respostas
já aceites como candidaturas pendentes. Uma mera nomeação sem resposta não cria
candidatura. O histórico concluído permanece intacto. Detalhes: [DONATIONS.md](docs/DONATIONS.md).

Após restauro, revogar convites e feeds recuperados e mudar `AMORINO_SECRET_KEY` antes de reabrir o serviço; reconciliar resets ocorridos depois da cópia, pois um backup pode conter o PIN anterior. O restauro preserva os ficheiros danificados e valida a cópia. Os comandos de operação exigem caminhos explícitos. `wsgi.py` com HTTPS executa preflight antes de importar a app; produção requer `AMORINO_SEED=0`, BD fora do código, um processo e disco local. Os exemplos e ensaios locais não verificam HTTPS, serviços nem cópias externas reais: [OPERATIONS.md](docs/OPERATIONS.md).

Regressões: `tests/test_enrollment.py`, `tests/test_publication.py`, `tests/test_publication_dates.py`, `tests/test_recovery.py` e `tests/test_preflight.py`; executar sempre com `AMORINO_DB_PATH` descartável.

## 📋 Checklist para deployment

O procedimento completo está em [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). Este é
o resumo de segurança.

1. [ ] `AMORINO_SECRET_KEY` definido com valor aleatório (≥ 32 bytes hex)
2. [ ] `AMORINO_SUPERVISOR_PIN` definido com um valor forte (com `AMORINO_HTTPS=1`
   a app recusa arrancar se for fraco — este passo é agora enforced, não confiança)
3. [ ] HTTPS configurado no reverse proxy
4. [ ] `AMORINO_HTTPS=1` definido (ativa Secure cookies + HSTS)
5. [ ] `AMORINO_DEBUG` **não** definido (o default já é seguro — confirma que
   ninguém o pôs no serviço/systemd unit; sem isto fica exposta a consola Werkzeug)
6. [ ] A servir por `wsgi.py`, **não** por `python app.py` (o servidor de dev do
   Werkzeug não é para produção). **Um processo, vários threads** — os contadores
   do rate limit vivem em memória do processo, portanto N workers multiplicam por
   N o orçamento de tentativas do supervisor
7. [ ] `AMORINO_TRUST_PROXY=1` **apenas** se existe mesmo um reverse proxy
   a reescrever `X-Forwarded-*`, e com a app em loopback
8. [ ] Confirmado que o link em "O meu calendário" começa por `https://` (se disser
   `http://`, falta o passo 7 — e o token do feed anda em claro)
9. [ ] Backups regulares do `amorino.db` via `python scripts/backup_db.py`
   (agendado, não à mão), e **um restauro testado pelo menos uma vez**
10. [ ] `/healthz` monitorizado por algo que avise
11. [ ] Logs encaminhados para algum sistema persistente (não só stderr)
12. [ ] `amorino.db` em disco local, não numa partilha de rede (o modo WAL não
    funciona sobre SMB/NFS)
13. [ ] Funcionários informados de não partilhar PINs
14. [ ] PIN do supervisor mudado a cada poucos meses (manualmente — não há rotação automática)
15. [ ] `pytest tests/` passa antes de cada deploy
16. [ ] Preflight aprovado sob a conta do serviço; `AMORINO_SEED=0`, `AMORINO_PROCESSES=1`, `AMORINO_STORAGE=local`, BD fora do checkout
17. [ ] Identidades verificadas, convites entregues em privado e reset ensaiado
18. [ ] Migração ensaiada, semanas futuras revistas/publicadas e PDF autorizado de contingência guardado
19. [ ] Cópia fora do servidor restaurada num ambiente isolado e falha de backup recebida pelo contacto de suporte
