# Operação, recuperação e suporte — Amorino Escalas

Preparado em 7 de outubro de 2026. Ferramentas e exemplos estão disponíveis; instalação em produção, HTTPS público, alertas e cópia fora do servidor continuam por verificar. Nenhum serviço foi comprado, DNS alterado ou dado real enviado.

## Responsabilidades por confirmar

| Responsabilidade | Titular / contacto / substituto |
|---|---|
| Empresa e patrocinador | PENDENTE |
| Supervisor responsável pela escala e contingência | PENDENTE |
| Responsável técnico e suporte | PENDENTE |
| Contacto único para pedidos; canal privado | PENDENTE |
| Escalamento e contacto fora do horário acordado | PENDENTE |
| Dono das contas de alojamento, domínio e backups | Empresa; pessoa autorizada PENDENTE |
| Horário, prazo de resposta e custo de suporte | PENDENTE — não existe SLA aceite |
| Localização, retenção e acesso às cópias | PENDENTE — proposta inicial: 14 cópias diárias |

A empresa verifica identidades e autoriza o tratamento/transferência dos dados. O supervisor decide horários e publica versões. O suporte trata disponibilidade, defeitos, releases e recuperação; não aprova escalas em nome da empresa.

## Instalação repetível preparada

Os exemplos em [ops](../ops/) assumem Linux com systemd, nginx, Python validado e armazenamento local persistente. Caminhos propostos: código `/opt/amorino/app`, Python `/opt/amorino/venv/bin/python`, dados `/var/lib/amorino/amorino.db`, cópias `/var/lib/amorino/backups`, configuração `/etc/amorino.env`. Não colocar SQLite em SMB/NFS. Validar sistema de ficheiros/mounts no servidor; a declaração `AMORINO_STORAGE=local` não os prova.

1. Criar utilizador de serviço `amorino`, sem privilégios administrativos. Pasta de dados/cópias: proprietário `amorino`, modo `0700`. Instalar código e dependências sem incluir dados reais ou configuração secreta no repositório.
2. Adaptar [amorino.env.example](../ops/amorino.env.example); gerar segredo aleatório `secrets.token_hex(32)` e credencial forte de supervisor no gestor de palavras-passe da empresa. Guardar `/etc/amorino.env` como `root:amorino`, modo `0640`. Não imprimir o ficheiro, incluir credenciais em tickets ou guardar tokens em logs.
3. Executar `scripts/preflight.py` com o ambiente do serviço, sob o utilizador `amorino`, com `AMORINO_SEED=0`. O comando não importa Flask, não cria bases de dados e não executa migrações. Recusa segredos/defaults fracos, demo/debug, seeding de roster de código, caminho relativo ou dentro do checkout, base ausente/inválida, restauro/migração incompletos, proxy/public bind incorreto, múltiplos processos declarados e fuso/configuração inválidos. Produção precisa de base aprovada e migração controlada antes de ativar o serviço/preflight. Um marcador `*.migration-in-progress` deixado após falha bloqueia o arranque até revisão do operador e conclusão/rollback verificados; não o apagar para contornar uma falha.
4. Copiar [amorino.service](../ops/amorino.service) para `/etc/systemd/system/`; executar `systemctl daemon-reload`, `systemctl enable --now amorino`. Um único `python wsgi.py`; concorrência por threads. Não aumentar workers/réplicas. O preflight corre antes de cada arranque. `Restart=on-failure` reinicia falhas; `enable` arranca após reboot.
5. Adaptar [nginx.conf.example](../ops/nginx.conf.example) com domínio autorizado e certificados. Só o proxy atende portas públicas 80/443; 8000 continua em loopback. Instalar e renovar TLS pelo procedimento do fornecedor autorizado, validar `nginx -t`, teste de renovação e redirecionamento HTTP. Confirmar HTTPS real, cookies `Secure` e URL HTTPS do calendário; `AMORINO_HTTPS=1` não instala certificados.
6. Instalar timer/serviços de backup abaixo. Completar o destino fora do servidor e a ligação dos alertas antes de autorizar utilização por funcionários.
7. Testar restart/reboot, `/healthz`, logins reais autorizados, envio/revisão de disponibilidade, publicação/alteração/troca e exports. Arquivar a evidência com data e operador. Preflight local aprovado não substitui estes testes do servidor.

