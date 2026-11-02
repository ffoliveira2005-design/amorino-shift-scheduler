# Doações de turnos

Uma doação usa `TrocaPedido(tipo='aberto_doacao')` e `TrocaCandidatura`. O
destinatário `para_sigla` permanece vazio até à aprovação da administração.

| Ação | Estado resultante |
|---|---|
| Publicar sem escolher colega | `aberto` |
| Primeira candidatura | `pendente_supervisor` |
| Candidaturas adicionais | Mantém `pendente_supervisor` |
| Retirada da última candidatura ativa | `aberto` |
| Administração escolhe e aprova candidatura | `aprovado` |
| Administração recusa toda a doação | `negado` |
| Autor cancela antes da aprovação | `cancelado` |
| Turno começa, é removido ou a identidade muda | `expirado` |

`aberto` e `pendente_supervisor` permanecem na Central. A candidatura exprime
disponibilidade para receber o turno; só a administração transfere a atribuição.
Na aprovação, a candidatura escolhida fica `aceite` e as outras ativas ficam
`rejeitada`. Cancelamento, recusa e expiração fecham as candidaturas ativas como
`cancelada`, `negada` e `expirada`. Retiradas anteriores conservam-se.

Cada candidatura é persistida e entra na fila administrativa no mesmo pedido,
sem escolha ou confirmação intermédia pelo autor. Abrir ou atualizar «Trocas»
na sessão da administração mostra as candidaturas atuais; não existe promessa
de atualização automática de uma página já aberta. O autor pode acompanhar e
cancelar a doação antes da aprovação, mas não dispõe de «Escolher» para doações.
Na apresentação, usar duas sessões de candidatos e atualizar a fila após cada
candidatura, escolhendo a segunda para demonstrar que a ordem de chegada não
determina o destinatário.

`POST /api/trocas/<id>/decidir` exige `aprovar: true` e `candidatura_id` na aprovação
de uma doação. O servidor verifica pertença e estado da candidatura, existência e
elegibilidade da pessoa, identidade publicada e atual do turno e sobreposições de
turnos/descargas nas duas versões. Avisos de indisponibilidade, excesso de horas e
descanso semanal mantêm-se consultivos. `/escolher` devolve 403 para doações. Uma
chamada antiga a `/api/trocas` sem turno em troca publica uma doação na Central,
ignorando o colega nomeado; não cria consentimento nem seleciona destinatário.
Trocas efetivas mantêm o processo de aceitação/escolha anterior.

Todas as mutações de trocas reservam a escrita com `BEGIN IMMEDIATE` antes das
leituras de validação. A seleção, decisão, atribuição, candidaturas, versão publicada
e auditoria são confirmadas numa transação. A publicação parte da última versão
autorizada, alterando apenas o funcionário do turno doado. Outras alterações no
rascunho permanecem por publicar. A auditoria regista atores, candidatura, estados,
hora e valores anteriores/posteriores; erros de armazenamento revertem tudo.
Uma aprovação repetida encontra o pedido fechado e não volta a transferir o
turno. Pedidos inválidos não devem alterar atribuições, resultados ou versões;
verificar estas propriedades nos testes e no ensaio atual antes de apresentar.

A expiração é verificada ao consultar a Central, as trocas, a fila administrativa
ou o contador, e ao executar ações. `Europe/Lisbon`, suportado pela dependência
`tzdata` também em Windows, determina o início do turno independentemente do fuso
do servidor. Não é necessário um agendador para impedir aprovações tardias.

## Migração

O arranque existente acrescenta `trocas.origem_snapshot_json` se necessário e
executa `helpers.donations.migration_donation_review()`, sob reserva de escrita.
O marcador `schema_migrations.donation_admin_review_v1` torna a migração de dados
idempotente; a criação da coluna também é idempotente.

Só doações em curso são adaptadas. Nomeações antigas são removidas. Candidaturas
escolhidas ou preteridas num pedido pendente voltam a `pendente`; uma aceitação
antiga sem linha de candidatura é preservada como candidatura pendente. Nomeações
sem resposta não originam candidaturas. Retiradas e vetos existentes conservam-se.
Nenhuma transferência é aprovada pela migração. Pedidos concluídos, decisões e
destinatários históricos não são alterados. Um turno legado não publicado ou já
inválido expira na próxima validação.

Atualizar dependências e ensaiar o arranque numa cópia descartável com
`AMORINO_DB_PATH` explícito antes da instalação. Seguir o procedimento de backup
e migração já documentado em [SCHEDULE_PUBLICATION.md](SCHEDULE_PUBLICATION.md).
O desenvolvimento e os testes não executaram esta migração na base real.

Regressões: `tests/test_donation_review.py`, `tests/test_trocas_workflow.py`,
`tests/test_publication.py`, `tests/test_basics.py`, `tests/test_week_navigation.py`
e os controlos JavaScript em `tests/js/schedule_week.cjs`.
