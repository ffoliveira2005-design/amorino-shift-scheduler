# Amorino Escalas — Changelog

---

## Revisão local para apresentação à direção — 8 de outubro de 2026

- Lançador fictício prepara doadora e dois interessados, desativa o elenco do
  código-fonte, conserva os marcadores de migração e verifica 22 rotas/acessos
  mais 11 passos de doação pela aplicação real antes de restaurar o início.
- Decisões da API exigem objeto JSON, aprovação/recusa booleana explícita, nota
  textual e candidatura inteira positiva. Pedidos inválidos não recusam,
  transferem ou publicam parcialmente; regressão e postura de segurança atualizadas.
- CSV identifica semana e versão publicada/rascunho dentro do ficheiro, com
  as colunas numéricas existentes preservadas. PDF deixa de afirmar pagamento
  de feriados e corrige acentos; gestão identifica indicadores de preparação
  e calendário distingue subscrição de cópia local.
- Página de publicação com contraste de links adequado aos dois temas e
  espaçamento entre instruções, confirmação e histórico; revalidada no navegador.
- Documentação alinhada com publicação explícita, escolha administrativa da
  doação, horas planeadas, cobertura diária e requisitos da subscrição externa.
  Ensaio de computador, reset e exportações documentados em
  CEO_READINESS_2026-10-08.md; suite final com
  501 aprovados e zero saltados/falhas. Revisão móvel completa continua pendente.

## Navegação de semanas e revisão administrativa de doações — 7 de Outubro de 2026

- «Editar escala» e a escala partilhada usam setas com intervalo de datas e ano.
  Semanas vizinhas são calculadas pelas datas da lista autorizada, incluindo
  mudanças de ano. Ligações conservam `semana`, filtros e ações da semana exibida;
  navegar não ativa, cria ou publica. Controlos acessíveis por teclado, com foco
  visível, temas claro/escuro e disposição móvel. Sem vizinho, a seta fica desativada.
- Doações publicadas na Central passam diretamente à administração quando chega
  a primeira candidatura. Continuam disponíveis para outras candidaturas. O turno
  pertence ao autor até «Escolher e aprovar»; o autor pode cancelar e os candidatos
  retirar-se. A fila e os contadores mostram os pedidos pendentes, com horários de
  candidatura, conflitos impeditivos e avisos consultivos. Decisões chegam ao
  histórico e aos indicadores de novidades.
- Aprovação, cancelamento e retirada são protegidos por transações SQLite. Só o
  destinatário aprovado recebe o turno e a publicação contém apenas a transferência
  autorizada. Doações expiram no início do turno em Lisboa ou por alteração da sua
  identidade. A API bloqueia escolha pelo autor e caminhos de doação direta.
- Migração idempotente dos pedidos antigos conserva o histórico concluído,
  transforma escolhas anteriores em candidaturas pendentes e exige nova decisão
  administrativa. Procedimento e estados: [DONATIONS.md](DONATIONS.md).

## Preparação do piloto controlado — 7 de Outubro de 2026

- Escalas com rascunho e publicação explícita. A seleção da semana de disponibilidade mantém-se independente. Funcionários, PDFs individuais e calendários consultam a versão autorizada; alterações guardadas ficam pendentes até confirmação da supervisão.
- Versões e alterações publicadas identificam data, responsável, motivo e valores antes/depois. Trocas aprovadas geram uma alteração publicada dos turnos envolvidos. A supervisão dispõe de revisão de alterações e exportação explícita de rascunho; exportações oficiais usam a publicação por omissão e identificam a versão.
- Acesso inicial e recuperação por convite privado de uso único, com validade de 24 horas. Saber a sigla não permite reclamar uma conta. Recuperar acesso revoga imediatamente o PIN, as sessões e o calendário anteriores; credenciais existentes continuam válidas.
- Preflight de produção, configuração de serviço/HTTPS, backups com manifesto e estado, verificação de cópias externas e restauro que preserva a base danificada. Ensaios locais usam apenas dados fictícios; instalação em produção e cópias externas reais continuam por executar.
- Procedimentos: [publicação/migração](SCHEDULE_PUBLICATION.md), [acessos](EMPLOYEE_ACCESS.md), [operação e suporte](OPERATIONS.md) e [ensaio de recuperação](RECOVERY_REHEARSAL.md).
- Carta do piloto, scorecard semanal e aceitação para assinatura: preparação, dois ciclos de referência e quatro ciclos medidos. Proprietários, datas, aprovações e resultados da empresa permanecem pendentes.

## Preparação para apresentação à direção — 6 de Outubro de 2026

- Mini equipa ao lado da escala do supervisor: pesquisa por nome/sigla,
  filtros de contrato, disponibilidade por dia e horas planeadas da semana.
  Arrastar uma pessoa para a célula atribui o respetivo turno com validação e
  opção de anular. Selecionar uma pessoa e usar o + permite trabalhar por
  toque ou teclado; o painel pode ser recolhido para ampliar a grelha.
- Ação «Apresentar» na visão geral abre três capítulos com os mesmos dados:
  semana, cobertura e decisões. Navegação por botões ou setas, saída por Escape,
  restauração após atualizar a página e impressão do resumo completo.
- Descargas mantêm atribuições e horários visíveis mesmo sem indicações de
  disponibilidade. Controlos identificam a loja/dia, bloqueiam envios repetidos
  e conflitos entre gravações e preservam o estado apresentado numa rejeição.
  Retirar uma pessoa não a volta a apresentar como disponível se retirou a indicação.
- Criar/copiar semanas recupera os controlos após falhas e mantém o destino
  escolhido. Configuração de descargas mostra alterações pendentes e exige
  «Guardar configuração», com confirmação ao retirar atribuições existentes.
- Calendário individual tem ação de fechar, tentativa após falha e descarga de
  uma cópia .ics para demonstrações locais. Respostas antigas não alteram um
  diálogo reaberto; falhas de cópia não apresentam uma confirmação de sucesso.
- Pesquisa na equipa e escala aceita nomes sem acentos, mostra resultados e
  permite limpar filtros. Gravação da equipa recupera de falhas de rede e
  mantém alterações que precisam de nova tentativa; impede envios repetidos.
- Navegação mantém a semana selecionada. Diálogos suportam foco por teclado
  e devolvem o foco ao fechar; Enter no botão Cancelar cancela a operação.
  Proteção CSRF no navegador usa a origem real do URL, preserva cabeçalhos de
  pedidos e não envia o token para domínios externos semelhantes.
- Arrastar um turno e anular a deslocação usam uma única alteração validada,
  mantendo o turno original quando há conflito ou falha de ligação. A nota e
  o identificador são preservados; o fim do arrasto não perde a pessoa escolhida.
- Início com cartão do próximo turno mais simples, controlos de calendário
  acessíveis e horas apresentadas como planeadas, com vírgula decimal. Horas
  em dias anteriores não são apresentadas como presenças confirmadas.
  Feriados ficam identificados sem afirmar uma regra de pagamento; o cálculo
  existente de horas planeadas mantém-se.
- Ligação de um turno ao Google Calendar usa o fuso Europe/Lisbon mesmo quando
  o dispositivo está noutro país; lembrete de uma hora é indicado apenas para
  o ficheiro .ics, com confirmação do lembrete no Google Calendar.
- Roteiro de valor identifica critérios para acessos, publicação da escala,
  recuperação do serviço e validação comercial a 30, 60 e 90 dias.
- Visão geral com tipografia mais legível, estado da recolha de disponibilidades,
  ações nos indicadores e lista de lojas/dias com lugares por preencher.
  Navegação dá acesso imediato à visão geral; cartão de entrada centrado sem
  texto lateral, com ação textual para mostrar ou ocultar o PIN.
- Páginas de disponibilidade, escala individual e conflitos com hierarquia
  mais clara, controlos móveis maiores e indicação de preparação da próxima
  semana. Preferências opcionais ficam recolhidas quando não estão preenchidas;
  trocar o estado de um dia preserva a nota escrita. Diálogo de trocas permite
  selecionar o tipo por teclado e usa «semanas disponíveis».
- Limpar um dia numa submissão parcial retira a resposta anterior; um formulário
  de uma semana que deixou de estar disponível é recusado, sem gravar noutra
  semana. Sobreposições entre lojas são recusadas mesmo com «troca» na nota.
- Criação e importação de semanas preservam o histórico quando o código curto
  se repete noutro ano. Data inicial tem de ser segunda-feira. Descargas e turnos
  aparecem em ordem cronológica na escala individual.
- Avaliação de mercado em Portugal com referências oficiais de preços,
  comparação do custo a três anos e metas de retorno para validar no piloto.
  Oferta ao cliente em português europeu, com alojamento e direitos de utilização
  explícitos; preços propostos não representam uma avaliação de empresa.
- Nova «Visão geral» de supervisão com seleção de semana, cobertura por loja e
  dia, horas planeadas incluindo descargas, respostas de disponibilidade e
  trocas da semana à espera de decisão. A cobertura limita cada dia ao objetivo
  para que excedentes numa loja não ocultem faltas noutra; objetivos em falta
  ficam explícitos. Resumo imprimível e ligações para agir ou exportar.
- Página de entrada mantém o cartão centrado original. A introdução e o
  texto de apresentação adicionados nesta preparação foram retirados.
- Ambiente local de demonstração com 46 pessoas fictícias, semanas atuais,
  cenários de cobertura e troca, credenciais próprias e aviso visível. Não
  consulta nem altera a base de dados de produção.
- Atribuições de descarga rejeitam sobreposições entre lojas; a edição dos
  horários valida intervalos e conflitos. Adicionar ou mover um turno exige
  um dia válido na semana escolhida.
- Remover um colaborador limpa descargas e encerra pedidos e candidaturas
  em curso, preservando o histórico de pedidos concluídos.
- Retirar uma loja/dia da configuração de descargas remove as atribuições
  correspondentes, sem deixar horas ocultas no total da semana.
- PDF individual inclui descargas e as horas creditadas, com ordem cronológica,
  totais consistentes e nomes/observações tratados como texto.
- Proposta comercial e roteiro de apresentação com piloto, âmbito, preços,
  limites de manutenção e cenários de retorno assentes em hipóteses explícitas.

## Páginas mais leves e responsabilidade por loja — 3 de Outubro de 2026

- Equipa passa a usar linhas compactas, com nome, função e balanço de horas
  fáceis de comparar. Pesquisa, filtros, ordenação e edição mantêm-se disponíveis.
- Na edição da equipa, cada responsável de loja pode ter uma «Loja gerida».
  A função e a loja são guardadas em conjunto; retirar a função de responsável
  de loja limpa a atribuição. Esta responsabilidade não altera o acesso à app.
- As sugestões de responsáveis de turno ficam limitadas às lojas habituais
  identificadas no histórico anterior. O sinal conta dias distintos por loja,
  dá mais peso às semanas recentes e aceita empates. Uma semana anterior basta
  para identificar a loja; sem histórico, a atribuição continua disponível manualmente.
- Os responsáveis de loja recebem prioridade na loja que gerem, respeitando
  as disponibilidades, os conflitos de horário e os avisos de horas e descanso.
  As sugestões explicam «Loja habitual» ou «Gere esta loja» quando se aplica.
- O painel de Escala usa toda a altura disponível para a lista de lojas e
  sugestões. A barra de horas mantém-se acessível, com abertura por teclado;
  já não deixa uma zona vazia por limitar a lista a 60% da altura.
- O painel de supervisão apresenta as ações pendentes numa lista simples.
  Exportações, análise de respostas e configuração de descargas usam secções
  expansíveis; as ações de atribuição e os avisos continuam visíveis.
- A revisão de disponibilidades agrupa os dias por pessoa, com pesquisa e
  filtros de dias por rever, observações e descarga. Marcar uma indicação como
  vista preserva a semana, os filtros e as pessoas abertas. Texto revisto fica legível.

## Trocas — decisões mais claras — 3 de Outubro de 2026

- Pedidos por decidir aparecem primeiro, com ações e conflitos sempre
  visíveis. Cartões mais leves, horários comparáveis e doações com um só
  turno em destaque tornam a página mais fácil de percorrer.
- Mensagens e histórico usam secções expansíveis. Novidades concluídas
  abrem o histórico automaticamente para não esconder uma resposta recente.
  Candidaturas pendentes e a escolha de quem publica permanecem à vista.
- Formulários preservam a seleção após uma falha de rede e impedem envios
  repetidos. Respostas antigas já não alteram uma seleção mais recente nem
  interrompem um diálogo reaberto. A confirmação de aprovação distingue
  uma troca de uma doação.
- Fluxos verificados na cópia local: pedido direto, candidatura, resposta
  do colega e aprovação, incluindo os dois temas e o telemóvel.

## Escala — sugestões por turno concreto — 3 de Outubro de 2026

