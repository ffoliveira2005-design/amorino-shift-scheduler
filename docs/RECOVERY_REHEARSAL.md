# Registo do ensaio de recuperação

Executado em **7 de outubro de 2026, 17:15:05 de Lisboa** (16:15:05 UTC), em Windows com Python 3.14 e dados fictícios novos. Evidência estruturada: [recovery-rehearsal-result.json](recovery-rehearsal-result.json). O ensaio não abriu, copiou, importou ou migrou o `amorino.db` real da raiz.

| Verificação | Resultado observado |
|---|---|
| Dados isolados | 3 pessoas fictícias, duas lojas, 1 semana, 21 respostas de disponibilidade, 3 turnos, 1 descarga, 1 troca aprovada |
| Publicação e alteração | Publicação inicial explícita; troca aprovada registou a segunda versão; ambas sobreviveram ao restauro |
| Login e roster | Supervisor e funcionário autenticados; roster e proprietários dos turnos iguais ao snapshot |
| Aplicação | `/healthz` e 8 páginas protegidas verificadas após restauro |
| Exports | CSV com 3 pessoas e linha TOTAL; conteúdos PDF global e individual idênticos aos anteriores à cópia, descontando apenas timestamps/IDs técnicos do PDF |
| Horas planeadas | Totais CSV reconciliados; turno noturno presente; descarga 08:00–10:00 com crédito planeado de 4 h |
| Preservação | Base danificada, WAL, SHM e manifesto conservados; teste separado também cobre journal |
| Segurança do restauro | Convites e tokens de calendário revogados; versões de sessão incrementadas; nova subscrição/feed verificados após login |
| Transferência | Cópia e manifesto numa segunda pasta **local**, SHA-256/integridade/schema verificados; não é armazenamento fora do servidor |
| Duração de restauro + validação da aplicação | **2,856 segundos** |
| Duração total, incluindo geração/publicação/troca/cópia | **10,024 segundos** |
| Alteração posterior à cópia | 1 mudança fictícia de nome intencionalmente perdida; confirmada a versão anterior após restauro |

O snapshot iniciou às 16:15:02,241 UTC. A janela observada foi de poucos segundos e uma alteração fictícia; não demonstra o objetivo de perda aceitável em produção. Com o timer diário às 03:00 de Lisboa, a janela possível chega a **25 horas na transição de outono**, acrescida da duração do job. Falhas sem resolução tornam-na maior. A empresa precisa de aceitar a frequência ou pedir cópias mais frequentes.

Os testes de `tests/test_preflight.py` e `tests/test_recovery.py` verificam configurações inseguras, recusa de acesso à base da raiz, leituras sem migração, WAL, colisões/poda de backups, falha reportada, checksum/idade, guardas de restauro, preservação de sidecars, revogação de convites/calendário e o ensaio completo. A revisão final aprovou **45 testes operacionais em 15,35 segundos**, incluindo validação do destinatário e checksum exato da cópia externa. A execução final combinada de publicação/operação aprovou 60 testes; a suíte completa aprovou 430 antes destes últimos nove controlos e do teste da página de revisão. Todas as execuções usaram `AMORINO_DB_PATH` explícito com bases descartáveis, incluindo scratch `--basetemp` dentro do checkout. Entrega e evidência.

Para repetir, executar em novo processo:

```powershell
$env:AMORINO_DB_PATH = Join-Path $env:TEMP 'amorino-rehearsal-launch-scratch.db'
python scripts/recovery_rehearsal.py --report docs/recovery-rehearsal-result.json
```

O comando cria uma nova pasta temporária dedicada e não reutiliza dados anteriores. Opcionalmente `--work-dir` aceita apenas uma pasta vazia, sem redirecionamento. Conservar a evidência JSON; eliminar depois apenas a pasta descartável identificada no resultado, seguindo a política de retenção do ambiente. Nunca apontar o ensaio à base real.

Ainda **PENDENTE**: alojamento/contas e acesso aprovados, migração de produção, domínio/HTTPS público, restart/reboot do serviço no host, instalação dos timers, alerta recebido pelo responsável, cópia em destino independente e seu restauro isolado, reconciliação de resets posteriores ao snapshot, rotação de `AMORINO_SECRET_KEY` no restauro, contactos e aceitação da empresa. O resultado suporta preparação e ensaio isolado; não comprova produção nem backups fora do servidor completos. Procedimentos: [OPERATIONS.md](OPERATIONS.md).
