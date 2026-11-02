# Publicação, alterações e migração

Preparação implementada em 7 de Outubro de 2026. A base `amorino.db` da raiz não foi aberta nem alterada. A utilização real depende da revisão da supervisão e da preparação de produção.

## Operação semanal

1. Selecionar a semana para recolher disponibilidades. Esta ação mantém o comportamento existente e **não publica a escala**.
2. Preparar turnos e descargas na escala de supervisão. Gravar, mover, remover ou copiar atribuições altera apenas o rascunho. Confirmar conflitos, horas planeadas e cobertura diária. Cobertura diária não comprova presença simultânea; horas planeadas não comprovam assiduidade.
3. Abrir «Rever e publicar», conferir valores antes/depois e exportações de preparação. O PDF de preparação identifica «RASCUNHO (não comunicar à equipa)». O CSV de preparação tem `RASCUNHO` no nome e no cabeçalho HTTP de versão.
4. Preencher motivo/plano de comunicação, assinalar a confirmação e publicar. O servidor verifica a versão e o hash do rascunho revisto; alterações concorrentes exigem nova revisão. Uma publicação sem diferenças não cria versões artificiais.
5. Descarregar os PDFs/CSV oficiais **sem** `rascunho=1`: usam a versão publicada, identificada no PDF, nome do ficheiro e `X-Amorino-Schedule-Version`. O PDF geral/loja mostra turnos regulares; PDF individual e CSV incluem descargas. Confirmar ambos quando existirem entregas.
6. Comunicar pelo canal interno aprovado: «Semana [semana], versão [número], publicada em [data/hora]. Consultar a aplicação ou PDF desta versão». Guardar o PDF/CSV num local interno autorizado. A publicação regista a autorização; **não envia mensagens** nem comprova a receção.

Editar uma semana já publicada prepara uma alteração. A equipa continua a ver a versão anterior até à confirmação/publicação. Antes de um turno próximo, contactar privadamente cada interveniente e registar a receção no procedimento do supervisor; escalar quem não responder. Cada funcionário encontra a versão atual e o histórico em «Consultar versões e alterações». Corrigir uma publicação errada através de nova versão, com motivo; nunca apagar a evidência anterior.

A aprovação de uma troca é a confirmação da alteração dos turnos envolvidos e cria uma versão registada em cada semana afetada. Nas doações, o autor publica o pedido e os interessados candidatam-se; a administração escolhe uma candidatura pendente e aprova na mesma decisão. O autor não escolhe o destinatário. Só a transferência do turno é publicada; outras mudanças do rascunho permanecem pendentes. Uma troca com o turno pedido alterado no rascunho exige revisão; sobreposições são verificadas contra ambas as escalas. Uma troca não aprovada não altera a escala autorizada. Procedimento: [DONATIONS.md](DONATIONS.md).

O feed e o ficheiro .ics usam a versão autorizada, identificadores estáveis e `SEQUENCE`. O download local não depende de um endereço público; uma subscrição por serviços externos requer um endereço acessível por esses serviços, com HTTPS em produção. A aplicação de calendário externa decide quando consulta o feed e pode manter dados antigos. Para alterações urgentes, usar a aplicação/PDF e confirmação individual; não prometer atualização instantânea. Os feeds atuais incluem turnos regulares; confirmar descargas na escala individual/PDF/CSV.

## Modelo e preservação

`turnos`, `descargas` e `semanas` mantêm a preparação. `published_turnos`, `published_descargas` e `published_semanas` guardam a projeção autorizada. `schedule_releases` guarda snapshots imutáveis com versão, instante UTC, responsável, motivo e diferenças; `schedule_changes` regista adições, remoções, movimentos e configuração com antes/depois na transação da gravação. `schedule_identities` evita reutilizar identificadores antigos. Não há edição de versões antigas pela aplicação.

Os leitores de funcionários, URLs de calendário, exportações oficiais e as mutações de supervisão partilham um bloqueio durante o pedido para não misturar versões. A troca da projeção, versão e histórico é uma única transação SQLite. **Um processo com threads** é requisito funcional; não executar réplicas, vários workers ou processos paralelos de importação durante a utilização.

Migração aditiva no primeiro arranque desta versão, com marcador `schedule_publication_v1`:

| Semana existente | Tratamento |
|---|---|
| Histórica (primeira data até ao dia da migração, incluindo semanas terminadas) | Preservar como versão 1, responsável `MIGRATION`, com data real da migração. A data/responsável original de publicação são desconhecidos, nunca inventados. |
| Atual (já começou) | Preservar exatamente a escala existente como versão 1 para não retirar aos funcionários a escala em que já trabalham. Supervisor deve rever e comunicar esta base. |
| Futura (ainda não começou) | Preservar turnos e descargas no rascunho, sem os tornar visíveis. Rever e publicar explicitamente as semanas já comunicadas antes da migração. |
| Datas ausentes/inválidas | Manter sem publicação automática e submeter à revisão do operador. |