- As sugestões passam a avaliar o horário que vai ser atribuído: apenas
  intervalos válidos e disponíveis, sem sobreposição com descargas. Uma
  descarga que termina às 14:00 permite agora sugerir um turno às 14:00.
- Preferências e disponibilidades mantêm prioridade sobre o histórico.
  O equilíbrio de horas considera a duração e os extras do turno proposto,
  incluindo as descargas já atribuídas. Os dias são contados sem duplicar
  uma descarga e um turno no mesmo dia.
- Cobertura e responsabilidade de abertura/fecho são avaliadas no intervalo
  real. O descanso considera também turnos nas semanas adjacentes e fechos
  depois da meia-noite. As escolhas adicionais são alternativas para os
  lugares em falta; não criam lugares fictícios no cálculo da cobertura.
- Dias de trabalho sem intervalo horário, como Provas Práticas, também
  contam para os dias consecutivos, incluindo a passagem de uma semana
  para outra. O descanso entre turnos mantém o cálculo por horas reais.
- Cada sugestão mostra o intervalo completo, horas atuais e projetadas,
  dias de trabalho, motivo e avisos. A disponibilidade por confirmar
  permanece explícita mesmo quando o histórico ou a responsabilidade pesam.
- O histórico exclui semanas futuras e rascunhos vazios, exige datas válidas
  e não multiplica sinais por linhas duplicadas. Os pares de colaboradores
  só contam quando trabalharam juntos na mesma loja com horas sobrepostas.
- Leitura de PDFs corrigida para títulos de loja em várias linhas,
  intervalos com «/» e horários específicos escritos nas células.
- Importação verificada das semanas de 28 de Setembro–4 de Outubro e
  5–11 de Outubro: 289 turnos, 9 descargas de quatro horas e 13 novos
  colaboradores. As 9 atualizações de contrato confirmadas foram aplicadas
  no mesmo lote, com cópia de segurança e uma só transação. Os 795 turnos
  anteriores foram preservados. A semana atual fica ativa.

Validação final das sugestões, importação e Trocas: 254 testes aprovados
numa base de dados de teste; formulários e decisões verificados no browser,
nos dois temas e em larguras de 320 a 1280 px. Foram também verificadas
13 situações controladas de respostas assíncronas e nova tentativa.

## Frontend — navegação e legibilidade — 3 de Outubro de 2026

- Navegação em duas linhas também nos tablets, antes de os links colidirem
  com os controlos da sessão. Alvos de toque maiores e contraste reforçado
  nos links ativos e nos botões âmbar, nos dois temas.
  Os cabeçalhos das lojas e o painel de atribuição acompanham a altura real
  da navegação, incluindo mudanças de largura e zoom.
- Página inicial com largura de leitura mais confortável, acessos rápidos
  mais compactos e menos decoração. No telemóvel, a faixa semanal permite
  consultar os horários e os nomes das lojas sem reduzir o texto; abre no
  dia de hoje e pode ser percorrida horizontalmente.
- Disponibilidade com acesso direto aos sete dias e resumo atualizado das
  seleções. Os saltos colocam o dia abaixo da navegação fixa e transferem
  o foco; as notas de cada dia passam a ter nomes acessíveis.
  A confirmação de envio deixa de executar o script de um formulário que
  já não existe nessa página.
- Ligação para saltar a navegação, indicação acessível da página atual e
  estado do tema sincronizado com a preferência guardada.
- Separadores de loja com navegação pelas setas, Home e End. O filtro dos
  próprios turnos e as opções de lembrete do calendário indicam o estado
  selecionado às tecnologias de apoio.

Alterações limitadas a templates e estilos, preservando as rotas, permissões,
submissões, trocas e regras de atribuição existentes. Verificação visual e
funcional com dados de demonstração numa cópia local da base de dados.
Validação final: 202 testes aprovados; navegação, formulários, calendário e
filtros verificados no browser, nos dois temas e em larguras de 320 a 1280 px.

---

## Trocas — revisão do fluxo — Setembro 2026

O fluxo de trocas percorrido de ponta a ponta com três funcionários de teste e
a supervisão, no telemóvel e no desktop, numa cópia da base de dados. Tudo o
que está abaixo foi reproduzido antes de ser corrigido e verificado depois.

### O que estava errado

| Descrição | Onde |
|-----------|------|
| **A recusa da supervisão publicava pedidos diretos.** Um pedido feito a um colega, quando recusado, era convertido num pedido **público** da central — com o motivo ("consulta médica") visível para toda a equipa — sem o autor o ter decidido e sem ser avisado. O colega que tinha aceitado via o pedido desaparecer. Agora um pedido direto recusado **fecha**, fica no histórico dos dois com a nota da supervisão, e o autor tem "Pedir a outra pessoa" no próprio cartão. Um pedido da central continua a voltar à central para o autor escolher outra candidatura. | `app.py` |
| **Quem aceitava deixava de ver o pedido** até à decisão da supervisão (já não era "por decidir" nem "enviado por mim"). O mesmo com o candidato escolhido na central. E quem não era escolhido, ou era recusado pela supervisão, nunca sabia. Agora "Os meus pedidos" inclui o que aceitei ("A aguardar a supervisão") e o histórico mostra as candidaturas que terminaram sem mim. | `app.py`, `trocas.html` |
| **Pedidos que nunca podiam ser aprovados** — uma doação a quem já trabalha nesse horário, uma troca que deixava alguém com dois turnos sobrepostos — passavam por duas pessoas e só falhavam na supervisão. A mesma verificação passa a correr em todos os passos (pedido, resposta, candidatura, escolha, aprovação), incluindo as janelas de descarga. | `app.py` |
| **O mesmo turno podia estar em vários pedidos** — a dois colegas ao mesmo tempo, ou a um colega e na central. Os dois aceitavam; a segunda aprovação falhava com "o turno mudou de dono". Um turno só pode agora estar num pedido aberto de cada vez. | `app.py` |
| **Turnos já passados** podiam ser pedidos a um colega (a central já recusava), e os seletores ofereciam os dias já passados da semana corrente. Pedidos por responder também **nunca expiravam**: ficavam para sempre em "Por decidir". Agora expiram quando o dia passa, como os da central. | `app.py`, `minha_escala.html` |
| **A página Trocas da supervisão não mostrava os conflitos** — só o painel mostrava. O supervisor carregava em "Aprovar e aplicar" e recebia um erro num balão estreito que desaparecia em 3 segundos ("Podes negar o pedido…"). Agora os problemas aparecem no cartão antes do clique, nas duas páginas. | `trocas.html`, `app.py` |
| **"Negar" dizia "O pedido fica fechado"** quando o pedido era, na verdade, republicado na central. A ação passa a chamar-se "Recusar" em todo o lado e o diálogo diz o que acontece: um pedido direto fica recusado; um da central volta à central. | `trocas.html`, `supervisor_dashboard.html` |
| **As horas relativas estavam uma hora atrasadas** no verão: os tempos iam para o browser sem fuso ("há 1h" para um pedido de agora). O painel de disponibilidades mostrava também a hora de submissão em UTC. | `app.py`, `time_helpers.py`, `supervisor_disponibilidades.html` |
| O pedido próprio publicado na central aparecia duas vezes; depois de uma recusa, a resposta do colega aparecia como ": Combinado"; quem a supervisão tinha recusado continuava a ver "Candidatar-me" (e recebia "A supervisão recusou-te"); "Candidaturas pendentes" listava pessoas recusadas. | `trocas.html` |
| O `/api/central` devolvia a qualquer funcionário quem se candidatou a cada pedido e o comentário que escreveu. Só o autor e a supervisão os recebem agora. Documentado no `SECURITY.md`. | `app.py`, `SECURITY.md` |
| A página tinha `page-md`, uma classe que nunca existiu: no desktop os cartões ocupavam 1400px de largura. | `app.css` |

### O fluxo novo

| Descrição | Onde |
|-----------|------|
| **Um só "Novo pedido"**, em vez de dois diálogos ("Pedir troca" e "Publicar na central") que pediam o mesmo por ordem diferente: o turno, a quem (um/a colega ou toda a equipa), o que vem em troca, motivo. Abre a partir de cada turno na Minha Escala (o turno já escolhido) ou do botão "Novo pedido" na página Trocas (que começa por escolher o turno — antes, o botão da central só levava à Minha Escala). | `_troca_pedido.html`, `app.css` |
| **Escolher colega**: pesquisa por nome ou sigla em vez de uma grelha de 46 botões; quem já trabalha nesse horário aparece marcado ("Ocupado/a: 10:00-18:30 · Carmelitas") e no fim da lista. Escolhido o colega, a lista fecha e o passo seguinte fica à vista. As opções que não podem ser escolhidas dizem porquê (turno sobreposto, turno já noutro pedido). O botão de enviar fica sempre visível no fundo do diálogo. | `_troca_pedido.html`, `app.py` (`/api/trocas/colegas`) |
| **Página Trocas por ordem de ação**: "Por decidir" (só quando há), "Os meus pedidos", Central, Histórico. Secções vazias ocupam uma linha em vez de uma caixa grande. Os cartões são escritos do lado de quem lê ("Para Bruno", "De Ana", "Resposta pendente", "À espera de Bruno"), com datas reais ("Qui 1 out" em vez de "Qui 1 · 28-4out") e, no histórico, a data em vez de "há 113d". | `trocas.html`, `app.py` |
| **Minha Escala**: um botão "Trocar ou doar" por turno; nada nos turnos já passados; "Pedido de troca em curso" (com link) quando o turno já está num pedido. | `minha_escala.html` |
| **Navegação no telemóvel**: "Trocas" passa para antes de "Equipa" — a 375px só cabem quatro links e o contador de pedidos pendentes ficava fora do ecrã. O link da página atual e a aba ativa do painel do supervisor passam a ser trazidos para a vista. | `base.html`, `app.css`, `supervisor_dashboard.html` |
| Mensagens longas (um conflito que nomeia quem ficaria com dois turnos) aparecem num bloco legível, com as quebras de linha, e ficam visíveis o tempo necessário para as ler. | `base.html`, `app.css` |
| Texto: "Este pedido não é para ti", "A supervisão recusou-te", "Tu ficas sem", "Procuras quem…", "Podes negar…", "Pedido já está Expirou" → formas impessoais e corretas. | `app.py`, `minha_escala.html` |

### Também encontrado nesta passagem

| Descrição | Onde |
|-----------|------|
| **Modais e painéis abriam fora do ecrã em todas as páginas longas.** A animação de entrada da página (`pageIn`, com `fill-mode: both`) deixava um `transform` aplicado ao `<main>` — o `transform: none` final, interpolado, vira uma matriz identidade — e isso torna o `<main>` o bloco de referência de todos os elementos `position: fixed` dentro dele. Na Equipa, o "Novo colaborador" abria abaixo do fundo do ecrã; no editor da escala, o painel "Atribuir turnos" e o popover de adicionar turno saíam do ecrã assim que a página era percorrida (clicar num "+" de uma loja abaixo da primeira parecia não fazer nada). A animação passa a `backwards`. | `app.css` |
| **Sessão expirada do supervisor sem caminho de volta.** O `@supervisor_required` respondia 403 "Não autenticado", que o `base.html` não trata como sessão expirada, e os pedidos JSON fora de `/api/` (criar/ativar semana, descargas) recebiam a página de login. Agora 401 `sessao_expirada` sem sessão, 403 com sessão de funcionário. Documentado no `SECURITY.md`. | `app.py`, `SECURITY.md` |
| **Disponibilidades (supervisão)**: o total "Descarga" contava um tipo que já não existe (era sempre 0); a disponibilidade para descarga num dia marcado como disponível não aparecia; a preferência "Noite" desaparecia (o template esperava "fecho"). | `supervisor_disponibilidades.html` |
| Modo escuro: o hover dos botões de ação destrutiva ("Recusar", "Cancelar pedido") ficava rosa-claro; dias feriados e preferências de horário nas Disponibilidades tinham fundos claros fixos. | `app.css`, `supervisor_disponibilidades.html` |

### Segunda passagem

