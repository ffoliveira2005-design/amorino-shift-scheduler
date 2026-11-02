# Deployment — Amorino Escalas

Como pôr a app a servir pessoal real. O [SECURITY.md](../SECURITY.md) descreve a
postura de segurança e tem o checklist; este documento é o procedimento.

Atualização de preparação do piloto (7 de Outubro de 2026): usar o procedimento
executável e os exemplos em [OPERATIONS.md](OPERATIONS.md). A migração e o
rollback estão em [SCHEDULE_PUBLICATION.md](SCHEDULE_PUBLICATION.md); o ensaio
fictício medido está em [RECOVERY_REHEARSAL.md](RECOVERY_REHEARSAL.md).
Estas verificações locais não constituem instalação nem backups externos em produção.

---

## 1. O que serve a app

**Não** usar `python app.py` num host acessível. Esse é o servidor de
desenvolvimento do Werkzeug: a própria documentação dele diz para não o usar em
produção. Usar o `wsgi.py`:

```bash
python wsgi.py
```

Ou entregar `wsgi:application` a qualquer servidor WSGI:

```bash
waitress-serve --port 8000 wsgi:application
```

```bash
gunicorn --workers 1 --threads 8 wsgi:application
```

### Um processo, vários threads

Não é afinação, é um requisito de correção. Dois motivos:

1. **O rate limit do login vive em memória do processo**
   (`helpers.auth.LOGIN_ATTEMPTS`). Com N workers há N contadores independentes,
   portanto N vezes o orçamento de tentativas: com 4 workers, o limite de 5
   tentativas em 5 minutos do supervisor passa a 20.
2. **As migrações correm no import** (`db.create_all()` + `ALTER TABLE` no
   `app.py`). Vários processos a importar ao mesmo tempo competem pela mesma
   alteração de schema.

Threads partilham o espaço de endereçamento, portanto um contador e um import.
O SQLite em modo WAL aguenta as ligações concorrentes.

---

## 2. Variáveis de ambiente

| Variável | Obrigatória | Efeito |
|---|---|---|
| `AMORINO_SECRET_KEY` | **sim** | Assina cookies de sessão e tokens CSRF. Sem ela fica o default, que está no repositório — qualquer pessoa que o leia forja uma sessão de supervisor. Gerar: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `AMORINO_SUPERVISOR_PIN` | **sim** | PIN do supervisor. Com `AMORINO_HTTPS=1` a app **recusa arrancar** se for fraco. |
| `AMORINO_HTTPS` | **sim** (`1`) | Ativa cookies `Secure` e o header HSTS. Só faz sentido com TLS mesmo à frente. |
| `AMORINO_TRUST_PROXY` | se houver proxy | Lê `X-Forwarded-Proto/Host/For`. Ver secção 4. |
| `AMORINO_DB_PATH` | **sim em produção** | Caminho absoluto fora do checkout; o preflight recusa o default na raiz. |
| `AMORINO_SEED` | **sim** (`0`) | Impede a inicialização com nomes/escalas do código-fonte. |
| `AMORINO_PROCESSES` | **sim** (`1`) | Declara a arquitetura de um processo, verificada pelo preflight. |
| `AMORINO_STORAGE` | **sim** (`local`) | Declara disco persistente local verificado pelo operador. |
| `AMORINO_HOST` | não | Interface. Default `127.0.0.1` — manter assim atrás de um proxy. |
| `AMORINO_PORT` | não | Porta. Default 8000 no `wsgi.py`. |
| `AMORINO_THREADS` | não | Threads do waitress. Default 8. |
| `AMORINO_DEBUG` | **nunca** | Expõe a consola interativa do Werkzeug, que dá execução de código arbitrário a quem alcance um traceback. |
| `TZ` | **sim**, se o host não estiver em hora de Lisboa | `TZ=Europe/Lisbon`. A app usa a hora local do processo para tudo o que é "hoje" e "agora": a coluna do dia na escala, o painel "em direto" do supervisor, os turnos a decorrer, a saudação. Num servidor em UTC isto fica uma hora atrasado no verão — o dia muda à 01:00 de Lisboa. (No Windows, acertar o fuso do sistema.) |

---

## 3. Base de dados

SQLite, em modo **WAL** (configurado no `app.py`, aplicado em cada ligação).
Sem WAL, uma escrita bloqueia todos os leitores: o supervisor a gravar a escala
para toda a gente que a está a ler.

**Restrição de deployment:** o WAL precisa de um sistema de ficheiros real
(mapeia um índice em memória ao lado da base de dados). **Não funciona sobre
SMB/NFS.** Se o `amorino.db` alguma vez for para uma partilha de rede, o WAL tem
de sair — e nessa altura a concorrência volta a ser um problema. Manter a base
de dados em disco local.

### Backups

```bash
python scripts/backup_db.py
```