Comandos operacionais, depois de configuração aprovada:

```bash
sudo systemctl restart amorino
sudo systemctl status amorino --no-pager
sudo journalctl -u amorino -n 100 --no-pager
curl --fail --silent https://DOMINIO_PENDENTE/healthz
```

O exemplo envia logs de serviço para journald. Ativar persistência (`Storage=persistent` na configuração do journald), definir retenção aprovada e verificar leitura após reboot. Usar a rotação nginx do sistema e acesso restrito aos logs de erro. O acesso nginx omite URLs/query para não registar tokens dos calendários; erros nginx podem conter caminhos, exigindo proteção. Monitorizar externamente `/healthz` e falhas de restart; destinatário e teste de entrega do alerta: PENDENTE.

## Backups diários e cópia fora do servidor

`backup_db.py` usa a API SQLite online, inclui commits WAL e verifica integridade. Publica um nome único depois da validação, junto de manifesto com SHA-256, hora UTC inicial/final, tamanho e contagens. Falhas têm código de saída não zero e `backup-status.json` com `ok:false`; nenhuma credencial é registada. A poda elimina a cópia e o respetivo manifesto; `--keep 0` conserva todas. Não copiar diretamente a base em utilização.

```bash
AMORINO_DB_PATH=/var/lib/amorino/amorino.db /opt/amorino/venv/bin/python /opt/amorino/app/scripts/backup_db.py --db /var/lib/amorino/amorino.db --dir /var/lib/amorino/backups --keep 14
/opt/amorino/venv/bin/python /opt/amorino/app/scripts/verify_backup.py --dir /var/lib/amorino/backups --max-age-hours 26
```

Instalar `amorino-backup.service`, `amorino-backup.timer`, `amorino-backup-failure.service` em `/etc/systemd/system/`. Executar `systemctl daemon-reload` e `systemctl enable --now amorino-backup.timer`; confirmar `systemctl list-timers amorino-backup.timer` e executar `systemctl start amorino-backup.service` uma vez. Timer diário às **03:00 de Lisboa**, incluindo execução pendente após reinício.

O job cria a cópia local, verifica-a, executa `ops/offserver-copy.sh` e só termina com sucesso após verificação no destinatário. Um destino por preencher falha deliberadamente, embora a cópia local permaneça válida. A falha aciona `amorino-backup-failure.service`, escreve `/var/lib/amorino/backup-failure` e mensagem de erro no journal. Isto regista a falha; **não entrega aviso a uma pessoa sem monitor configurado**. O monitor deve verificar estado do serviço, marcador de falha, idade da cópia local e idade de `/var/lib/amorino/offserver-status` no máximo a cada hora; notificar o contacto acordado. Testar uma falha e a receção humana antes do piloto.

Para o destino aprovado:

- Empresa escolhe armazenamento independente do servidor web, localização UE, acesso, retenção e responsáveis. Ativar MFA na conta; encriptação em repouso no fornecedor/volume e transferência SSH. Não usar conta pessoal do programador.
- Criar conta SSH restrita para upload, chave dedicada sem interação e host key previamente verificada. Guardar chave fora do repositório com modo `0600`; não desativar a verificação do host. O recetor precisa de Python 3 e de `scripts/verify_backup.py`/`scripts/ops_common.py` em `/opt/amorino-recovery/scripts/`, com destino `/srv/amorino/backups`.
- Preencher `AMORINO_OFFSERVER_DEST` e `AMORINO_OFFSERVER_HOST` no ambiente. `offserver_plan.py` exige que ambos indiquem o mesmo host e `/srv/amorino/backups/`, valida o último snapshot local e fixa nome/checksum. `rsync` copia esse snapshot e manifesto sem apagar no recetor; SSH verifica **esse nome exato**, o SHA-256 esperado na origem, integridade/schema e idade **no recetor**. Uma cópia antiga recente não pode substituir esta prova. Adaptar os caminhos fixos/validador em conjunto se o destino autorizado diferir. Agendar retenção no recetor segundo decisão da empresa e verificar que elimina base e manifesto em conjunto.
- Descarregar uma cópia do recetor para um ambiente isolado e repetir restauro/validação. Guardar hora, tamanho, checksum, resultado do recetor e nome do operador; nunca publicar dados do pessoal nem tokens como evidência. Cópia simulada numa segunda pasta do portátil não é cópia fora do servidor.