| Descrição | Onde |
|-----------|------|
| **Novidades nas trocas.** O contador só contava o que havia para decidir: quem via a sua troca aprovada ou recusada, ou não era escolhido/a na central, não recebia sinal nenhum. Agora o contador inclui o que mudou desde a última visita à página Trocas (marcado "Novo"), e o cartão da página inicial diz "1 novidade". As próprias ações nunca contam. A última visita fica num cookie por dispositivo, sem alterar a base de dados. Documentado no `SECURITY.md`. | `app.py`, `trocas.html`, `index.html`, `SECURITY.md` |
| **A Disponibilidade pedia respostas para uma semana já terminada** quando a semana ativa acabou antes de a seguinte ser criada (é o caso da base de dados atual: 14 a 20 de Setembro). Semanas terminadas deixam de ser oferecidas; sem nenhuma aberta, a página diz isso. | `app.py` |
| **Painel do supervisor com a semana ativa terminada**: deixa de contar lugares por preencher e respostas em falta dessa semana (e de pôr alertas nos separadores); mostra "já terminou" e um único cartão para ativar a seguinte. | `app.py`, `supervisor_dashboard.html` |
| Painel: "2%" de respostas aparecia a verde; as cores das respostas passam a ser as mesmas do formulário do funcionário (preferência âmbar, descarga azul), com legenda e sem o símbolo "⬇"; "Ativar" numa semana já criada deixa de perguntar "Criar e ativar?"; "/5" lojas deixa de estar fixo. | `supervisor_dashboard.html` |
| Equipa: os ícones pictográficos das secções passam a marcadores neutros (como na escala); a sigla deixa de aparecer duas vezes em cada cartão; "Rank" → "Função"; um funcionário vê por omissão a semana em curso, não o rascunho da seguinte. | `equipa.html`, `app.py` |
| Página inicial: "Qui 1 · quinta" → "daqui a 2 dias"; no telemóvel, dia e data da faixa semanal deixam de se colar ("SEG28"). | `index.html` |
| Minha Escala / Equipa: "(ativa)" (termo da supervisão) passa a "esta semana" / "em construção" para os funcionários; o botão "Ver" só aparece sem JavaScript. | `minha_escala.html`, `equipa.html` |
| Modo escuro: todas as superfícies claras fixas que restavam (confirmação da disponibilidade, escala, Minha Escala, Equipa, página de erro). | vários |
| Texto: "← Dashboard" → "← Painel", "Atribuir Turnos", "Minimo", "Provas Praticas" → "Provas Práticas" (nenhum turno usava ainda este bloco). Acessibilidade: campos sem `<label for>` e botões "×" sem nome. | vários |

### Testes

| Descrição | Onde |
|-----------|------|
| 26 testes novos, na soma das duas passagens. O fluxo de trocas tinha perdido os 12 testes da primeira versão; passa a estar coberto de ponta a ponta (pedido, resposta, candidatura, escolha, aprovação, recusa, expiração, visibilidade para cada pessoa). | `tests/test_trocas_workflow.py`, `tests/test_basics.py`, `tests/test_workflow_review.py` |

---

## Revisão de fluxo de trabalho e design — Setembro 2026

Passagem página a página, como supervisor e como funcionário, em desktop e em
telemóvel. Tudo o que está abaixo foi reproduzido no browser antes de ser
corrigido e verificado depois.

### Erros visíveis

| Descrição | Onde |
|-----------|------|
| **O editor da escala do supervisor rebentava em cada carregamento** (desde o primeiro commit). `setFilter('todos')` corria antes do `let activeFilter` que usa — um `ReferenceError` que abortava o resto do script. Não funcionavam: a pesquisa de colaborador, o filtro Todos/Contrato/Turnos, os atalhos de teclado (`/`, `p`, `Esc`) e as abas de loja no telemóvel. A página dos funcionários não era afetada (esse bloco é só do supervisor). As declarações passam para o topo do script. | `escala.html` |
| **Todos os funcionários viam "Alterações não enviadas recuperadas"** sempre que abriam a Disponibilidade, sem terem alterado nada; "Descartar" recarregava para o mesmo aviso. Causa: `.draft-banner { display: flex }` anulava o atributo `hidden`. O mesmo erro deixava sempre aberto o editor de horário de cada loja nas Descargas e sempre visível o "Limpar seleção" no telemóvel. Uma regra global `[hidden] { display: none !important }` corrige os três e qualquer caso futuro. | `app.css` |
| **Reset PIN e Remover não faziam nada** para quem tem um apóstrofo no nome (`D'Almeida`, `Sant'Anna`): o nome ia dentro de `onclick="resetPin('X','…')"`, e o apóstrofo partia o JavaScript. Os botões passam `this` e leem a sigla e o nome do cartão. | `equipa.html` |
| **A semana de 27 de Abril a 3 de Maio aparecia como "27 a 3 de Maio"**, e Março estava escrito "Marco". O `import_history.py` construía o rótulo à mão; passa a usar os mesmos helpers da app, e os rótulos já gravados com essas duas formas são corrigidos no arranque (só essas formas exatas — um rótulo escolhido nunca é reescrito). | `import_history.py`, `config.py`, `app.py` |
| O `import_history.py` gravava também **todos os dias como não-feriado**, pelo que as semanas importadas nunca mostravam feriados. Corrigido para importações futuras. **As duas semanas já importadas que têm feriado (20–26 Abr: 25 de Abril; 27 Abr–3 Mai: 1 de Maio) ficaram como estavam**: a flag entra na coluna "Horas feriado" do CSV de horas, e mudá-la altera exportações de semanas provavelmente já pagas. Fica para decisão. | `import_history.py` |
| A barra de progresso do topo ficava presa a 85% depois de exportar o CSV de horas, de descarregar um `.ics`, ou de um Ctrl/Cmd+clique num link. Ignora cliques com modificador e links de download, e desaparece sozinha se a página não mudar. | `base.html` |
| Os saltos para uma loja (`#store-gaia`, a partir do painel ou dos conflitos) deixavam o cabeçalho da loja escondido por baixo da barra de navegação. | `app.css` |
| No telemóvel, o ponto laranja da marca ficava por cima do "A" de "Amorino". | `app.css` |
| O botão "Criar e ativar" das semanas só existia com o rato por cima: sem teclado e sem ecrã tátil não havia maneira de criar uma semana. | `supervisor_dashboard.html` |
| O preview local copiava só o `amorino.db` e não o `-wal`, por isso mostrava dados desatualizados (a semana ativa real já era outra); e reaproveitava o `-wal` da sessão anterior, que o SQLite aplicaria por cima da cópia nova. Passa a usar a API de backup do SQLite, como o `backup_db.py`. | `.claude/preview_app.py` |

### Fluxo de trabalho

| Descrição | Onde |
|-----------|------|
| **Conflitos por ordem de triagem**: excesso de horas do maior para o menor (um +17h estava abaixo de vários +3h), e os avisos por turno por loja e dia (Seg→Dom; havia lojas com os dias ao contrário). **Cada linha é agora um link** para onde se resolve: a loja no editor, ou a semana da pessoa. | `app.py`, `supervisor_conflitos.html` |
| **Trocas:** o supervisor vê primeiro "A aprovar" (na central só observa). Um funcionário com um pedido à espera da resposta dele também o vê primeiro. O histórico mostra a semana de cada turno ("Seg 14" sozinho é ambíguo ao longo de meses). | `trocas.html` |
| Recusar uma troca no painel usava o `prompt()` nativo do browser; passa a usar o diálogo da app (novo `promptDialog()`). Nos modais de troca, o botão diz a ação ("Negar", "Recusar", "Aprovar e aplicar") e as recusas têm o estilo de ação destrutiva. Escolher um candidato deixa de mostrar uma caixa de nota que nunca era enviada. | `base.html`, `supervisor_dashboard.html`, `trocas.html` |
| **Escape fecha o modal aberto** em todas as páginas (só o diálogo de confirmação o fazia). No editor, fecha o modal sem fechar também o painel de atribuição por trás. | `base.html` |
| **Copiar semana** vinha com origem e destino iguais (primeiro clique = erro). A origem passa a ser a semana anterior ao destino, e acompanha-o quando o destino muda. A lista de lojas vem da configuração. | `supervisor_dashboard.html` |
| O painel "Esta semana · em direto" dizia "em direto", com o ponto a pulsar e três zeros, quando nenhuma semana cobre o dia de hoje. Passa a mostrar a data de hoje e a última semana existente, sem estatísticas. | `supervisor_dashboard.html`, `app.py` |
| Equipa: sair da página com alterações por guardar pede confirmação; Enter guarda o "Novo colaborador"; os campos do modal têm `<label for>`. | `equipa.html` |
| Minha Escala: mudar de semana (ou de pessoa, no supervisor) carrega logo, como nas outras páginas. | `minha_escala.html` |
| Descargas sem dias/lojas configurados: o aviso passa a ter link para o separador Descargas do painel. Blocos de horário: Enter adiciona; a confirmação de remoção nomeia a loja. | `descargas.html`, `escala.html` |

### Segurança

| Descrição | Onde |
|-----------|------|
| O `horario` é interpolado em handlers inline na grelha (`onclick="openQuickAdd('…','<horario>',…)"`), onde o autoescape não protege. Blocos, turnos novos e turnos movidos só aceitam os caracteres de um rótulo de turno; `/api/turno/move` passa a validar a loja e a cortar os campos. Documentado no `SECURITY.md`. | `app.py`, `SECURITY.md` |

### Texto e design

| Descrição | Onde |
|-----------|------|
| Segunda passagem ao registo neutro: "Preenche", "Escolhe", "Para ti", "Pedidos que enviaste", "Copia a mensagem", "Ativa uma semana", "Seleciona", "toca para atribuir", "Escreve", "Clica", "usa", "Guarda para confirmar", "precisas de", "Já te candidataste", "Não tens permissão", "Tenta novamente", "a tua sigla" → formas impessoais. "Boa madrugada" → "Boa noite". "alteracao(oes)" → plural correto. "Clerigos" → "Clérigos". Um teste impede que estas formas voltem. | vários |
| A saudação da página inicial dizia "Semana ativa" para a semana em curso, que nem sempre é a ativa (a ativa é a que está em construção); passa a dizer "Semana atual" quando é o caso. A semana deixou de aparecer duas vezes seguidas. | `index.html`, `app.py` |
| Faixa semanal da página inicial: feriado marcado com um ponto em vez de ★; o número do dia deixou de aparecer repetido ("SEG 14 14"); legenda "barra: 06h – 06h" em vez de setas em ASCII. | `index.html` |
| O contador de trocas na navegação pulsa três vezes e para, em vez de pulsar para sempre. O painel do supervisor deixou de ter um segundo "Sair". | `app.css`, `supervisor_dashboard.html` |
| Mensagens de conflito ao atribuir turnos nomeiam a loja ("Gaia") e não o identificador interno ("gaia"). | `app.py` |

### Documentação e testes

| Descrição | Onde |
|-----------|------|
| `DEPLOYMENT.md`: `TZ=Europe/Lisbon` — a app usa a hora local para "hoje"/"agora"; num servidor em UTC fica uma hora atrasada no verão. | `docs/DEPLOYMENT.md` |
| 16 testes novos. | `tests/test_workflow_review.py`, `tests/test_basics.py` |

---

## Preparação para deployment — Setembro 2026

Passagem focada no que só falha num host real. Nada disto se vê num portátil,
em loopback, com um utilizador — que é exatamente porque faltava.

### Fiabilidade

| Descrição | Onde |
|-----------|------|
| **SQLite corria com o journal de rollback**, cujo lock exclui os leitores durante toda a escrita. Num servidor WSGI com threads isso significa que o supervisor a gravar a escala bloqueia toda a gente que a está a ler, e passado o `timeout` recebem um 500 "database is locked". Passa a **WAL** (leitores e escritor deixam de se bloquear), `synchronous=NORMAL` (o par durável do WAL, sem um fsync por commit), `foreign_keys=ON` (o SQLite ignora-as por omissão) e `busy_timeout` de 30s em vez de 5s. Aplicado em cada ligação nova, porque os PRAGMA são por ligação e o pool abre uma por thread. **Restrição:** o WAL não funciona sobre SMB/NFS — a base de dados tem de ficar em disco local. | `app.py` |
| **Não havia entrypoint de produção.** O `SECURITY.md` dizia "usa gunicorn/waitress" mas o repositório não trazia nenhum, e `python app.py` — o servidor de dev do Werkzeug — era a única forma óbvia de arrancar. Novo `wsgi.py`, com o waitress no `requirements.txt`. Documenta a razão de ser **um processo com threads** e não vários workers: os contadores do rate limit vivem em memória do processo, portanto N workers multiplicam por N o orçamento de tentativas do supervisor, e as migrações no import competiriam entre si. | `wsgi.py`, `requirements.txt` |
| **Dependências sem limite superior** (`flask>=3.0`): a versão em produção passava a ser decidida pela data do `pip install` e não por algo que tivesse sido testado. Todas passam a ter teto no major seguinte. `werkzeug` e `sqlalchemy` são agora declaradas, já que o `app.py` as importa diretamente em vez de as receber por transitividade. | `requirements.txt` |
| **Backups eram um passo do checklist sem nada que o executasse.** Novo `scripts/backup_db.py`: usa a API de backup online do SQLite (uma cópia de ficheiro pode apanhar uma transação a meio e, em WAL, ignora em silêncio os commits ainda no `-wal`), verifica a cópia com `integrity_check`, e mantém as N mais recentes. Corre com a app a servir. | `scripts/backup_db.py` |
| Novo `/healthz`. Responde à falha que um teste de TCP não vê: o processo continuar a aceitar ligações enquanto todas as páginas dão 500 porque a base de dados desapareceu. Sem sessão (um monitor não tem nenhuma) e por isso sem versão, sem hostname, sem contagens. | `app.py` |

