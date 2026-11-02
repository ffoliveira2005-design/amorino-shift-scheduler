# Acesso dos colaboradores

Procedimento implementado em 7 de Outubro de 2026. A sigla identifica uma pessoa;
não comprova a sua identidade e nunca permite, por si só, ativar uma conta.
Não são necessários email ou SMS integrados.

## Novo colaborador ou conta antiga por ativar

1. A supervisão confirma a identidade através do procedimento interno da
   empresa e confirma a sigla na lista aprovada. Responsável e meio de
   verificação: **pendentes de confirmação pela empresa**.
2. Entrar como supervisor. Em **Equipa**, criar o colaborador, se necessário.
   Uma conta nova fica sem acesso; conhecer a sigla e escolher um PIN no login
   não permite reclamar essa conta.
3. No cartão da pessoa, selecionar **Emitir convite** e confirmar que a
   identidade foi verificada. O convite aparece uma única vez. Entregar a
   sigla, o convite e o endereço HTTPS da página **Ativar ou recuperar acesso**
   (`/acesso`) em privado, presencialmente ou por canal privado já aprovado.
   Não colocar o convite num grupo, numa escala, num ticket ou num URL.
4. A pessoa abre essa página, introduz a sigla e o convite, define e confirma
   um PIN pessoal de 4 a 32 caracteres. Sequências, caracteres repetidos,
   valores triviais e a própria sigla são recusados.
5. Depois de ativado, confirmar que a pessoa consegue iniciar sessão e
   submeter disponibilidade. O cartão passa a **Acesso ativo** ao atualizar
   a página. O PIN pessoal não é mostrado à supervisão.

Os convites expiram **24 horas após a emissão**, em horário UTC no registo e
Lisboa na interface. Só podem ser utilizados uma vez e estão associados a
uma sigla. Emitir um novo convite invalida todos os anteriores. Não é possível
consultar novamente o segredo: se não foi entregue, foi perdido ou expirou,
emitir outro após verificar a identidade.

## PIN perdido ou suspeita de acesso indevido

1. Verificar a identidade antes de repor acesso. Não aceitar apenas a sigla
   ou uma mensagem anónima como prova.
2. Em **Equipa**, selecionar **Repor acesso** no cartão certo e confirmar.
   O PIN antigo deixa de funcionar imediatamente. A app invalida as sessões
   anteriores no pedido seguinte e revoga o token de calendário antigo.
   A conta não fica aberta a reclamação; exige o novo convite privado.
3. Entregar e utilizar o convite conforme o procedimento acima. Caso não
   seja entregue, manter a conta bloqueada e emitir outro convite depois.
4. Pedir à pessoa que remova a subscrição antiga do calendário e obtenha
   uma nova em **O meu calendário**. A reposição revoga o acesso ao feed, mas
   não consegue apagar cópias que o telemóvel já descarregou.
5. Se houver suspeita de uso indevido, encaminhar para o responsável de
   suporte e preservar os registos de auditoria; contacto **pendente**.

Não se deve editar `pin` para `''` diretamente na base de dados nem fornecer
um PIN partilhado. A operação através da app aplica a revogação de sessões,
calendário e convites em conjunto.

## Migração e supervisão da operação

As credenciais válidas existentes continuam a funcionar, incluindo valores
antigos. PINs legacy em texto simples são convertidos para PBKDF2 após um login
válido. As contas que já tinham PIN vazio continuam bloqueadas até receberem
um convite. A migração adiciona `access_version`, `enrollment_hash`,
`enrollment_expires_at` e `enrollment_kind` sem atribuir credenciais novas nem
emitir convites automaticamente.

Antes do piloto, a supervisão verifica todas as contas elegíveis e confirma
entrega/ativação individual. Pessoas fora do piloto com credenciais existentes
continuam a ter os direitos existentes: a empresa deve rever o roster e os
participantes antes da exposição pública.

As ações `enrollment_issued`, `pin_reset`, `enrollment_complete`,
`enrollment_fail` e `enrollment_rate_limited` registam ator, pessoa e momento,
sem PIN nem convite utilizável. A emissão/reposição e a conclusão são gravadas
na mesma transação que a alteração de acesso. Só se guarda o digest SHA-256
do convite aleatório de 256 bits; PINs continuam a usar PBKDF2 com salt.
Nenhum convite é enviado automaticamente pela aplicação.

## Verificação local

Executar `tests/test_enrollment.py` e os testes de segurança de
`tests/test_basics.py` apenas com `AMORINO_DB_PATH` definido para um ficheiro
descartável. Cobrem reclamação por sigla, expiração, reutilização, substituição,
sigla errada, autorização, CSRF, política do novo PIN, compatibilidade de
credenciais, limitação de tentativas e replay de cookies após reposição.
Não usar a base `amorino.db` da raiz para ensaios ou provisionamento fictício.