A semana ativa, disponibilidades e PINs existentes mantêm-se. Contas sem PIN ficam bloqueadas até convite verificado; novos campos de acesso têm defaults seguros. Não inferir uma publicação a partir de `ativa`. Após o marcador, arranques seguintes não voltam a publicar rascunhos. Dados introduzidos posteriormente por importação exigem publicação/revisão explícita, incluindo históricos.

## Migração controlada e rollback

1. Confirmar a janela de manutenção e responsáveis; guardar o último PDF publicado e impedir novas escritas. Parar o serviço e todos os escritores/importadores. Preservar o código anterior **com o trabalho local existente** numa cópia restrita fora da BD; não usar apenas `git checkout`, porque há alterações não commitadas. Registar versões das dependências/configuração, sem copiar segredos para o repositório.
2. A BD de produção deve residir em disco local fora do checkout, por exemplo `/var/lib/amorino/amorino.db`. A BD de raiz não é alvo dos comandos de ensaio ou migração. A empresa autoriza a preparação do caminho externo a partir de uma cópia online consistente (`scripts/backup_db.py`), conservando a origem intacta e o manifesto verificado. Definir `AMORINO_DB_PATH` explicitamente para o novo caminho. Verificar também a cópia externa aprovada.
3. Executar `scripts/migrate_launch.py --db [caminho-absoluto] --backup-dir [pasta-restrita]` para ensaiar sobre uma cópia. Confirmar contagens, integridade, PINs, disponibilidade, semana ativa e tratamento de semanas. O original permanece sem alterações. Não abrir este ensaio ao público.
4. Depois da revisão concreta da empresa, aplicar com os mesmos caminhos e `--apply --service-stopped --confirm-target [caminho-absoluto]`. O comando cria backup antes de importar/migrar e exige `AMORINO_DB_PATH` igual ao alvo. Usar `AMORINO_SEED=0`. Executar preflight; arrancar serviço em acesso restrito; conferir login supervisor/funcionário, escala publicada, trocas e PDF/CSV; publicar futuras semanas já autorizadas antes de abrir aos participantes.
5. Para rollback, manter serviço parado, preservar a base pós-migração e sidecars, usar `restore_db.py` conforme [OPERATIONS.md](OPERATIONS.md) para repor o backup pré-migração e repor a cópia preservada do código/dependências anterior. A migração é aditiva, mas o rollback recomendado repõe a cópia completa; não eliminar tabelas/colunas na BD em utilização. Validar de novo com acesso restrito. Mudar a chave de sessão, revogar feeds/convites recuperados e reconciliar resets desde a cópia antes de reabrir.

O rollback elimina alterações feitas depois do backup; registar essa janela e reconciliar disponibilidades, novas versões e decisões de trocas. O código anterior volta a ter acesso livre por sigla para PINs vazios: só pode ser reaberto após provisionamento privado verificado ou sob restrição de rede. O rollback não transforma a versão anterior numa configuração segura de piloto.

## Evidência local histórica

Os resultados abaixo pertencem ao ensaio de 7 de outubro de 2026. Não comprovam
a prontidão da versão atual; executar os controlos relevantes sobre uma base
descartável e guardar nova evidência antes de uma apresentação ou instalação.

O relatório do ensaio de migração regista 5,501 segundos para migração e segundo arranque: dois funcionários fictícios, três semanas, três turnos, três descargas e 42 respostas preservados; histórica/atual publicadas como v1 `MIGRATION`, futura em rascunho, PIN existente verificado e conta vazia bloqueada. As projeções autorizadas coincidem com os snapshots. A semana ativa de disponibilidade permaneceu futura. O original fictício não foi modificado. Para repetir: `scripts/migration_rehearsal.py --work-dir [pasta-absoluta-vazia] --report [relatório-absoluto.json]`, com `AMORINO_DB_PATH` explicitamente configurado para uma base descartável. `tests/test_launch_migration.py` cobre ensaio, aplicação offline, confirmação e recusa da base viva.

`tests/test_publication.py` verifica rascunhos ausentes de páginas, PDFs, URLs e feeds; publicação/CSRF/autorização; hash desatualizado; versões; antes/depois; descargas; trocas publicadas sem outras alterações pendentes; conflitos autorizados; migração histórica/atual/futura e não reutilização de IDs. `tests/test_publication_dates.py` verifica datas e expiração de trocas. O ensaio executável de migração usa dados fictícios e produz o respetivo relatório; a [recuperação](RECOVERY_REHEARSAL.md) valida restauração e exportações. São evidências locais, sem dados de produção, assinaturas ou resultados medidos do piloto.