### Segurança

| Descrição | Onde |
|-----------|------|
| **O link de subscrição do calendário dizia `http://` em produção.** Atrás de um proxy que termina TLS, a app vê o esquema como `http`, e é daí que sai o `url_for(..., _external=True)`. O funcionário copiava esse link para o telemóvel; os clientes de calendário não respeitam HSTS, portanto o token do feed — que **é** a credencial daquele endpoint — passaria em claro a cada consulta. `ProxyFix` reposto sob `AMORINO_TRUST_PROXY`, a mesma flag que o `_client_ip()` já usava, porque os headers são forjáveis por quem alcance a app diretamente. De caminho, o `request.remote_addr` passa a ser o IP de quem chamou em vez do IP do proxy, portanto o rate limit do login volta a contar a coisa certa. | `app.py` |
| Nova secção **"Rotas sem autenticação"** no `SECURITY.md`, com o `/healthz` incluído, e um teste que fixa essa lista: acrescentar outra rota sem login falha até ser uma decisão documentada. | `SECURITY.md`, `tests/test_deployment.py` |

### Desempenho

| Descrição | Onde |
|-----------|------|
| **Nada era comprimido.** `/escala` são ~195 KB de HTML, lidos ao telemóvel na loja. Compressão gzip para HTML/CSS/JSON: as seis páginas principais passam de 554 KB para 97 KB (**-82%**); a `/escala` sozinha de 195 KB para 24 KB. Feito na app e não só no proxy — a diferença entre "instantâneo" e "lento" não devia depender de uma linha na configuração do nginx de alguém. Se o proxy comprimir, vê o `Content-Encoding` já definido e passa à frente. | `app.py` |
| **Estáticos com `Cache-Control: no-cache`** (o default do Flask), o que revalidava ~140 KB de CSS e fontes em **cada** navegação — 7 idas ao servidor por página. Passam a um ano. As folhas de estilo levam versão no URL (`?v=`, do mtime+tamanho) para um deploy as poder invalidar; as fontes **não**, porque o URL do `preload` tem de ser idêntico ao do `@font-face` — versionar um e não o outro descarregaria cada face duas vezes. Se alguma fonte ou ícone for substituído, muda-se o nome do ficheiro. | `app.py`, `base.html` |

### Documentação e testes

| Descrição | Onde |
|-----------|------|
| Novo [docs/DEPLOYMENT.md](DEPLOYMENT.md): o procedimento, as variáveis de ambiente, a configuração do proxy, os backups, e a tabela de versões verificadas em conjunto. O `SECURITY.md` continua a ser a postura de segurança e remete para lá. | `docs/DEPLOYMENT.md` |
| Checklist de deployment do `SECURITY.md` reescrito: passa a incluir o servidor WSGI, a verificação do link `https://` do calendário, o restauro de backup testado, e a restrição de disco local. | `SECURITY.md` |
| 26 testes novos. Cada um guarda um comportamento que só aparece num host real. | `tests/test_deployment.py` |

---

## Revisão de design e segurança — Agosto 2026

### Segurança

| Descrição | Onde |
|-----------|------|
| **XSS armazenado.** `nome` e `horario` são texto livre escrito no painel de supervisão e eram concatenados em `innerHTML` em vários sítios: a gaveta de resultados da pesquisa e os chips de sugestão/detalhe de slot da escala, os popovers do dashboard, e os seletores de turno das trocas — este último renderizado no browser de **todos** os funcionários. Novo helper `escHtml()` (escapa `& < > " '`, cobrindo também a fuga de atributo `" onmouseover=…`) aplicado em todos eles. Verificado no browser: com o código antigo o `onerror` de um nome plantado executa; com o novo não. | `base.html`, `escala.html`, `trocas.html`, `minha_escala.html`, `supervisor_dashboard.html` |
| A nota de disponibilidade (o único campo de texto livre escrito pelo **funcionário**) também foi protegida no `buildCard()`. Este caminho está hoje em código morto (`_renderPanel_legacy_unused`), mas está guardado porque revivê-lo reintroduziria uma escalada funcionário → supervisão. | `escala.html` |
| **Enumeração de siglas no feed iCal.** `/cal/<sigla>/<token>.ics` devolvia 404 para sigla inexistente e 403 para token errado, o que permitia a qualquer pessoa sem autenticação descobrir quais siglas são pessoal real. Agora todos os casos devolvem o mesmo 404. | `app.py` (`ical_feed`) |
| **Open redirect.** `safe_referrer()` não rejeitava `\` — o browser normaliza `/\evil.com` para `//evil.com`. O `next` do login sempre cobriu isto; o `SECURITY.md` descrevia os dois da mesma maneira sem ser verdade. | `app.py` (`safe_referrer`) |
| **Cap em falta.** A nota de disponibilidade não tinha limite no servidor e o SQLite não impõe `String(200)` — aceitava até ao teto do body (256 KB). Cortada a 200. | `app.py` (`/disponibilidade`) |

### Design / UX

| Descrição | Onde |
|-----------|------|
| **Sessão expirada dava botões mortos.** `@login_required` redirecionava `/api/*` para a *página* de login; o `fetch()` seguia o redirect e o `await res.json()` de cada um dos ~40 call sites rebentava no HTML — o botão não fazia nada e nada explicava porquê. Agora `/api/*` devolve **401 JSON** e o wrapper de `fetch` do `base.html` trata-o num único sítio: avisa "A sessão expirou" e reencaminha para `/login?next=<página atual>`, para a pessoa voltar ao sítio onde estava. Páginas HTML continuam a redirecionar como antes. | `app.py` (`login_required`), `base.html` |

### Testes

| Descrição | Onde |
|-----------|------|
| Três testes de segurança do PIN de supervisor falhavam em Windows com `UnicodeDecodeError`: `subprocess.run(text=True)` descodifica com o codec local (cp1252), que não lê o `═` do banner de arranque. Nada a ver com o que estava a ser testado — mas deixava a suite vermelha, e o checklist de deploy pede `pytest` verde. | `tests/test_basics.py` (`_boot_app`) |
| 15 testes novos para tudo o que está acima. | `tests/test_security_review.py` |
| Novo `CLAUDE.md` com as regras de trabalho no repositório (não usar worktrees, não tocar na BD de produção). | `CLAUDE.md` |

---

## Auditoria de Maio 2026

Relatório de tudo o que foi mudado nesta passagem, e o que ficou para decidir.

## 🐛 Bugs corrigidos

### 🔴 Críticos

| Nº | Descrição | Onde |
|----|-----------|------|
| **1** | Disponibilidade não pré-carregava respostas anteriores — agora o GET lê da BD, mostra um banner *"Já submeteste"*, e o JS hidrata o UI. | `app.py` (rota `/disponibilidade`), `disponibilidade.html` (banner + `hydrateFromServer()`) |
| **2** | Drag-drop podia perder turnos silenciosamente (delete + add em dois requests). Substituído por novo endpoint `POST /api/turno/move/<id>` que é **atómico**: se falhar, o turno original mantém-se intacto. | `app.py` (novo `api_move_turno`), `escala.html` (`onDrop` reescrito com revert otimista) |
| **3** | Adicionar turno na mesma loja, no mesmo dia, **mesmo sem sobreposição**, era bloqueado e forçava nota "Troca de Loja" mesmo quando não havia troca. Agora a regra é mais inteligente: bloqueia só se houver duplicado exato OU sobreposição de horários. Cross-loja continua a precisar de "troca". | `app.py` (`api_add_turno` + novo helper `_shifts_overlap`) |
| **4** | Apagar um bloco-base era só em memória e voltava ao reiniciar a app. Agora há uma tabela nova `horario_supressed` que persiste a supressão. Re-adicionar um bloco suprimido **restaura-o** em vez de criar um custom-block duplicado. | `app.py` (novo model `HorarioSupressed`, `build_grid` filtra, `api_delete_horario`/`api_add_horario` reescritos) |

### 🟡 Médios / segurança

| Nº | Descrição | Onde |
|----|-----------|------|
| **5** | JS arredondava horas a 1 decimal (`Math.round(mins/6)/10`), o backend arredonda a 2. Causava diferenças de minutos. Agora ambos a 2 decimais. | `escala.html` (`parseHours`, `adjustHours`) |
| **6** | `request.referrer` usado sem validação em `marcar_revisto` (risco de open-redirect via Referer). Novo helper `safe_referrer()` aceita só paths relativos same-site. | `app.py` (helper + rota) |
| **13** | PINs em plaintext. Agora **pbkdf2-sha256** via `werkzeug.security`. PINs legacy são automaticamente upgradados no próximo login bem-sucedido. Coluna `pin` alargada para 200 chars. | `app.py` (helpers `hash_pin`, `verify_pin`, `_looks_hashed`; rota `/login`; migration) |
| **14** | Sem rate-limit no login → brute-force trivial. Limite **8 tentativas em 5 minutos** por (sigla, IP). | `app.py` (`LOGIN_ATTEMPTS`, `login_rate_limited`, helpers) |

### 🟢 Pequenos / polish

| Nº | Descrição | Onde |
|----|-----------|------|
| **7** | Templates legacy removidos: `supervisor_escala.html`, `supervisor_login.html`. | (apagados) |
| **8** | Variável `funcionarios` carregada e nunca usada na rota `/disponibilidade` — removida. | `app.py` |
| **10** | `_build_user_home` agora hidrata `dias_list`/`feriados` defensivamente se receber uma `SemanaConfig` "crua". | `app.py` |
| **11** | `next_shift` agora inclui o nome do feriado (`feriado_nome`) quando aplicável. | `app.py` (`_build_user_home`) |
| **12** | Seed deixou de criar uma semana ativa hardcoded em `2026-05-18`. Agora cria a semana de **hoje** (segunda atual). | `app.py` (`seed_db`) |

## ✨ Novas funcionalidades / melhorias UX

| Nº | Descrição | Onde |
|----|-----------|------|
| **19** | Já existia botão "Cancelar" no quick-add (verificado durante a auditoria). | — |
| **20** | `confirm()` nativo removido da remoção de turnos. Agora delete instantâneo. | `escala.html` (`removeTurno`) |
| **21** | **Toast "Desfazer"** durante 7 segundos depois de cada remoção. Recria o turno se o utilizador carregar. | `escala.html` (`showUndoToast`) |
| **22** | Rodapé com **nome da semana + número de página** em cada página dos PDFs (escala completa, por loja, individual). | `gerar_pdf.py` (`_draw_footer`, callbacks `onFirstPage`/`onLaterPages`) |
| **23** | Acessibilidade: `aria-label` nos botões só com símbolos (`+`, `×`, `↺`), focus-ring nos chips, role/tabindex no chip-del. | `escala.html`, `disponibilidade.html` |
| **24** | **Mobile loja-picker**: em ecrãs <700px aparece um tab-strip horizontal "Todas / Carmelitas / Clérigos / Norte / Gaia / MBS". Escolhe uma → só essa loja é mostrada. Persistido em `sessionStorage`. | `escala.html` |
| **25** | **Indicador "atualizada agora"** no topo da escala — pill com timestamp da última alteração, refresca a cada 30s, atualiza imediatamente quando o supervisor add/move/remove. | `app.py` (coluna `Turno.updated_at` + migration; query mais recente), `escala.html` (pill + JS) |

## 🧪 Testes (#16)

Suite pytest em `tests/test_basics.py` com **19 testes** cobrindo todos os bugs acima:

- Auth: login OK, PIN errado, primeiro login (hash), upgrade de plaintext, rate-limit
- Disponibilidade: GET em branco vs GET com preload, submissão
- Turnos: add same-loja overlap/non-overlap, cross-loja com/sem troca, move atómico (preserva em conflito), move válido
- Blocos: supressão persiste, restaurar, recusa quando em uso
- Segurança: `safe_referrer` rejeita external, aceita relativo
- Helpers: `_shift_hours`, `_shifts_overlap`

```bash
$ pip install pytest
$ pytest tests/ -v
# 19 passed in ~4s
```

## 📋 O que **NÃO** foi feito (decisão para ti)

Os pontos abaixo são refactors estruturais — não são bugs, são opiniões sobre como o código deveria estar organizado. Não os fiz porque envolvem **muitas linhas de mudança em ficheiros que tu queres manter familiares** e mereces opinar primeiro.

### #15 — Partir `app.py` (1890 linhas)

Sugestão de estrutura com Flask blueprints:

```
app.py              # 30 linhas: cria app, regista blueprints
models.py           # todas as `db.Model`
helpers.py          # _shift_hours, _shifts_overlap, week_*, hash_pin, etc.
auth.py             # login, logout, decorators, rate-limiting
routes/
  public.py         # /, /login, /escala, /disponibilidade, /equipa, /minha-escala
  supervisor.py     # /supervisor/*
  api.py            # /api/turno/*, /api/horario/*, /api/funcionario/*, etc.
```