Escreve `backups/amorino-<data>.db`, verifica a cópia com `integrity_check` e
mantém as 14 mais recentes (`--keep`, `--dir`, `--db` para mudar).

Usa a API de backup online do SQLite, não uma cópia de ficheiro: `copy
amorino.db backup.db` pode apanhar uma transação a meio e, em WAL, ignora em
silêncio todos os commits ainda no ficheiro `-wal`. O resultado parece válido e
falha quando é preciso. O script pode correr com a app a servir.

Agendar (Windows, diariamente às 03:00):

```bash
schtasks /create /tn "Amorino backup" /tr "python C:\caminho\scripts\backup_db.py" /sc daily /st 03:00
```

`backups/` está no `.gitignore` — são cópias completas dos dados do pessoal.

---

## 4. Reverse proxy

O proxy termina TLS e fala HTTP simples com a app, portanto a app vê o esquema
como `http` e o host como aquilo a que está bound. Tudo o que derive do URL do
pedido fica errado de uma maneira que não se vê em desenvolvimento — em
particular `url_for(..., _external=True)`, que constrói o link de subscrição do
calendário que o funcionário copia para o telemóvel. Sem correção, esse link diz
`http://`, e os clientes de calendário não respeitam HSTS: o token do feed
passaria em claro a cada consulta.

`AMORINO_TRUST_PROXY=1` liga o `ProxyFix`, que repõe os valores reais. Também dá
ao `request.remote_addr` o IP de quem chamou em vez do IP do proxy, portanto o
rate limit do login volta a contar a coisa certa.

**Só ligar isto se existir mesmo um proxy a reescrever esses headers**, e com a
app bound a loopback. Os headers são forjáveis por quem alcance a app
diretamente; a trustar-se, o rate limit por IP contorna-se com um header novo em
cada tentativa.

O proxy tem de definir:

```nginx
proxy_set_header Host              $host;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-Host  $host;
proxy_set_header X-Forwarded-For   $remote_addr;
proxy_set_header X-Forwarded-Prefix "";
```

---

## 5. Monitorização

`GET /healthz` → `{"ok": true}` (200) ou `{"ok": false}` (503).

Não pede sessão, porque um monitor não tem nenhuma, e por isso não revela nada:
sem versão, sem hostname, sem contagens. Responde à falha que um teste de TCP
não vê — o processo continuar a aceitar ligações enquanto todas as páginas dão
500 porque a base de dados desapareceu.

Os logs vão para stderr. Encaminhá-los para algo persistente (journald, um
ficheiro com rotação); um erro que só existiu no terminal de alguém não é um
erro investigável.

---

## 6. Desempenho

Já tratado na app, mas convém saber porquê:

- **Compressão gzip** para HTML/CSS/JSON. `/escala` são ~195 KB que passam a
  ~24 KB. A app não depende de o proxy estar configurado para isso; se o proxy
  comprimir, vê o `Content-Encoding` já definido e passa o corpo à frente.
- **Ficheiros estáticos com cache de um ano.** O default do Flask é `no-cache`,
  que revalida ~140 KB de CSS e fontes em cada navegação. As folhas de estilo
  levam uma versão no URL (`?v=…`, derivada do mtime+tamanho), portanto um
  deploy invalida-as. As fontes e os ícones **não** levam versão: os URLs das
  fontes têm de ser idênticos aos do `preload` e aos do `@font-face`, senão cada
  face é descarregada duas vezes. Se alguma vez uma fonte ou ícone for
  substituído, **mudar o nome do ficheiro** — a cache de um ano não tem como ser
  invalidada de outra maneira.

---

## 7. Verificado com

Versões testadas em conjunto (setembro 2026). Os limites no
[requirements.txt](../requirements.txt) permitem patches e minors, e param no
major seguinte.

| Pacote | Versão |
|---|---|
| Python | 3.14.5 |
| flask | 3.1.3 |
| werkzeug | 3.1.8 |
| flask-sqlalchemy | 3.1.1 |
| sqlalchemy | 2.0.49 |
| holidays | 0.96 |
| qrcode | 8.2 |
| reportlab | 4.5.0 |
| waitress | 3.0.2 |

---

## 8. Procedimento de deploy

1. `pytest` verde (ver [CLAUDE.md](../CLAUDE.md) — correr contra uma base de
   dados descartável, nunca a de produção).
2. `python scripts/backup_db.py` antes de mexer em nada.
3. Instalar dependências: `pip install -r requirements.txt`.
4. Confirmar as variáveis de ambiente da secção 2 no serviço/unit.
5. Arrancar via `wsgi.py`, um processo.
6. `curl -f https://.../healthz` deve devolver 200.
7. Entrar como supervisor e como funcionário, e confirmar que o link de
   calendário em "O meu calendário" começa por `https://`.