No Windows, [windows-backup.ps1](../ops/windows-backup.ps1) executa backup, validação e, com `-ApprovedOffserverHost`, SCP+verificação remota no mesmo layout. Agendar diariamente às 03:00 no Agendador de Tarefas sob uma conta dedicada, com ação `powershell.exe -NoProfile -NonInteractive -File "CAMINHO\ops\windows-backup.ps1" -PythonExecutable "PYTHON" -CodeDirectory "CODIGO" -DatabasePath "DADOS\amorino.db" -BackupDirectory "COPIAS" -ApprovedOffserverHost "CONTA@HOST_PENDENTE"`. Ativar execução após início perdido e histórico; verificar o código de saída e `backup-failure`. Confirmar fuso do Windows. Configuração/credenciais do agendador e alerta humano são passos pendentes; não foram instalados neste trabalho.

## Restauro e validação

É uma operação offline autorizada. Parar todas as escritas antes de substituir ficheiros; os flags do script são declarações do operador, não deteção automática de serviços. O script valida checksum, integridade, referências e tabelas **antes** de mover qualquer ficheiro do destino.

1. Supervisor regista incidente, hora e versão da última escala publicada. Restringir acesso de funcionários no proxy; passar à contingência abaixo. Guardar logs relevantes sem credenciais.
2. Parar `amorino`, timer e job de backup; confirmar todos os processos que escrevem estão parados e impedir restart automático durante intervenção. Não importar `app.py` sobre a base danificada para tentar corrigi-la.
3. Escolher a última cópia íntegra, preferencialmente recuperada do recetor independente. Guardar base e manifesto juntos e executar `verify_backup.py --backup COPIA.db`. Registar hora inicial da cópia e intervalo potencialmente perdido.
4. Definir `AMORINO_DB_PATH` com o destino absoluto. Executar:

```bash
AMORINO_DB_PATH=/var/lib/amorino/amorino.db /opt/amorino/venv/bin/python /opt/amorino/app/scripts/restore_db.py --backup /CAMINHO/COPIA.db --target /var/lib/amorino/amorino.db --confirm-target /var/lib/amorino/amorino.db --service-stopped
```

5. O script cria snapshot validado em staging, revoga convites e tokens de calendário da cópia e aumenta as versões de acesso de sessão; preserva os PINs hashed presentes no snapshot. Regista `restore_revoke_access` quando o audit log existe. Preserva a base danificada **e `-wal`, `-shm`, `-journal`** numa pasta `damaged-UTC-identificador` ao lado do destino, com hashes de preservação, e substitui a base. Conserva a pasta para investigação. `*.restore-in-progress` bloqueia o preflight se houve interrupção: inspecionar o manifesto/pasta e completar recuperação sob controlo técnico antes de remover esse marcador.
6. Verificar permissões da base e diretório. Arrancar serviço ainda com acesso restrito. Validar `/healthz`; login supervisor e funcionário autorizado; roster esperado; disponibilidade; versão publicada e alterações; histórico de trocas e donos dos turnos; PDFs globais/individuais e CSV contra última versão recuperada. Confirmar totals de **horas planeadas**, turnos noturnos e crédito de descarga. Guardar amostras privadas e aceitação do supervisor.
7. **Gerar um novo `AMORINO_SECRET_KEY` antes de reabrir** para invalidar todos os cookies antigos, mesmo que versões de sessão tenham retrocedido/colidido no restauro. Verificar os resets efetuados após a cópia: o snapshot pode conter o PIN antigo. Repetir o reset dos afetados, confirmar identidade e entregar novos convites privados; se os afetados não puderem ser determinados, reset controlado de todas as contas potencialmente afetadas antes de reabrir. Convites anteriores nunca são recuperados; funcionários recriam as subscrições de calendário após login. Reconciliar escalas/trocas/disponibilidades posteriores à hora inicial da cópia com supervisores/funcionários e registos privados. As alterações perdidas não são recuperadas pelo restauro. Registar e publicar uma nova alteração autorizada; avisar os afetados no canal acordado.
8. Só reabrir acesso após aceitação; reativar timer/monitor e verificar primeira cópia fora do servidor. Registar recuperação, operador, duração e alterações perdidas/reconciliadas.