**Custo:** ~2 horas, mexe em tudo. Risco de regressão se não houver testes a cobrir as rotas (o que agora temos!).
**Benefício:** muito mais fácil de navegar; cada ficheiro <400 linhas; menos merge conflicts se forem várias pessoas a editar.

### #17 — Migrações com Flask-Migrate (Alembic)

Hoje as migrações são `ALTER TABLE` ad-hoc em `app.py:425–470`. Funcionam, mas:
- Não há rollback
- Não dá para renomear colunas, só adicionar
- Cada vez que adicionas uma coluna nova, partes o app local de quem ainda não correu

Migrar é fazer:
```bash
pip install Flask-Migrate
flask db init
flask db migrate -m "initial"
flask db upgrade
```
E depois remover o bloco `ALTER TABLE` manual. **Custo:** 1 hora. **Benefício:** profissionalismo + segurança em produção.

### #18 — Extrair JS de `escala.html` (1561 linhas, ~700 são JS)

Atualmente todo o JS está inline no template. Mover para `static/js/escala.js`:
- Browser cacheia ⇒ páginas mais rápidas em visitas seguintes
- Editor com syntax highlighting + linting
- Permite usar uma source-map para debug

**Subtleza:** o JS hoje usa template-strings Jinja (`{{ semana_atual }}`, `{% if is_supervisor %}`). Para extrair, ou (a) injeta-se um `<script>` curto no HTML com as variáveis e o `.js` lê delas, ou (b) gera-se o `.js` por uma rota Flask que serve JS templado.

A solução (a) é mais simples:
```html
<script>window.AMORINO_CFG = {semana:'{{ semana_atual }}', isSupervisor:{{ is_supervisor|tojson }}};</script>
<script src="/static/js/escala.js"></script>
```

**Custo:** 1 hora.

## 📁 Estado dos ficheiros

```
app.py                       1890 linhas (era 1581) — +18%
gerar_pdf.py                  434 linhas (era  420) — +footer callbacks
escala.html                  1561 linhas (era 1349) — undo toast, mobile picker, last-updated pill, move endpoint
disponibilidade.html          814 linhas (era  791) — preload + banner
[suprimidos]                supervisor_escala.html, supervisor_login.html (dead code)
tests/test_basics.py          395 linhas (novo)
```

## 🚨 Migração

Se já tens uma BD com dados:

1. **Nada a fazer manualmente** — a app aplica as migrações ao arrancar (novas colunas `Turno.updated_at`, nova tabela `horario_supressed`, coluna `pin` alargada).
2. **PINs legacy continuam a funcionar** — vão ser hashados automaticamente no próximo login de cada utilizador.
3. **Variável de ambiente nova (opcional):**
   - `AMORINO_SECRET_KEY` — override do `secret_key` (recomendado em produção)
   - `AMORINO_SUPERVISOR_PIN` — override do PIN de supervisor (default `1234`)

## 🎯 Próximos passos sugeridos (por ordem de impacto)

1. **Mudar o PIN de supervisor** — `AMORINO_SUPERVISOR_PIN=algo-que-só-tu-saibas`
2. **Mudar a `secret_key`** — `AMORINO_SECRET_KEY=$(openssl rand -hex 32)`
3. Decidir sobre #15, #17, #18 (refactors). Se sim, fazê-los **antes** de adicionar features novas — quanto mais código, mais doloroso é mover depois.
4. (Eventualmente) o auto-scheduler que descartámos no início desta conversa. Agora que existem testes, é seguro implementar.

---

# Passagem de Segurança (Maio 2026, 2ª passagem)

Depois dos bugs anteriores, fiz uma passagem dedicada de segurança. Ver `SECURITY.md` para a documentação completa da postura defensiva.

## Resumo do que mudou

**Defesas novas:**
- ✅ **CSRF tokens** em todas as requests state-changing (POST/PUT/PATCH/DELETE). Tokens injetados automaticamente em `fetch()` via `base.html`.
- ✅ **Security headers**: CSP, X-Frame-Options DENY, X-Content-Type-Options nosniff, Referrer-Policy, Permissions-Policy, Cache-Control no-store em HTML.
- ✅ **HSTS** ativável via `AMORINO_HTTPS=1`.
- ✅ **Session hardening**: HttpOnly + SameSite=Lax + (opt) Secure cookies, lifetime 12h enforced server-side, ID rotation no login, validação por request.
- ✅ **Audit log** (tabela `audit_log`) com 12+ ações sensíveis registadas.
- ✅ **Rate limiting** melhorado: per-(sigla,IP) + global per-sigla, ADMIN mais restritivo.

**Login hardening:**
- ✅ Mensagem de erro genérica ("Sigla ou PIN incorreto") — não revela quais siglas existem.
- ✅ PIN mínimo 4 caracteres no primeiro login.
- ✅ Constant-time PIN compare + fake-hash para siglas inexistentes (mata timing attacks).
- ✅ Warning loud no startup se `AMORINO_SECRET_KEY` ou `AMORINO_SUPERVISOR_PIN` forem default.

**Input validation:**
- ✅ Length caps em todos os campos (sigla, nome, nota, horario, semana, etc.)
- ✅ Whitelist de valores enumerados (loja, tipo, rank, slot)
- ✅ Validação de caracteres em sigla (alfanumérico + `.-_`)
- ✅ Sigla `ADMIN` reservada
- ✅ MAX_CONTENT_LENGTH = 256 KB (bodies maiores → 413)
- ✅ Range checks (horas 0-80)

**Authorization:**
- ✅ `@supervisor_required` em `/api/*` devolve 403 JSON, não redirect.
- ✅ Verificação por request: se a sigla na sessão já não existir, sessão é limpa.
- ✅ Verificação de sigla `ADMIN` em supervisor session (defende contra session manipulation).

**Variáveis de ambiente novas:**
- `AMORINO_HTTPS=1` — ativa Secure cookies + HSTS quando estás atrás de HTTPS
- `AMORINO_DB_PATH=...` — útil para tests/sandboxes; em produção deixar default

## Testes

20 testes de segurança novos. Total agora: **39 testes**, todos passam.

```bash
pytest tests/ -v
# 39 passed
```

## Files alterados nesta passagem

- `app.py`: +200 linhas (CSRF, audit, headers, session, validation)
- `base.html`: meta tag + fetch wrapper que injeta X-CSRF-Token
- `disponibilidade.html`, `supervisor_disponibilidades.html`: hidden CSRF input em forms
- `tests/test_basics.py`: 20 testes novos de segurança
- `SECURITY.md`: NOVO — documenta toda a postura defensiva

---

# Passagem de UX/Calendar (Maio 2026, 3ª passagem)

Nova **feature destacada** + polish visual generalizado.

## ✨ Nova feature: Calendário iCal subscritível

A pergunta que isto resolve: **"posso ter os meus turnos no calendário do telemóvel sem ter de abrir o site?"**

### Como funciona

1. Funcionário vai a `/minha-escala` → vê um cartão laranja com texto *"Adicionar ao calendário do telemóvel"* e botão **Subscrever**.
2. Clica → abre um modal com:
   - **QR code** para escanear com a câmara do telemóvel (apontas → iOS pergunta se queres subscrever)
   - **URL `webcal://`** para copiar manualmente (Android, Outlook, etc.)
   - **Botão "Abrir"** para deep-link no calendário do sistema
   - **Instruções passo-a-passo** para iPhone e Android/Google Calendar
   - **Botão "Gerar novo link"** para revogar o link atual (se foi partilhado por engano)
3. O calendário do telemóvel passa a mostrar os turnos como eventos. Quando o supervisor mexe na escala, **atualiza automaticamente** dentro de minutos a horas (depende do cliente).

### Implementação técnica

- **Nova coluna** `Funcionario.ical_token` — random URL-safe (32 bytes) por funcionário, gerada lazily na primeira utilização.
- **Endpoint `GET /cal/<sigla>/<token>.ics`** devolve um feed RFC 5545 com todos os turnos do funcionário (passados + futuros). Sem auth de sessão — calendar apps não conseguem enviar cookies; o token é o bearer.
- **Endpoint `GET /cal/<sigla>/<token>.svg`** devolve um QR code SVG (sem Pillow — `qrcode` lib em modo SVG-puro) com a URL `webcal://`.
- **`GET /api/cal-link`** devolve a URL atual (cria token se preciso). Supervisor pode passar `?sigla=XYZ` para obter URL de qualquer funcionário.
- **`POST /api/cal-link/rotate`** invalida o token actual e gera um novo. Audited.

### Segurança

- **Token de 32 bytes URL-safe** (entropy ≈ 192 bits) — brute-force totalmente impraticável.
- **Comparação constant-time** com `hmac.compare_digest` — defesa contra timing attacks.
- **404 para siglas desconhecidas, 403 para tokens errados** — não distingue (não revela se a sigla existe).
- **Sem informação sensível no feed** — só o que o funcionário já vê na sua página `/minha-escala`.
- **Rotação manual disponível** — se o link vazar, novo token + URL antiga deixa de funcionar imediatamente.

### Nova dependência

```bash
pip install qrcode
```
(Não usa Pillow. SVG puro.)

## 🎨 Polish visual

**`base.html`:**
- Body com gradient subtil (warm radials) em vez de cor sólida
- Font rendering melhorado (`-webkit-font-smoothing`, `text-rendering`, OpenType features do Inter)
- Nav com gradient + ponto laranja brilhante ao lado do logo + sombra interna sutil
- Botões: gradients suaves, sombra interna, estado `:active` afundado, transições com easing
- `:focus-visible` rings acessíveis (visíveis só na navegação por teclado)
- Selecção de texto em cor da marca
- Toast com glass blur e easing curva (cubic-bezier)
- Modal overlay com `backdrop-filter: blur(4px)` + animação de entrada
- `prefers-reduced-motion` respeitado

**`login.html`:**
- Glow laranja decorativo atrás do card
- Animação de entrada do card (`cardEntry`)
- Tipografia refinada (-0.3px tracking no título)

**`index.html`:**
- `next-shift-card` com 3 layers de gradients + diagonal stripe overlay + dot laranja brilhante + botão com lift hover
- Tiles com stripe laranja no top que se desenha em hover (esquerda → direita)
- Stats com hover lift sutil

**`minha_escala.html`:**
- Novo card "Adicionar ao calendário" com gradient, ícone, badge "novo"

## 🧪 Testes adicionais (11)

- `cal_link_mints_token_on_first_call`
- `cal_link_admin_has_no_calendar`
- `cal_link_supervisor_can_query_any_user`
- `ical_feed_serves_valid_calendar`
- `ical_feed_rejects_bad_token`
- `ical_feed_404_for_unknown_sigla`
- `ical_feed_uses_constant_time_token_compare`
- `cal_token_rotate_invalidates_old`
- `cal_qr_endpoint_returns_svg`
- `cal_qr_rejects_bad_token`
- `cal_link_no_auth_required_to_use_feed`
- `cal_modal_visible_on_minha_escala`

**Total agora: 51 testes, todos passam.**

## Files alterados

- `app.py`: +~220 linhas (modelo iCal + 4 endpoints + helpers)
- `base.html`: polish geral (gradients, transições, accessibility)
- `index.html`: polish (next-shift hero, tiles, stats)
- `login.html`: polish (glow, entry animation, typography)
- `minha_escala.html`: novo card + modal completo + JS
- `tests/test_basics.py`: +11 testes iCal

---

# Passagem 4 — Working Week + Trocas de Turno (Maio 2026)

Duas mudanças importantes pedidas pelo user.

## 🐛 Fix: dashboard mostrava a semana errada

**Problema:** O dashboard `/` (e `/minha-escala` sem `?semana=`) usava a "semana ativa" do supervisor — que é a semana **em construção** (drafting), não a semana onde o funcionário está realmente a trabalhar.

**Resultado visível:** "Sem turnos esta semana" para uma pessoa que está literalmente a meio da semana de trabalho.

**Fix:** Nova função `get_semana_for_employee()` com a regra (decidida com o user):

1. Se a semana ativa do supervisor contém **hoje** → usa essa
2. Senão, encontra a semana **mais antiga (lowest id) que contém hoje**
3. Fallback à semana ativa se nada contém hoje

**Bónus no dashboard:**
- Badge no topo: *"A tua semana — 11 a 17 de Maio"*
- Quando a próxima já está em construção: aviso *"Próxima (1 a 7 de Junho) ainda em construção"*
- Horas mostradas como progresso (`12.5/16h`) em vez de só total
- Contagem "feitos · por fazer"
- Cartão "Disponibilidade" referencia a semana de drafting correta

## ✨ Feature nova: Trocas de Turno

A feature mais pedida em apps de turnos: **pedir troca com um colega**, com aprovação obrigatória da supervisão.

### Modelo: `TrocaPedido`

