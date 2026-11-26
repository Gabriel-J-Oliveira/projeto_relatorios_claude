# Central de Permissoes

Documento tecnico de referencia para a Central. O estado efetivo de producao deve ser confirmado no ambiente; o default no codigo e `PERMISSOES_CENTRAL_ENABLED=False`.

## Implementado

- Catalogo e avaliador em `relatorios/services/permissoes_service.py`, com overrides HERDAR/PERMITIR/NEGAR e historico de alteracoes.
- Central visual de usuarios e permissoes, incluindo replicacao e preview, em `usuarios_permissoes_service.py` e templates correspondentes.
- Full protegido via `ERP_FULL_ACCESS_USERS`; grupos e flags Django nao equivalem a Full central.
- Shadow mode compara legado e central sem mudar a decisao produtiva quando a flag esta desligada; logs usam `[PERMISSOES_SHADOW]`.
- Pontos preparados por lote: manutencao, ajuda, clientes e valor KM; visibilidade; edicao e envio; acesso financeiro; aprovacao, rejeicao e devolucao.
- Perfil global somente leitura e backstop HTTP para metodos mutaveis quando a Central esta ativa, com excecoes especificas para acoes pessoais. Full protegido prevalece.
- Workflow e autorizacao sao distintos: permissoes nao substituem validacoes de estado.

## Pendencias e riscos

- Confirmar no codigo e nos testes atuais o escopo de cada endpoint antes de ativar a flag. Areas legadas ainda podem ter checks independentes.
- Auditar em especial alteracao de valores, itens e rateios financeiros, PDF interno, reabertura, tecnicos, adiantamentos e dashboard; nao presumir que estao migrados integralmente.
- Antes do cutover: configurar e revisar overrides, executar preview, observar divergencias shadow, validar usuarios e casos de objeto/workflow e preparar rollback.
- O acesso de identidade corporativa pode depender de regras legadas; validar usuarios sem vinculo legado antes do cutover.
- A interface nao substitui validacao no backend. Revisar operacoes mutaveis e excecoes do perfil somente leitura, inclusive endpoints antigos.
- A normalizacao de Full Access admite limitacoes em ambientes com multiplos dominios; nao presumir isolamento entre dominios.

## Arquivos e operacao

- `relatorios/services/autorizacao_service.py`: regras legadas e integracao flag-aware.
- `relatorios/services/permissoes_service.py`: catalogo, avaliacao central e shadow.
- `relatorios/services/usuarios_permissoes_service.py`: operacoes e replicacao.
- `relatorios/middleware.py`: protecao transversal de somente leitura.
- `python manage.py permissoes_preview_cutover`: preview; conferir opcoes com `--help`.

Nao ativar a flag nem conceder permissoes como efeito de uma atualizacao de documentacao.