Para cópia antiga sem manifesto existe `--allow-without-manifest`: apenas após decisão documentada do responsável técnico; mantém verificação de integridade/schema, mas não pode provar origem/checksum esperado. `--allow-repository-target` permite dados isolados noutras pastas do checkout; o script recusa sempre o `amorino.db` vivo da raiz. O layout de produção separa código e dados.

Rollback de release/migração: parar serviço e jobs, conservar dados atuais e sidecars com o restauro guardado, voltar ao código anterior aprovado e restaurar a cópia validada **anterior** à mudança. Não executar código antigo sobre um schema novo e esperar reversão. Tratar mudanças desde essa cópia como possível perda; reconciliar antes de reabrir. A migração de publicação tem procedimento próprio em [SCHEDULE_PUBLICATION.md](SCHEDULE_PUBLICATION.md).

## Ensaio local repetível

```powershell
$env:AMORINO_DB_PATH = Join-Path $env:TEMP 'amorino-rehearsal-launch-scratch.db'
python scripts/recovery_rehearsal.py --report docs/recovery-rehearsal-result.json
```

Cria base nova, três contas fictícias, disponibilidades, duas lojas, turno noturno, descarga e troca aprovada após publicação. Faz cópia SQLite, transferência para segunda pasta local, valida checksum, simula dano e preserva base/sidecars. Novo processo valida login, roster, páginas, disponibilidades, turnos/donos/troca, PDF geral/individual e CSV idênticos ao snapshot. Uma alteração fictícia posterior à cópia é intencionalmente perdida. Tempos e resultado ficam no JSON. Não usa a base de produção nem valida servidor, HTTPS externo ou armazenamento remoto. Repetir trimestralmente e após alteração relevante de schema/infraestrutura.

O exemplo diário permite perder até cerca de um dia de alterações; no regresso da hora de verão podem decorrer 25 horas entre execuções, acrescidas da duração do job. Falhas aumentam o intervalo. Escolher frequência e objetivo de perda aceitável com a empresa; não apresentar o resultado de poucos segundos com dados fictícios como prazo garantido em produção. Resultado registado: [RECOVERY_REHEARSAL.md](RECOVERY_REHEARSAL.md).

## Suporte, contingência e passagem de manutenção

Pedido no canal privado único (PENDENTE): loja, semana, versão publicada, comportamento observado, hora e passos mínimos. Sigla só quando necessária. Nunca pedir PIN, convite, cookie, feed de calendário ou base completa num ticket. Suporte atribui número do incidente e responsável; supervisor confirma gravidade e prazo operacional.

| Situação | Primeira ação | Escalamento / fecho |
|---|---|---|
| Bloqueio de login individual | Supervisor verifica identidade e usa convite/reset seguro | Técnico se nova credencial válida falhar; confirmar acesso com a pessoa |
| Escala errada ou mudança urgente | Supervisor verifica versão e publica alteração explícita | Técnico se publicação/export inconsistente; confirmar versão com afetados |
| Serviço indisponível ou backup/recetor falhado | Técnico recebe alerta; supervisor ativa contingência quando impede planeamento | Escalar ao substituto e dono do alojamento; contacto e tempos PENDENTE |
| Suspeita de acesso indevido | Restringir acesso, preservar logs/cópias, contactar responsáveis | Responsável da empresa decide comunicações; técnico revoga acessos/segredos afetados |

Contingência: manter PDF privado da última **versão publicada**, identificado com semana/versão/hora de publicação. Supervisor distribui pelos canais já aprovados, recolhe disponibilidade temporária e regista alterações manuais com autor/hora/pessoas avisadas. A app e a versão publicada voltam a ser referência após reconciliação explícita. Calendários de terceiros atualizam segundo o cliente; usar a página/último PDF e comunicação direta para alterações urgentes. Não prometer atualização instantânea.

Handover ao substituto exige: repositório privado/source e release aprovada; versões Python/dependências; mapa de paths/serviços/proxy e arquitetura de um processo; contas empresa/MFA e acesso individual ao gestor de segredos; acesso ao recetor e retenção; timer/monitor/alertas testados; última cópia verificada e último ensaio; lista de incidentes/limitações; regras/aceitações do supervisor e export esperado. O substituto executa preflight, restart e ensaio isolado, identifica o backup e contacto de escalamento e assina aceitação com data **PENDENTE**. Revogar acesso de quem sai; não partilhar contas master.