```
id, criado_em, atualizado_em
de_sigla, para_sigla, turno_origem_id, turno_destino_id (NULL = cobertura)
motivo, estado, resposta_colega, resposta_supervisor, decisao_em
```

### State machine

```
                  [criar]
                     ↓
              pendente_colega
              /     |      \
   [B aceita] [B recusa] [A cancela]
       ↓         ↓          ↓
pendente_supervisor  recusado_colega  cancelado
       /        \
 [aprovar]  [negar]
     ↓          ↓
  aprovado*   negado
  (turnos
  trocados!)
```

\* Na aprovação, as colunas `funcionario` dos turnos correspondentes são realmente atualizadas (com validação extra para detetar drift entre o aceite do colega e a decisão da supervisão).

### Tipos de troca

- **Troca 1↔1** clássica (A dá turno X, B dá turno Y, ficam trocados)
- **Cobertura** (one-way): A diz "alguém me cobre?", B aceita, A fica sem turno

### Endpoints

| Método | Path | Quem | Descrição |
|---|---|---|---|
| `GET`  | `/trocas` | Todos | Inbox (a decidir / em curso / histórico) |
| `POST` | `/api/trocas` | Funcionário | Criar pedido |
| `POST` | `/api/trocas/<id>/responder` | Colega destinatário | Aceitar/Recusar |
| `POST` | `/api/trocas/<id>/cancelar` | Autor | Cancelar |
| `POST` | `/api/trocas/<id>/decidir` | Supervisor | Aprovar/Negar |
| `GET`  | `/api/trocas/contagem` | Todos | Badge counter |
| `GET`  | `/api/trocas/turnos-do-colega` | Todos | Lista de turnos para o modal de pedir |

### UI

- **Botão "⇄ Pedir troca"** em cada cartão de turno em `/minha-escala`
- **Modal de 4 passos**: o teu turno → escolher colega → escolher turno deles (ou cobertura) → motivo opcional
- **Página `/trocas`** com 3 buckets: *Para ti decidir* (urgente, com pulse animation) · *Pedidos que enviaste* · *Histórico*
- **Cartões visuais** com avatares ⇄ avatares e blocos de horário lado-a-lado
- **Pill colorida** para o estado (laranja para pendente, verde para aprovado, etc.)
- **Modal de confirmação** ao aceitar/recusar/aprovar/negar — permite adicionar nota
- **Badge no topbar** (laranja, com pulse) ao lado do link "Trocas" mostrando o nº de itens à espera da tua ação
- **Audit log** regista cada transição com actor + detalhes

### Segurança

- **CSRF token** em todos os POSTs (auto-injetado pelo wrapper de `base.html`)
- **Validação por servidor**: tu só podes pedir trocas dos **teus** turnos
- **Não dá para aceitar o próprio pedido** (que era um trick óbvio)
- **Não dá para auto-aprovar** como supervisor (precisa de sessão admin)
- **Re-validação** na aprovação: se o turno foi alterado entre o aceite do colega e a aprovação, a aprovação é rejeitada (defesa contra race condition / supervisor manual edit)
- **Duplicate open requests** rejeitados (não dá para spam o colega)

### 12 testes novos

- Happy path (criar → aceitar → aprovar)
- Cobertura (one-way assignment)
- Recusa pelo colega (no swap)
- Negação pela supervisão (no swap)
- Não dá para aceitar o próprio pedido (403)
- Não dá para decidir como sup sendo employee (403)
- Não dá para pedir para si mesmo (400)
- Não dá para usar turno alheio como origem (403)
- Não dá para abrir 2 pedidos idênticos (409)
- Autor cancela
- Audit log regista todos os passos
- Counter de pendentes via context processor

**Total: 67 testes a passar.**

### Files alterados

- `app.py`: novo modelo `TrocaPedido` + 7 endpoints + helpers + context_processor para o badge
- `base.html`: link "Trocas" no nav com badge animado (pulse)
- `minha_escala.html`: botão "Pedir troca" + modal de 4 passos + JS do fluxo
- `trocas.html`: **novo template** — inbox com 3 buckets, cartões, modal de confirmação
- `index.html` + `minha_escala.html`: novo dashboard com working-week
- `tests/test_basics.py`: +15 testes (12 trocas + 3 working-week)

---

# Passagem 5 — "Esta semana" no painel do supervisor

Tu pediste para ver a semana de hoje no dashboard do supervisor. Antes o dashboard mostrava apenas a "semana ativa" (drafting), que pode ser uma semana futura quando o supervisor está a construir a próxima escala. Agora há um painel dedicado **"Esta semana · em direto"** sempre no topo.

## O painel

Localizado no topo de `/supervisor`, sempre visível. Mostra:

- **Dia de hoje** com o ponto laranja pulsando ("ao vivo")
- **3 stats** lado-a-lado:
  - `N turnos` em curso hoje
  - `Xh horas hoje` total
  - `N/5 lojas abertas` (quantas das 5 lojas têm pessoal hoje)
- **Quando o supervisor está a construir a próxima semana**: badge "A construir a próxima semana" no canto

Por baixo, uma **grelha de 5 lojas** (uma card por loja):
- Cada card lista o pessoal de hoje, ordenado por horário de início
- Mostra `08:00-16:30 · FO Felipe` por linha
- Notas (ex: "troca") aparecem como pill laranja
- Funcionários `turnos` (avenca) com a sigla em azul para os distinguir dos contrato
- **Lojas sem pessoal aparecem com fundo cinza e label "vazio"** em vermelho — alerta visual imediato

Quando o supervisor está a planear a próxima semana, há também um **header clarificador** antes das estatísticas de disponibilidade:

> **PRÓXIMA SEMANA — 1 a 7 de Junho — a construir**

Para nunca haver confusão entre "o que está a acontecer" vs "o que estou a planear".

## Edge case: semana de hoje não existe

Se nenhuma `SemanaConfig` cobre a data de hoje (gap), o painel mostra:

> **Nenhuma semana cobre o dia de hoje**
> Cria uma nova semana para começar a registar os turnos atuais.

## 4 testes novos

- `test_supervisor_dashboard_shows_today_panel`
- `test_supervisor_today_panel_shows_per_loja_data`
- `test_supervisor_today_panel_distinguishes_drafting_week`
- `test_build_supervisor_today_panel_helper`

**Total: 71 testes a passar.**

## Files alterados

- `app.py`: nova função `_build_supervisor_today_panel()` + integração no route `/supervisor`
- `supervisor_dashboard.html`: novo painel + estilos (`.today-panel`, `.today-loja-grid`, pulse-dot animation)
- `tests/test_basics.py`: +4 testes

---

# Passagem 6 — "Doação" + Central de Trocas (Maio 2026)

Tu pediste duas coisas:
1. Renomear "Cobertura" para "Doação" (como vocês chamam no Amorino)
2. Central onde alguém publica pedidos abertos para qualquer colega aceitar

Está tudo feito. 18 testes novos. **Total: 89 testes a passar.**

## 🏷️ Rename: Cobertura → Doação

Substituído em todo o UI:
- `app.py`: docstrings, comentários, audit detail string
- `minha_escala.html`: classe CSS `.cobertura` → `.doacao`, texto "Cobertura" → "Doação"
- `trocas.html`: "cobertura por X" → "doação para X", textos das pills

**Retro-compat:** o JSON da API ainda emite `is_cobertura` como alias de `is_doacao` para não partir clientes antigos.

## ✨ Central de Trocas

A funcionalidade que mais se aproxima do que apps tipo Deputy/When-I-Work têm: **um mural onde se publicam pedidos sem destinatário específico, qualquer colega se candidata, e quem publicou escolhe**.

### Modelo

- Coluna nova `TrocaPedido.tipo`: `'direto'` (default, igual ao que havia) | `'aberto_doacao'` | `'aberto_troca'`
- Nova tabela `TrocaCandidatura`: `troca_id, sigla, comentario, turno_oferecido_id, criado_em, status`
- Migração: SQL automático adiciona `tipo` em deploys existentes. A nova tabela vem por `db.create_all()`.

### Estados (alargados)

```
                  PUBLICAR
                      ↓
                  ┌──────┐
                  │aberto│ ←─── candidatos podem candidatar-se livremente
                  └──┬───┘
                     │ [autor escolhe candidato]
                     ↓
            ┌────────────────────┐
            │pendente_supervisor │ ←─── fluxo idêntico ao direto daqui em diante
            └────────────────────┘
                  /        \
            [aprova]    [nega]
                ↓           ↓
            aprovado     negado
```

### 5 endpoints novos

| Método | Rota | Quem | O que faz |
|---|---|---|---|
| `POST` | `/api/trocas/abrir` | Funcionário | Publica pedido (doação ou troca) |
| `GET`  | `/api/central` | Todos | Lista pedidos abertos + flag de conflito |
| `POST` | `/api/trocas/<id>/candidatar` | Funcionário | Candidata-se (com turno opcional) |
| `POST` | `/api/trocas/<id>/retirar-candidatura` | Próprio candidato | Retira candidatura |
| `POST` | `/api/trocas/<id>/escolher` | Autor | Escolhe um candidato → vai para supervisão |

### Tipos de pedido

- **Doação aberta** (`aberto_doacao`): "alguém me cobre? quem quiser, fica com o turno"
- **Troca aberta** (`aberto_troca`): "quero trocar este turno; quem se candidatar tem de oferecer outro turno seu"

### Detecção de conflito visual

Helper `_has_turno_conflict(sigla, dia, horario, semana)` verifica se o viewer já tem um turno que se sobrepõe. Na Central, pedidos com conflito mostram pill vermelho ⚠️ "tens turno sobreposto" — o utilizador vê imediatamente que se aceitar vai criar um conflito.

### UI

**`/trocas` (página principal):**
- Nova secção **"Central — pedidos abertos"** no topo
- CTA laranja: *"Quer doar ou trocar um turno? Publica um pedido."* → leva a `/minha-escala`
- Cartões dos pedidos abertos com:
  - Pill verde "Doação" ou azul "Troca"
  - Nome do autor + horas em texto
  - Pill ⚠️ "tens turno sobreposto" se aplicável
  - Botão "Eu posso!" / "Candidatar com um turno meu" (consoante tipo)
  - Para o autor: lista das candidaturas + botão "Escolher" em cada
  - Status visual ✓ "Já te candidataste" se o viewer já se candidatou

**`/minha-escala` (em cada cartão de turno do utilizador):**
- Novo botão **"📣 Publicar na central"** ao lado do "⇄ Pedir troca"
- Abre modal de 3 passos: turno → tipo (toggle Doar/Trocar) → motivo opcional

**Fluxo cross-page** para `aberto_troca`:
- Se carregar em "Candidatar com um turno meu" na Central, redireciona para `/minha-escala?candidatar=<id>` e abre lá um sub-modal para escolher qual dos teus turnos oferecer

**Badge do topbar:** o contador de pendentes inclui agora "sou autor de pedido aberto com pelo menos uma candidatura pendente" — ou seja, vês sempre o número quando há candidatos à espera da tua escolha.

### Segurança

- Não te podes candidatar ao teu próprio pedido (400)
- Não podes oferecer turno alheio (400)
- Só o autor pode escolher (403)
- Sem candidaturas duplicadas (409)
- Sem 2 pedidos abertos no mesmo turno (409)
- Re-validação no `escolher`: se o turno oferecido foi alterado entre a candidatura e a escolha, é rejeitada (race-condition defense)
- Audit log: `central_publicar`, `central_candidatar`, `central_retirar`, `central_escolher`

### 18 testes novos

Cobrem:
- Publicar doação / troca / tipo inválido / turno alheio / duplicate-open
- Candidatar / não próprio / não duplicate / troca requer turno / turno alheio não permitido
- Escolher promove para `pendente_supervisor` e supervisora consegue aprovar → turno reassigned
- Só autor pode escolher
- Retirar candidatura permite re-candidatar
- Counter de autor com candidaturas pendentes
- Detecção de conflito (viewer com turno sobreposto)
- Audit log captura cada passo
- **Regression**: palavra "cobertura" deixou de aparecer no HTML
- Página renderiza para supervisor

## Ficheiros alterados

- `app.py`: +~330 linhas (modelo `TrocaCandidatura`, 5 endpoints, helpers, migration)
- `trocas.html`: secção Central no topo + cards + handlers JS
- `minha_escala.html`: botão "📣 Publicar" + modal de 3 passos + handler de redirect `?candidatar=`
- `tests/test_basics.py`: +18 testes

## Migração para utilizadores existentes

1. Substitui os 4 ficheiros (`app.py`, `trocas.html`, `minha_escala.html`, `tests/test_basics.py`)
2. Reinicia a app
3. A migração SQL corre automaticamente — só adiciona a coluna `tipo` à tabela `trocas` existente e cria a nova tabela `trocas_candidaturas`
4. Nenhum dado existente é tocado; pedidos antigos continuam a funcionar com `tipo='direto'` (default)


---

# Passagem 7 — UI cleanup: emojis fora, segmented control

Tu vias o modal "Publicar pedido" com botões Doar/Trocar a parecerem cinzentos flat e disseste "não gosto destes botões, nem do nome com emoji". Razão simples: o CSS dos botões vivia em `trocas.html` mas o modal renderiza em `minha_escala.html` — as regras nunca chegavam à página. Já está corrigido + fiz um pass de limpeza de emojis em toda a app.

## Modal Publicar — segmented control bem-feito

- CSS dos `.pub-tipo-toggle` movido para `minha_escala.html` onde o modal vive
- Design refeito: track em `--bg2`, padding 3px à volta, opção selecionada com card branco + sombra subtil (segmented control nativo iOS-style)
- Eliminado o wrapping awkward de duas linhas — labels agora numa linha só ("Doar" / "Trocar")
- Linha de ajuda por baixo que muda quando alternas entre Doar e Trocar
- Atributos `role="tablist"` + `aria-selected` para acessibilidade
- Animações suaves nas transições (cor, sombra)

## Emojis fora em toda a app

| Antes | Depois |
|---|---|
| `📣 Publicar na central` | `Publicar na central` |
| `⇄ Pedir troca` | `Pedir troca` |
| `⤓ Descarregar PDF` | `Descarregar PDF` |
| `📅` ícone gigante no cartão iCal | (removido — tipografia chega) |
| `📋` ícone do estado vazio na Central | (removido) |
| `🔑 🚫 🏪 📉 ⚠️` ícones nos cartões de conflitos do supervisor | (removidos — banda de cor + accent strip à esquerda chega) |
| `✅` no banner "sem conflitos" | círculo verde limpo com `✓` |
| `↪ →` setas decorativas dentro de botões | (removidas — hover já dá a affordance) |

### O que ficou (e porquê)
- `✓` `✗` — indicadores de estado universais, não emojis
- `⇄` `⇅` `↪` `⚠` `↺` — caracteres tipográficos Unicode, não emojis a cores
  - O conflict pill ainda diz "⚠ tens turno sobreposto" porque sem o símbolo perderia urgência visual
  - O `⇅` mobile fallback do `⇄` indica direção da troca semanticamente
  - O `↺` em "Usar globais" indica reset/redo

### "novo" badge no cartão iCal removido
Já não é novo — fica subjetivo dizer-se durante quanto tempo é "novo". Texto e gradient já chegam.

## Testes

Todos os 89 testes continuam a passar. O regression-test `test_central_doacao_label_no_longer_says_cobertura` continua a verificar a presença de "Doação" e ausência de "cobertura".

## Ficheiros alterados

- `minha_escala.html`: segmented control novo, emojis fora dos botões, CSS limpo
- `trocas.html`: emoji `📋` fora, `.tr-empty-icon` virado em chip verde, CSS dos `pub-tipo-*` removido (estava no sítio errado)
- `supervisor_conflitos.html`: 6 emojis fora, header com accent strip à esquerda, banner "sem conflitos" com círculo verde limpo


---

# Sessão de manutenção — 28 de Junho 2026

Passagem de revisão geral: arranjar a suite de testes, limpar duplicados mortos, e
adicionar a exportação de horas para folha de pagamento.

## 🐛 Bugs / saúde do projeto

| Área | O que estava mal | Resolução |
|------|------------------|-----------|
| **Suite de testes** | 4 testes em `tests/test_basics.py` falhavam — não por bugs na app, mas por verificarem texto/comportamento desatualizado: o banner de disponibilidade foi reescrito ("submetida para esta semana"), a mensagem de sobreposição usa "sobrepõe" (acento), a eliminação de blocos passou a ignorar semanas passadas (Junho 2026), e o reforço de "responsável" renomeia o motivo histórico. | Testes atualizados para refletir o comportamento atual (que está correto). `test_horario_delete_refuses_when_in_use` agora fixa o bloco numa **semana futura**; `test_historical_affinity_*` pré-coloca um abridor para isolar o sinal histórico. |
| **Colisão do `pytest`** | Corria-se `pytest tests/`, mas um `pytest` simples **falhava na coleção** (`import file mismatch`) por existirem dois `test_basics.py` (raiz + `tests/`). | Removido o `test_basics.py` da **raiz** (cópia antiga de 739 linhas; o de `tests/` é superconjunto com 63 testes). `pytest` simples agora funciona. |
| **Módulos duplicados mortos** | `helpers/config.py` e `helpers/models.py` eram cópias **divergentes e não importadas** (códigos de loja diferentes — `CRM`/`CLR` vs `CAR`/`CL` — e faltavam colunas `descarga_*`). Uma armadilha: um `from helpers.config import …` acidental traria dados errados. | Apagados. Tudo importa de `config`/`models` na raiz (confirmado). |
| **Duplicados na diretoria** | Pasta `pdf/` com cópias: `gerar_pdf.py` (idêntico ao da raiz que a app importa), `IMPORT_HISTORY_README.md` (idêntico), 4 PDFs (idênticos aos da raiz). `pdf/import_history.py` era na verdade **mais recente** que o da raiz (fix do parsing do ano + flag `replace`). Havia ainda `REFERENCIA.md` == `REFERENCIA_v2.md` (byte-a-byte, superseded por v3) e `instance/amorino.db` obsoleto não usado (a app usa o `amorino.db` da raiz). | `import_history.py` da raiz **atualizado** para a versão mais recente; pasta `pdf/` removida; `REFERENCIA.md`/`_v2.md` removidos (v3 fica); `instance/amorino.db` removido. Nada no código referencia `pdf/`/`instance/`. App + 79 testes intactos; BD viva (46 func., 774 turnos) intocada. `AMORINO_ESCALAS_REFERENCE.md` (master-ref EN) mantido — não é duplicado. |

## ✨ Nova funcionalidade

### Exportação de horas (CSV) para folha de pagamento
Os PDFs servem para imprimir/afixar; não dão para analisar. Nova rota
`GET /supervisor/exportar-horas-csv?semana=<codigo>` gera um CSV pronto para Excel:

- Uma linha por pessoa que trabalhou na semana + linha **TOTAL**.
- Colunas: Sigla, Nome, Tipo, Contrato (h), Turnos, Horas turnos, Descargas,
  Horas descargas, **Horas feriado** (dias de feriado — paga em dobro), Total horas,
  Diferença vs contrato.
- Horas de turno **e** de descarga somadas com o mesmo `_shift_hours` do resto da app.
- Formato amigável para Excel-PT: delimitador `;`, decimais com vírgula, BOM UTF-8
  (acentos corretos).
- Botão **"Exportar horas (CSV)"** na barra de ações do painel do supervisor, ao lado
  de "Exportar PDF".

Onde: `app.py` (`exportar_horas_csv`), `supervisor_dashboard.html` (botão).

### Copiar disponibilidade da semana anterior
Muitos colaboradores têm uma disponibilidade estável semana após semana, mas
tinham de a reintroduzir do zero todas as semanas. Agora, quando a semana ativa
ainda está em branco para a pessoa, a página `/disponibilidade` oferece um banner
**"Copiar da semana passada"** que pré-preenche o formulário com as respostas da
**semana anterior em que essa pessoa submeteu** (mapeadas por dia-da-semana para
os dias da semana atual). É só ajustar o que mudou e enviar.

- Só aparece quando não há respostas submetidas para a semana ativa — nunca
  sobrescreve respostas reais.
- O opt-in de descarga só é copiado se a semana atual tiver descarga nesse dia.
- Reutiliza a mesma rotina por-dia do `hydrateFromServer()` (sem duplicar lógica).

Onde: `app.py` (rota `/disponibilidade` → `prev_prefill`), `disponibilidade.html`
(banner + `copyFromLastWeek()`).

### Avisos no momento de escalar
A página de conflitos era reativa — só mostrava problemas depois. Agora, ao
atribuir um turno, o `api_add_turno` devolve um `aviso` (não bloqueia) e o
construtor mostra-o no toast da atribuição. Avisa quando a pessoa:
- se marcou **indisponível** nesse dia,
- passa o **contrato** em mais de 2h (mostra as horas),
- fica com **6+ dias** de trabalho na semana.

Cobre todas as vias de atribuição (painel de sugestões, drag-drop, quick-add,
slot-picker) via um helper `avisoToast` único. Onde: `app.py`
(`_assignment_warnings`, `api_add_turno`), `escala.html` (`avisoToast`).

### Horas ao vivo nas sugestões
Cada chip de sugestão (painel "Construir o dia") mostra agora as horas da semana
da pessoa, com cor: neutro com margem, **âmbar** perto do contrato, **vermelho**
quando já passou. O supervisor vê quem está sobrecarregado antes de adicionar
mais um turno (par natural do aviso acima). Usa o `hoursCache` já existente.
Onde: `escala.html` (chip `lc-suggest-chip` + `.lc-chip-hrs`).

## 🎨 Correções de design

| Área | Problema | Correção |
|------|----------|----------|
| **Painel · Cobertura** | O destaque "**35** lugares por preencher" contava **células** loja×dia (5×7=35), mas a matriz mostra `0/141` lugares. "Lugares" lê-se como pessoas, por isso o número subestimava o trabalho real em 4×. | Novo `n_missing` = soma dos lugares em falta (141). O destaque, o badge do tab e o rodapé passam a mostrar os **lugares** reais; o rodapé acrescenta "· em 35 lojas-dia" para contexto. Reconcilia com o total da matriz. |
| **Navegação · telemóvel** | A `.nav` tinha `height: 52px` fixo; no media query de telemóvel só se repunha `min-height`. Com os links a passarem para uma 2.ª linha (`flex-wrap`), a barra não crescia e os links ficavam **cortados/sobrepostos** por baixo do cabeçalho castanho. | `height: auto` no media query `≤700px`. A barra passa a 80px e contém ambas as linhas (logo+utilizador em cima, links por baixo, sobre o fundo castanho). |

## Testes
- Suite passa de 76 → **82** testes. Novos: CSV (conteúdo/BOM, gate de supervisor,
  404), cobertura conta lugares e não células, e copiar-da-semana-passada (oferta +
  não-sobrescreve). Todos verdes, incluindo via `pytest` simples.

---

# Motor de sugestões + Tab "Semana" (Junho 2026, sessão 3)

## 🧠 Motor de sugestões

- **Descanso semanal (escalonado, por dias seguidos).** Substituiu o `-150` fixo
  a partir do 5.º dia por um modelo que mede a sequência de dias consecutivos que
  o turno de hoje prolongaria: 4 dias `-80`, 5 `-250`, 6 `-600`, e penaliza ainda
  `-150` quando só sobraria 1 folga. Quem já trabalha 6 dias **nunca** é sugerido
  para o 7.º (folga legal) — o supervisor pode na mesma colocar à mão.
- **Equidade da bolsa de turnos.** Os contratados já eram equilibrados contra o
  contrato; os colaboradores a turnos não tinham contra o quê equilibrar. Agora
  equilibram-se contra a média de horas da própria bolsa (±150), empurrando as
  sugestões para quem tem menos horas até agora.
- Onde: `app.py` (`api_empties`: `_fatigue_adj`, fairness da bolsa). Testes novos
  para o corte ao 7.º dia e o despromover de sequências longas.

## 📊 Tab "Semana" (cobertura) — centro de comando

A matriz lojas×dias passou de grelha estática a painel acionável:

- **Risco de responsável por célula.** Marca `▲` numa célula preenchida onde
  ninguém presente pode abrir/fechar sozinho (mesma definição que o motor usa),
  com contagem no rodapé. Evita descobrir o problema só na página de Conflitos.
- **Clicar numa célula → popover** (funciona em telemóvel, ao contrário do antigo
  `title`): quem está escalado, com horário e etiquetas *abre*/*fecha*, lacunas,
  avisos de responsável, e um link **"Construir este dia →"**.
- **Faixa "Risco de descanso"** por baixo da matriz: quem está com 6–7 dias na
  semana (liga ao modelo de fadiga acima); 7 dias a vermelho (sem folga).
- Onde: `app.py` (`week_coverage` enriquecido: `who_list`, `open/close_risk`,
  `rest_risk`, `n_risk_cells`), `supervisor_dashboard.html` (células, popover,
  faixa, CSS+JS). Teste novo cobre risco de descanso + responsável.

Suite: **82 → 87** testes, todos verdes.

---

# Tab "Respostas" — inteligência de disponibilidade (Junho 2026, sessão 4)

A tab mostrava só *quem* submeteu. Agora mostra *o que* a equipa disse e onde a
semana vai ter falta de gente — o espelho, do lado da entrada, da tab Semana.

- **Disponibilidade vs necessidade, por dia.** Para a semana a construir, cada
  dia mostra quantas pessoas se disponibilizaram (disp+pref) contra os lugares
  que o dia precisa (soma dos alvos), com barra e cor: verde (chega), âmbar
  (apertado, ≥80%), vermelho (falta). Um aviso no topo conta os dias a vermelho.
  Vê-se a falta de pessoal **antes** de montar a escala.
- **Detalhe por dia (popover).** Clicar num dia mostra quem está disponível (com
  etiqueta *prefere*) e o resumo Disp./Pref./Indisp./Descarga.
- **Procura por loja.** Quantas pessoas disponíveis preferem cada loja — lojas a 0
  ficam a vermelho (vão ser difíceis de preencher).
- Mantém a barra de progresso e as listas sem-resposta/responderam.
- Onde: `app.py` (`supervisor_dashboard` → `avail_overview`),
  `supervisor_dashboard.html` (painel, popover, CSS+JS). Teste novo.

Suite: **87 → 88** testes, todos verdes.

---

# Modo escuro + polish de design (Junho 2026, sessão 5)

Night mode a sério, controlado por um botão na barra (sol/lua), com memória e
respeito pela preferência do sistema operativo.

- **Toggle na nav** (sol/lua), em todas as páginas incluindo o login. Guarda a
  escolha em `localStorage`; por defeito segue `prefers-color-scheme`.
- **Sem flash**: um script no `<head>` aplica o tema antes do primeiro paint.
- **Token-driven**: o tema escuro re-aponta os tokens CSS (`--bg`, `--white`,
  `--text`, `--border`, cores de estado…). Cores de estado (verde/vermelho/âmbar/
  azul) clareiam o texto e escurecem o fundo dos chips para contraste.
- **Novo token `--brand`** (superfície escura da marca — nav, botões, avatares).
  Fica escuro nos dois temas, ao contrário de `--brown` que passa a creme claro
  no escuro para os títulos/labels continuarem legíveis. 47 superfícies migradas
  para `--brand` (substituição cirúrgica; modo claro **idêntico**).
- **Correções por página** das poucas superfícies com cores claras hardcoded
  (painel "hoje", grelha do construtor, cartões de ação da home, banners de
  disponibilidade/trocas, células "hoje"/feriado).
- Verificado ao vivo: login, painel (6 tabs), construtor, equipa, home, minha
  escala, disponibilidade, trocas — e o toggle ida-e-volta com persistência.
- Onde: `base.html` (tokens dark, `--brand`, toggle, script no-flash),
  + overrides por template. Teste novo.

Suite: **88 → 89** testes, todos verdes. Modo claro inalterado.

---

# Revisão do site (Julho 2026, sessão 6)

Passagem completa pelo site (supervisor + funcionário, claro + escuro) com
seis correções funcionais encontradas ao vivo:

- **"0/5 lojas abertas" aparecia como "/5"**: quando nenhuma semana cobre o
  dia de hoje, o early-return do painel "Esta semana" não devolvia as chaves
  `lojas_com_pessoal`/`now_people`/`drafting_is_different` que o template lê.
  Onde: `app.py` (`_build_supervisor_today_panel`).
- **A semana atual não podia ser criada a meio da semana**: a grelha
  "Semanas" começava na *próxima* segunda (`next_mondays`), por isso à
  quinta-feira a semana em curso nunca era oferecida — mesmo com o painel a
  dizer "cria uma nova semana para hoje". Novo helper
  `mondays_from_current_week()`; a grelha começa agora na semana em curso
  (com tag verde «Esta semana») e o estado vazio do Resumo ganhou um botão
  direto "Criar a semana de …". Onde: `helpers/time_helpers.py`, `app.py`
  (rotas `/supervisor` e `/api/upcoming-weeks`), `supervisor_dashboard.html`.
- **Hash desconhecido deixava o painel em branco**: `show(hash)` escondia
  todos os separadores quando o nome não existia (bookmark antigo → página
  vazia). Agora cai no Resumo. Onde: `supervisor_dashboard.html`.
- **Central de trocas mostrava pedidos de turnos já passados** (ofertas de
  maio ainda "abertas" em julho). O estado `expirado` existia na máquina de
  estados mas nunca era usado — agora um sweep lazy
  (`_expire_stale_central()`) fecha ofertas cujo dia já passou; correm ao
  ler `/trocas`, `/api/central` e ao candidatar. Publicar um turno já
  passado na central também é recusado. No histórico, pedidos abertos
  mostram «doação aberta»/«troca aberta» em vez de "doação para" sem nome.
  Onde: `app.py`, `trocas.html`.
- **Blocos custom apareciam no fim da grelha** (um "13:00-19:00" novo ficava
  depois do 17:00-23:00). `build_grid` ordena agora as linhas por hora de
  início; blocos sem hora ("Provas Praticas") mantêm a posição configurada.
  A ordem do PDF acompanha. Onde: `app.py` (`build_grid`).
- **Contador "N turnos" do cabeçalho de loja não atualizava** ao
  adicionar/remover/arrastar turnos (só ao recarregar). `updateSummary`
  também atualiza agora o badge e o contador do tab-strip mobile. Aproveitou-se
  para remover o `confirm()` da remoção de turnos — o toast "Anular" (que já
  existia) é o mecanismo de segurança, como documentado na auditoria (#20/21).
  Onde: `escala.html`.

Suite: **89 → 95** testes, todos verdes (novos em
`tests/test_website_improvements.py`).

---

# Passagem de design (Julho 2026, sessão 6b)

Pedido do dono: tirar os avisos «células sem responsável de abertura/fecho»
do separador Semana, e uma passagem geral de profissionalismo/prazer de uso.

**Removido (a pedido):**
- Triângulos ▲ nas células, contorno vermelho, rodapé «N células sem
  responsável de abertura/fecho» e avisos no popover da matriz de cobertura.
  O cálculo open/close-risk saiu do backend; as tags «abre/fecha» por pessoa
  no popover mantêm-se. Onde: `supervisor_dashboard.html`, `app.py`.

**Polimento global (`base.html`):**
- **Favicon** SVG da marca (quadrado castanho + ponto âmbar) + `theme-color`.
- **Scrollbars finas** com as cores do tema (as barras cinzentas do Windows
  destoavam dentro das grelhas) e sem setas.
- **Entrada suave da página** (fade+slide de 220 ms; respeita
  `prefers-reduced-motion`).

**Por página:**
- **Login:** CTA «Entrar» passa a laranja-marca com glow no hover — primeira
  impressão mais quente.
- **Construtor de escala:** coluna de HOJE com lavagem âmbar + pill «hoje»
  no cabeçalho (novo `today_label` nas rotas `/escala` e `/supervisor/escala`);
  hover de linha para seguir um turno pelas 7 colunas; chips com profundidade
  (sombra + hover); link «PDF ↗» do cabeçalho de loja estilizado como pill.
- **Painel do supervisor:** hover nos separadores (fundo suave arredondado).
- **Minha Escala:** ponto de loja com a cor da loja (mesma paleta dos accents
  do construtor — Carmelitas terracota, Gaia azul, etc.).
- **Disponibilidade:** pontos de cor nos botões Disponível/Preferência/
  Indisponível antes de selecionar (o controlo lê-se à primeira); em modo
  escuro o estado selecionado usa texto escuro sobre a cor clara (contraste).

Suite: 95 testes verdes (1 teste antigo atualizado — deixou de esperar o
rodapé de responsabilidade).

---

# Disponibilidades — workflow dos funcionários (Julho 2026, sessão 6c)

O formulário já era bom a preencher; esta passagem tornou o CICLO completo
(preencher → não perder → enviar completo → confiar no que foi enviado →
planear à frente) à prova de bala.

- **Rascunho automático (localStorage).** Tudo o que se marca/escreve é
  guardado no telemóvel à medida que muda. Fechar o separador ou tocar num
  link a meio já não perde nada: ao voltar, o formulário restaura-se com um
  banner «Recuperámos alterações que não chegaste a enviar (há X min)» e um
  botão Descartar. O rascunho morre quando: é submetido, é igual ao que está
  no servidor, é descartado, tem >7 dias, ou existe uma submissão mais
  recente (ex.: feita noutro aparelho — nunca ressuscita dados velhos).
- **Verificação de dias em branco ao enviar.** Dias em branco contam como
  «sem resposta» para o supervisor. Ao enviar com dias por marcar, um modal
  amigável lista-os e oferece: «Marcar os restantes como disponível e
  enviar», «Enviar assim mesmo», ou voltar a editar.
- **Recibo visual após submeter.** O ecrã de sucesso mostra os 7 dias como
  pills coloridas (verde/âmbar/vermelho/azul/—) com exatamente o que foi
  registado, mais um botão «Corrigir alguma coisa» que volta ao formulário
  da semana certa.
- **Responder à frente.** Quando existem semanas futuras já criadas, um
  seletor «Responder para» permite submeter disponibilidade para elas (ex.:
  férias daqui a duas semanas). A rota valida o código contra o conjunto
  permitido (ativa + futuras criadas); código inválido cai na ativa.
- **Confiança:** o banner «já submetida» mostra agora «atualizada há X»
  (com fix de timezone — SQLite devolve datetimes naive-UTC), o botão passa
  a «Atualizar disponibilidade» quando já existe submissão, e os três
  botões de estado têm tooltips a explicar a diferença
  Disponível/Preferência/Indisponível.

Verificado ao vivo (fluxo completo como funcionário): rascunho → restauro →
modal de incompletos → «marcar restantes e enviar» → recibo → reabrir e
editar → responder para a semana seguinte. Onde: `app.py` (rota
`/disponibilidade`), `disponibilidade.html`.

Suite: **95 → 97** testes, todos verdes.

---

# Tom neutro e profissional em todo o site (Julho 2026, sessão 6d)

Pedido do dono: eliminar o tom «conversa com o utilizador» — exclamações,
perguntas retóricas, primeira pessoa do plural e tratamento informal — e
manter tudo neutro e profissional.

Varredura completa (~95 strings em 10 templates + app.py):

- **Exclamações e frases coloquiais removidas:** «Boa folga até à próxima!»
  → «Sem mais turnos nesta semana.»; «Eu posso!» → «Candidatar-me ao turno»;
  «Tudo em dia» → «Sem pedidos/decisões pendentes»; «Igual à semana
  passada?» → «Disponibilidade recorrente»; «Quer doar ou trocar um turno?»
  → «Doar ou trocar um turno»; «Recuperámos alterações que não chegaste a
  enviar» → «Alterações não enviadas recuperadas».
- **Registo impessoal nas instruções:** imperativos informais («Marca»,
  «Escolhe», «Indica», «Revê», «Cria», «Toca», «Usa», «Cola», «Aponta»)
  passaram a infinitivo («Marcar…», «Escolher…», «Indicar…») ou a
  construções impessoais («Pode ser editada e reenviada», «É possível…»).
- **Possessivos informais neutralizados:** «o teu próximo turno» → «Próximo
  turno»; «A tua semana» → «Semana»; «os teus turnos» → «turnos»; «A tua
  sigla» → «Sigla»; «tens turno sobreposto» → «turno sobreposto»; «Estás
  sozinho neste turno» → «Sem colegas em simultâneo neste turno».
- **Mensagens de erro do backend no mesmo registo:** «Já tens um pedido…»
  → «Já existe um pedido…»; «Esse turno não é teu.» → «Esse turno pertence
  a outra pessoa.»; «Não te podes candidatar…» → «Não é possível
  candidatar-se…»; «Marca pelo menos um dia.» → «É necessário marcar pelo
  menos um dia.»; etc.
- **Mantido deliberadamente** (padrão profissional): a saudação horária da
  home («Bom dia, Nome.»), labels de navegação na primeira pessoa («Minha
  Escala», «Os meus turnos») e a cortesia dentro do modelo de mensagem
  WhatsApp que o supervisor copia (comunicação entre pessoas).
- Design: o botão de submissão da disponibilidade passou a laranja-marca,
  alinhado com o CTA do login.

Verificado ao vivo (login, home, trocas, disponibilidade, todas as páginas
a responder 200). Suite: 97 testes verdes (2 asserções atualizadas).

---

# Transição de tema animada (Julho 2026, sessão 6e)

O alternar claro/escuro deixou de ser uma troca instantânea de cores:

- **Revelação circular** — o tema novo expande-se num círculo a partir do
  botão sol/lua (View Transitions API, ~450 ms). O tema antigo fica como
  imagem estática por baixo, por isso nada pisca.
- **Fallback** — em browsers sem a API, um cross-fade de cores de 350 ms
  (classe temporária em `<html>`, removida logo a seguir, para não tornar
  os hovers lentos).
- **`prefers-reduced-motion`** respeitado: troca instantânea.
- O `theme-color` do browser móvel acompanha o tema, e cliques rápidos
  seguidos não baralham o estado (o alvo pendente é registado, porque a
  View Transition aplica o tema de forma assíncrona).
- Toque tátil no botão (escala ao premir).

Onde: `base.html` (toggleTheme/applyTheme + CSS `::view-transition-*` e
`.theme-fade`). Suite: 97 testes verdes.
