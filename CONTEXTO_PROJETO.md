# CONTEXTO DO PROJETO

Documento criado para servir como memoria tecnica portatil do projeto em um novo chat/agente.

Legenda usada neste documento:

- `[CONFIRMADO NO CODIGO]`: confirmado por leitura direta do repositorio.
- `[CONTEXTO HISTORICO]`: conhecido pelo historico de trabalho/conversa, mas nao necessariamente verificavel por uma unica leitura de codigo.
- `[PENDENTE / NAO CONFIRMADO]`: ponto que precisa ser validado antes de assumir como verdade.

## Resumo Executivo

[CONFIRMADO NO CODIGO] Este repositorio e um sistema Django chamado `app_relatorios`, com app principal `relatorios`, voltado ao cadastro, conferencia, aprovacao, consulta e emissao de PDFs de relatorios de viagem/prestacao de contas. O dominio central e `RelatorioTecnico`, que agrega clientes, tecnicos, cidades atendidas, despesas, trechos de KM, anexos, historico e snapshot financeiro.

[CONFIRMADO NO CODIGO] Stack principal:

| Item | Tecnologia |
| --- | --- |
| Backend | Django 6.0.4 |
| Python deps | `django-auth-ldap`, `django-crispy-forms`, `django-filter`, `python-decouple`, `requests`, `bleach`, `markdown`, `weasyprint`, `gunicorn`, `psycopg` |
| Frontend | Templates Django, Bootstrap, HTMX, JavaScript proprio |
| PDF | WeasyPrint 68.1 |
| Auth | Django auth + LDAP/Active Directory opcional |
| Banco | PostgreSQL em producao via env; SQLite local/dev existe no repositorio |
| Storage | `ANEXOS_ROOT` separado de `MEDIA_ROOT`; imagens da ajuda em `HELP_IMAGES_ROOT` |

[CONFIRMADO NO CODIGO] O fluxo principal do relatorio usa os status:

```text
rascunho
  -> conferencia_pendente
  -> ajuste_pendente
  -> conferencia_pendente
  -> aprovado ou rejeitado
```

[CONFIRMADO NO CODIGO] O sistema separa duas visoes de KM:

- reembolso ao tecnico: sempre baseado em `VALOR_KM_CONTROLSUL`, default `1.35`;
- cobranca ao cliente: baseada no `Cliente.valor_km` ou regra central em `km_financeiro_service.valor_km_cliente_contratual`.

[CONFIRMADO NO CODIGO] A V2.0 esta presente no Git como ultimo commit `3cd7571 Release oficial v2.0` na branch `main`. A tag encontrada no repositorio local foi apenas `v1.0-checkpoint`; nao ha tag `v2.0` confirmada localmente.

[CONTEXTO HISTORICO] Antes do commit oficial da V2.0, havia bloqueio local porque o ambiente Windows nao executava os testes Django por falta do modulo `ldap`. Depois, o usuario informou que as validacoes necessarias passaram no servidor Linux e solicitou o commit da release oficial.

[CONFIRMADO NO CODIGO] Funcionalidades V2.0 relevantes:

- cliente unico propagado automaticamente para despesas/trechos e rateios;
- tecnico unico aplicado automaticamente em participantes de despesa;
- botao/acao "Ida e volta" em trecho KM;
- anexos com estado local, `DataTransfer`, manifesto `upload_expected_manifest` e monitor de utilizacao;
- multiplos comprovantes por despesa via `AnexoRelatorio`, mantendo `ItemDespesa.comprovante` por compatibilidade;
- hospedagem com periodo e diarias;
- tecnicos participantes por despesa;
- multiplas cidades de atendimento;
- tour contextual para novidades.

Pendencias criticas para novo agente:

- [PENDENTE / NAO CONFIRMADO] Se ainda nao foi criada tag Git `v2.0`, decidir se ela deve existir.
- [PENDENTE / NAO CONFIRMADO] Rodar novamente no servidor Linux `python manage.py check` e `python manage.py test relatorios` antes de qualquer publicacao futura.
- [PENDENTE / NAO CONFIRMADO] Validar manualmente upload de anexos em producao apos `collectstatic`, pois esta area ja teve regressao critica por cache/JS/submissao direta.

---

## 1. Visao Geral

[CONFIRMADO NO CODIGO] O projeto chama-se `app_relatorios` e possui um app Django principal chamado `relatorios`.

Finalidade:

- controlar relatorios de viagem/prestacao de contas;
- permitir cadastro por tecnicos/usuarios autorizados;
- permitir conferencia financeira;
- aplicar politicas de valor;
- calcular despesas, reembolso tecnico e cobranca ao cliente;
- gerar historico/auditoria;
- congelar snapshot financeiro em finalizacao;
- gerar PDFs internos e de cliente;
- manter uma central de ajuda editavel e area de manutencao administrativa.

Usuarios/perfis principais:

| Perfil | Papel |
| --- | --- |
| Tecnico | Cria/preenche relatorios, despesas, KM e anexos |
| Financeiro | Confere, ajusta, aprova, rejeita, solicita ajuste |
| Gestor/Admin ERP/Domain Admin | Acesso administrativo conforme regras |
| EXTRA_ADMIN_USERS | Excecao administrativa por variavel de ambiente |
| Superuser | Acesso total Django |

[CONFIRMADO NO CODIGO] Permissoes sao centralizadas principalmente em [`relatorios/services/autorizacao_service.py`](relatorios/services/autorizacao_service.py).

[CONFIRMADO NO CODIGO] O settings default de `manage.py` aponta para `app_relatorios.settings.prod`. O ambiente de producao usa PostgreSQL configurado por env em [`app_relatorios/settings/prod.py`](app_relatorios/settings/prod.py). O repositorio contem `db.sqlite3`, mas isso nao deve ser assumido como banco de producao.

---

## 2. Arquitetura

Estrutura principal:

```text
projeto_relatorios_claude/
├── app_relatorios/             # settings, urls raiz, wsgi/asgi
├── relatorios/                 # app principal de negocio
│   ├── management/commands/    # comandos operacionais/importacao/testes
│   ├── migrations/             # historico de schema
│   ├── services/               # regras de negocio e integracoes
│   ├── static/                 # alguns assets do app
│   ├── templates/              # templates do app
│   ├── models.py               # models principais
│   ├── forms.py                # forms/formsets do relatorio
│   ├── urls.py                 # rotas do app
│   └── views.py                # views HTML/AJAX/PDF
├── static/js/                  # JavaScript global e de relatorio
├── templates/                  # base.html e templates globais
├── media/                      # assets locais internos
├── staticfiles/                # saida de collectstatic
├── deploy/                     # artefatos/scripts de deploy
├── docs/                       # documentacao auxiliar
├── logs/                       # logs locais
└── requirements.txt
```

Responsabilidades:

| Camada | Arquivos | Responsabilidade |
| --- | --- | --- |
| Models | [`relatorios/models.py`](relatorios/models.py) | Estado persistido, escolhas, propriedades financeiras |
| Forms/Formsets | [`relatorios/forms.py`](relatorios/forms.py) | Validacao de entrada, formsets dinamicos |
| Views | [`relatorios/views.py`](relatorios/views.py) | Fluxos HTTP, renderizacao, AJAX, upload/remocao |
| Workflow | [`relatorios/services/workflow_service.py`](relatorios/services/workflow_service.py) | Envio, ajuste, aprovacao, rejeicao |
| Autorizacao | [`relatorios/services/autorizacao_service.py`](relatorios/services/autorizacao_service.py) | Permissoes por perfil/grupo/status |
| Rateios | [`relatorios/services/rateio_service.py`](relatorios/services/rateio_service.py) | Consistencia de rateios por cliente |
| KM | [`relatorios/services/km_financeiro_service.py`](relatorios/services/km_financeiro_service.py), [`relatorios/services/trecho_km_calculo_service.py`](relatorios/services/trecho_km_calculo_service.py) | Reembolso tecnico, cobranca cliente, calculo/rateio de trechos |
| Politicas | [`relatorios/services/politica_valor_service.py`](relatorios/services/politica_valor_service.py), [`relatorios/services/politica_aprovacao_service.py`](relatorios/services/politica_aprovacao_service.py) | Resolver politica e valor aprovado inicial |
| Snapshot | [`relatorios/services/snapshot_service.py`](relatorios/services/snapshot_service.py) | Congelar dados financeiros finalizados |
| PDFs | [`relatorios/services/pdf_cliente_service.py`](relatorios/services/pdf_cliente_service.py), [`relatorios/services/pdf_interno_service.py`](relatorios/services/pdf_interno_service.py) | Gerar PDFs com WeasyPrint |
| Anexos | [`static/js/relatorio_upload_monitor.js`](static/js/relatorio_upload_monitor.js), [`relatorios/storage.py`](relatorios/storage.py), `AnexoRelatorio` | Upload, manifesto, storage, remocao |
| Help Center | [`relatorios/services/help_center_service.py`](relatorios/services/help_center_service.py) | Central de ajuda, artigos, imagens |
| Manutencao | [`relatorios/services/manutencao_service.py`](relatorios/services/manutencao_service.py) | Consulta segura de logs/e-mails |

---

## 3. Rotas Principais

[CONFIRMADO NO CODIGO] Rotas do app em [`relatorios/urls.py`](relatorios/urls.py):

| Rota | Nome/funcao |
| --- | --- |
| `/dashboard/` | dashboard |
| `/relatorios/` | listagem |
| `/relatorios/novo/` | criar relatorio |
| `/relatorios/autosave/` | autosave de rascunho |
| `/relatorios/<pk>/` | detalhe/conferencia |
| `/relatorios/<pk>/consulta/` | consulta readonly/final |
| `/relatorios/<pk>/editar/` | editar |
| `/relatorios/<pk>/duplicar/` | duplicar |
| `/relatorios/<pk>/status/<status>/` | transicao/status |
| `/relatorios/<pk>/reabrir/` | reabrir aprovado |
| `/relatorios/<pk>/pdf-reembolso/` | PDF de reembolso |
| `/relatorios/<pk>/pdf/cliente/<cliente_id>/` | PDF cliente |
| `/relatorios/<pk>/pdf/clientes/` | PDFs por clientes |
| `/relatorios/<pk>/pdf-interno/` | PDF interno |
| `/relatorios/legados/` | relatorios legados |
| `/relatorios/legados/<pk>/` | detalhe legado |
| `/manutencao/` | manutencao admin |
| `/ajuda/` e subrotas | central de ajuda |

---

## 4. Models Principais

### Choices/Enums

[CONFIRMADO NO CODIGO] Em [`relatorios/models.py`](relatorios/models.py):

| Enum | Valores principais |
| --- | --- |
| `StatusRelatorio` | `rascunho`, `conferencia_pendente`, `ajuste_pendente`, `aprovado`, `rejeitado` |
| `TipoRelatorio` | `administrativo`, `institucional`, `operacional`, `treinamento` |
| `TipoReembolso` | `reembolsavel`, `nao_reembolsavel` |
| `EmpresaGrupo` | `blazius_e_lorenzetti`, `controlsul`, `fiscalmax` |
| `StatusFinanceiroItem` | `aprovado`, `rejeitado` |
| `StatusRateio` | `auto`, `adjusted`, `approved` |
| `TipoEventoHistorico` | criado, enviado, ajuste, reenviado, aprovado, rejeitado, reaberto, item rejeitado, valor alterado, email |
| `TipoDespesa` | alimentacao, hospedagem, combustivel, pedagio, passagem, transporte, estacionamento, material, comunicacao, outros |
| `QuemPagou` | tecnico, empresa |
| `TipoDocumentoComprovante` | nota_fiscal, recibo |

### `Tecnico`

[CONFIRMADO NO CODIGO] Model em [`relatorios/models.py`](relatorios/models.py).

Finalidade: colaborador/tecnico usado em relatorios, responsavel, equipe adicional, tecnico reembolsado e participantes de despesas.

Relacionamentos relevantes:

- `RelatorioTecnico.tecnico_responsavel -> Tecnico` (`PROTECT`);
- `RelatorioTecnico.tecnicos_adicionais` via `RelatorioTecnicoEquipe`;
- `RelatorioTecnico.tecnico_reembolso -> Tecnico`;
- `DespesaTecnico.tecnico -> Tecnico`;
- legados podem vincular tecnico via `RelatorioLegado.tecnico_vinculado`.

### `Cliente`

[CONFIRMADO NO CODIGO] Model em [`relatorios/models.py`](relatorios/models.py).

Finalidade: cliente externo ou empresa interna do grupo, usado para vinculo do relatorio, despesas, trechos KM e rateios.

Campos importantes confirmados por uso:

- `nome`;
- `razao_social`;
- `nome_fantasia`;
- `cnpj_cpf`;
- `ativo`;
- `valor_km`;
- cidade/UF e normalizacao usadas em consultas e PDFs.

Relacionamentos importantes:

- `RelatorioTecnico.cliente -> Cliente` legado/principal;
- `RelatorioCliente.cliente`;
- `DespesaCliente.cliente`;
- `DespesaRateio.cliente`;
- `TrechoKMCliente.cliente`;
- `TrechoRateioKM.cliente`.

[CONFIRMADO NO CODIGO] Valor de KM contratual deve ser obtido por service central, nao acessado de forma ad hoc quando houver regra especial: `km_financeiro_service.valor_km_cliente_contratual`.

### `PoliticaValor`

[CONFIRMADO NO CODIGO] Model de politicas em [`relatorios/models.py`](relatorios/models.py), usado por [`relatorios/services/politica_valor_service.py`](relatorios/services/politica_valor_service.py).

Finalidade: armazenar politicas vigentes por chave/tipo, incluindo valor de refeicao/hospedagem e valor KM ControlSul.

Uso importante:

- `PoliticaValor.vigente_por_chave(chave, data)`;
- `VALOR_KM_CONTROLSUL` pode vir de politica vigente ou fallback `1.35`;
- politicas de alimentacao/hospedagem sao resolvidas por tipo, localidade/cidade, data e descricao.

### `RelatorioTecnico`

[CONFIRMADO NO CODIGO] Model central em [`relatorios/models.py`](relatorios/models.py).

Campos principais:

| Campo | Tipo/uso |
| --- | --- |
| `numero` | numero oficial unico, gerado no envio |
| `status` | `StatusRelatorio` |
| `cliente` | FK `Cliente`, legado/principal |
| `tecnico_responsavel` | FK `Tecnico` |
| `tecnicos_adicionais` | M2M via `RelatorioTecnicoEquipe` |
| `tecnico_reembolso` | FK `Tecnico`, quem recebe reembolso |
| `municipio_atendimento` | FK `Municipio`, opcional |
| `cidade_atendimento`, `uf_atendimento`, `tipo_localidade` | campos historicos/compatibilidade |
| `data_inicio`, `data_fim` | periodo da viagem |
| `motivo` | descricao do servico |
| `tipo_relatorio` | area de gasto: administrativo/institucional/operacional/treinamento |
| `tipo_reembolso` | reembolsavel/nao reembolsavel |
| `empresa_grupo` | empresa interna responsavel pelo custo |
| `valor_adiantamento` | adiantamento recebido |
| `km_excedente_interno` | deslocamento interno extra |
| `observacao_km_excedente` | observacao do KM interno |
| `aprovado_em`, `aprovado_por` | auditoria de aprovacao |
| `criado_por`, `criado_em`, `atualizado_em` | auditoria de criacao |

Relacionamentos:

- `despesas`: `ItemDespesa`;
- `trechos`: `TrechoKm`;
- `clientes_vinculados`: `RelatorioCliente`;
- `equipe`: `RelatorioTecnicoEquipe`;
- `historicos`: `HistoricoRelatorio`;
- `snapshot_financeiro`: `RelatorioSnapshotFinanceiro`;
- `cidades_atendimento`: `CidadeAtendimento`;
- `anexos`: `AnexoRelatorio`.

### `CidadeAtendimento`

[CONFIRMADO NO CODIGO] Implementa multiplas cidades por relatorio.

Campos:

- `relatorio`;
- `municipio`;
- `cidade`;
- `uf`;
- `tipo_localidade`;
- `endereco`;
- `ordem`;
- `observacao`.

[CONFIRMADO NO CODIGO] O formset de cidades usa `extra=0` em `CidadeAtendimentoFormSet`, para evitar linha vazia indevida.

### `RelatorioTecnicoEquipe`

[CONFIRMADO NO CODIGO] Tabela intermediaria entre relatorio e tecnico adicional.

Campos:

- `relatorio`;
- `tecnico`;
- `papel`;
- `ordem`.

### `RelatorioCliente`

[CONFIRMADO NO CODIGO] Relaciona relatorio a clientes participantes.

Campos principais:

- `relatorio`;
- `cliente`;
- `ordem`;
- `motivo_viagem`.

Uso:

- fonte de clientes aplicaveis do relatorio;
- base para auto cliente e rateios;
- nao confundir com `RelatorioTecnico.cliente`, que existe por compatibilidade/principalidade.

### `ItemDespesa`

[CONFIRMADO NO CODIGO] Despesa do relatorio.

Campos principais:

| Campo | Uso |
| --- | --- |
| `relatorio` | FK |
| `ordem` | ordenacao |
| `data` | data da despesa |
| `tipo` | `TipoDespesa` |
| `descricao` | descricao/fornecedor |
| `valor` | valor solicitado |
| `valor_aprovado` | valor aprovado pelo financeiro ou politica |
| `status_financeiro` | aprovado/rejeitado |
| `rejeitado` / `motivo_rejeicao` | rejeicao financeira |
| `quem_pagou` | tecnico/empresa |
| `comprovante` | campo antigo de arquivo, mantido por compatibilidade |
| `data_inicio_hospedagem`, `data_fim_hospedagem` | periodo de hospedagem |
| `tipo_documento_comprovante`, `numero_documento_comprovante` | metadados fiscais |
| `observacoes` | observacao |

Propriedades importantes:

- `valor_final`: se rejeitado, `0`; senao `valor_aprovado` quando nao nulo, caso contrario `valor`.
- `valor_ajustado`: indica ajuste quando `valor_aprovado` difere de `valor`.
- `valor_politica`: resolve politica e calcula limite efetivo.
- `quantidade_tecnicos_participantes`: conta `tecnicos_vinculados`, minimo `1`.
- `quantidade_diarias_hospedagem`: usa service de periodo para hospedagem.
- `politica_aplicada_automaticamente`: detecta quando valor aprovado == limite e valor solicitado > limite.
- `politica_alterada_manualmente`: detecta alteracao financeira posterior quando possivel.

Regra critica:

```text
valor_aprovado = NULL
```

[CONFIRMADO NO CODIGO] Em `ItemDespesa.valor_final`, `NULL` significa aprovacao integral do valor solicitado, desde que o item nao esteja rejeitado.

### `DespesaCliente`

[CONFIRMADO NO CODIGO] Vincula uma despesa a clientes participantes.

Campos:

- `despesa`;
- `cliente`;

Restricao:

- `unique_together = ("despesa", "cliente")`.

### `DespesaTecnico`

[CONFIRMADO NO CODIGO] Vincula uma despesa a tecnicos participantes.

Campos:

- `despesa`;
- `tecnico`;

Restricao:

- `unique_together = ("despesa", "tecnico")`.

Uso:

- quantidade de participantes da despesa;
- multiplicador da politica por tecnico;
- base para auto tecnico quando relatorio possui exatamente um tecnico aplicavel.

### `DespesaRateio`

[CONFIRMADO NO CODIGO] Rateio financeiro de despesa por cliente.

Campos:

- `despesa`;
- `cliente`;
- `valor_original`;
- `valor_final`;
- `percentual`;
- `status`;
- `alterado_por`;
- `motivo_ajuste`;
- timestamps.

Restricao:

- `unique_together = ("despesa", "cliente")`.

### `TrechoKm`

[CONFIRMADO NO CODIGO] Trecho de deslocamento/KM.

Campos principais:

| Campo | Uso |
| --- | --- |
| `relatorio` | FK |
| `ordem` | ordenacao |
| `data` | data do deslocamento |
| `origem`, `destino` | pontos do trecho |
| `origem_endereco_completo`, `destino_endereco_completo` | enderecos |
| lat/lon origem/destino | mapa/rota |
| `km` | distancia |
| `km_calculado_api`, `km_informado`, `diferenca_km_percentual` | validacao de rota |
| `valor_km` | valor/km de cobranca do cliente |
| `valor_km_aprovado` | ajuste financeiro de valor/km quando aplicavel |
| `comprovante` | campo antigo/compatibilidade |
| `status_financeiro`, `rejeitado` | conferencia |
| `valor_calculado` | valor persistido/calculado |
| `observacao` | observacao |

Propriedades importantes:

- `valor_km_final`: `valor_km_aprovado` se houver, senao `valor_km`;
- `valor_final`: `km * valor_km_final`, zerado se rejeitado;
- `valor_km_control_sul`: usa `valor_km_control_sul()`;
- `valor_reembolso_tecnico`: `km * valor_km_control_sul`, zerado se rejeitado;
- `valor_reembolso_tecnico_solicitado`: `km * valor_km_control_sul`;
- `valor_calculado_clientes` / `valor_final_clientes`: cobranca por clientes/rateios.

### `TrechoKMCliente`

[CONFIRMADO NO CODIGO] Vincula trecho KM a cliente.

Campos:

- `trecho`;
- `cliente`;

Restricao:

- `unique_together = ("trecho", "cliente")`.

### `TrechoRateioKM`

[CONFIRMADO NO CODIGO] Rateio de KM por cliente.

Campos:

- `trecho`;
- `cliente`;
- `km_original`;
- `km_final`;
- `valor_rateado`;
- `km_cliente`;
- `valor_km`;
- `valor_calculado`;
- `valor_final`;
- `status`;
- `alterado_por`;
- `motivo_ajuste`;
- timestamps.

### `AnexoRelatorio`

[CONFIRMADO NO CODIGO] Model de anexos adicionais.

Campos:

- `relatorio`;
- `despesa` opcional;
- `trecho` opcional;
- `arquivo`;
- `nome_original`;
- `tipo_mime`;
- `tamanho_bytes`;
- `enviado_por`;
- `criado_em`;
- `observacao`;
- `tipo_documento`;
- `numero_documento`.

Regras:

- anexo pode estar vinculado a despesa ou trecho, nao ambos;
- valida se despesa/trecho pertence ao relatorio;
- chama `validar_anexo_upload`;
- para Nota Fiscal exige numero do documento;
- `registrar_comprovante()` existe para compatibilidade/registro a partir de `FileField` antigo.

### `HistoricoRelatorio`

[CONFIRMADO NO CODIGO] Auditoria/timeline do relatorio.

Campos:

- `relatorio`;
- `usuario`;
- `acao`;
- `tipo_evento`;
- `descricao`;
- `created_at`;
- `data_hora`;
- `dados_json`.

### `RelatorioSnapshotFinanceiro`

[CONFIRMADO NO CODIGO] Snapshot financeiro OneToOne com `RelatorioTecnico`.

Campos:

- `relatorio`;
- `schema_version`;
- `numero`;
- `status`;
- `total_solicitado`;
- `total_aprovado`;
- `diferenca_removida`;
- `payload`;
- `checksum`;
- `finalizado_em`;
- `finalizado_por`;
- `criado_em`.

Protecoes:

- `save()` bloqueia alteracao de snapshot existente salvo se `_permitir_atualizacao_snapshot`;
- `delete()` sempre levanta `ValidationError`.

### Legados

[CONFIRMADO NO CODIGO] Existem models de relatorio legado/historico frio:

- `RelatorioLegado`;
- `DespesaLegada`;
- `KmLegado`.

Eles armazenam dados importados da planilha antiga sem entrar no workflow moderno.

---

## 5. Fluxo do Relatorio

[CONFIRMADO NO CODIGO] O fluxo operacional fica em [`relatorios/services/workflow_service.py`](relatorios/services/workflow_service.py).

Estados:

```text
RASCUNHO
  |
  | enviar_para_conferencia()
  v
CONFERENCIA
  |             |
  | aprovar     | solicitar_ajuste()
  v             v
APROVADO       AJUSTE
                |
                | reenviar
                v
              CONFERENCIA

CONFERENCIA -> REJEITADO tambem e suportado
APROVADO -> CONFERENCIA via reabertura administrativa especial
```

Principais funcoes:

| Funcao | Responsabilidade |
| --- | --- |
| `preparar_rascunho_para_salvar` | manter status de rascunho/edicao sem transicao operacional |
| `enviar_para_conferencia` | valida permissao, rateios, validacoes operacionais, gera numero oficial, muda status, historico, email |
| `solicitar_ajuste` | financeiro solicita correcao e muda para ajuste |
| `aprovar_relatorio` | salva valores aprovados, valida, registra adiantamento, cria snapshot, historico, email |
| `rejeitar_relatorio` | rejeita definitivamente, cria snapshot, historico, email |

Validacoes na transicao:

- permissao de envio;
- transicao de status permitida;
- rateios consistentes;
- validacoes operacionais para envio;
- tecnico de reembolso quando ha pagamento ao tecnico;
- empresa do grupo quando nao reembolsavel;
- aprovacao financeira com total aprovado e itens ativos.

Relatorio finalizado:

[CONFIRMADO NO CODIGO] `relatorio_bloqueado()` considera finais: `APROVADO` e `REJEITADO`.

---

## 6. Reabertura Administrativa

[CONFIRMADO NO CODIGO] Implementada em [`relatorios/services/reabertura_relatorio_service.py`](relatorios/services/reabertura_relatorio_service.py), rota `/relatorios/<pk>/reabrir/`.

Regra de acesso:

- helper `usuario_pode_reabrir_relatorio(user)` em `autorizacao_service.py`;
- usuario permitido normalizado: `gabriel.oliveira`;
- a normalizacao remove dominio antes da barra e parte apos `@`, entao `control.local\gabriel.oliveira` deve casar.

Comportamento:

- aceita somente relatorio `APROVADO`;
- usa `transaction.atomic`;
- faz lock com `select_for_update`;
- altera apenas `status` para `CONFERENCIA`;
- preserva `aprovado_em` e `aprovado_por`;
- registra `TipoEventoHistorico.REABERTO`;
- nao recalcula snapshot nem totais.

---

## 7. Financeiro

### Valores solicitados, aprovados e removidos

[CONFIRMADO NO CODIGO] Despesas:

- `ItemDespesa.valor` = valor solicitado pelo tecnico;
- `ItemDespesa.valor_aprovado` = valor aprovado explicito, quando reduzido/alterado;
- `NULL` em `valor_aprovado` = aprovacao integral do solicitado;
- rejeicao zera `valor_final`.

[CONFIRMADO NO CODIGO] Trechos KM:

- reembolso tecnico usa `valor_km_control_sul`;
- cobranca cliente usa rateios/cliente;
- rejeicao zera valor final do item.

[CONFIRMADO NO CODIGO] `_salvar_valores_aprovados()` em workflow salva edicoes financeiras e registra `TipoEventoHistorico.VALOR_ALTERADO` quando aplicavel.

### Reembolso tecnico vs cobranca cliente

Regra critica:

```text
Reembolso ao tecnico = KM aprovado * R$ 1,35 (ou politica VALOR_KM_CONTROLSUL vigente)
Cobranca ao cliente = KM rateado * Cliente.valor_km/valor contratual resolvido
```

[CONFIRMADO NO CODIGO] Em [`relatorios/services/km_financeiro_service.py`](relatorios/services/km_financeiro_service.py):

- `VALOR_KM_REEMBOLSO_TECNICO = Decimal("1.35")`;
- `valor_km_reembolso_tecnico()` tenta `valor_km_control_sul()` e fallback env/default;
- `valor_km_cliente_contratual(cliente)` centraliza valor de cobranca cliente;
- `calcular_km_financeiro()` retorna chaves separadas como `valor_km_reembolso_tecnico`, `valor_reembolso_tecnico`, `valor_km_cobranca_cliente`, `valor_cobranca_cliente`.

Decisao de negocio:

- cobranca cliente nao entra no total a reembolsar ao tecnico;
- diferenca entre cobranca cliente e reembolso tecnico nao e "valor removido";
- valor removido deve representar itens rejeitados/removidos/reduzidos pela conferencia.

### Resumo financeiro

[CONFIRMADO NO CODIGO] Existem services de detalhe/consulta/resumo:

- `financeiro_detail_service.py`;
- `consulta_relatorio_service.py`;
- `resumo_cliente_service.py`;
- `km_financeiro_service.py`.

[CONTEXTO HISTORICO] O card de resumo foi reorganizado para separar:

- Reembolso ao tecnico;
- Cobranca ao cliente;
- Ajustes da conferencia.

Novo agente deve conferir o template atual antes de alterar qualquer label financeiro.

---

## 8. Politicas de Aprovacao

[CONFIRMADO NO CODIGO] Politicas sao resolvidas em [`relatorios/services/politica_valor_service.py`](relatorios/services/politica_valor_service.py) e aplicadas inicialmente em [`relatorios/services/politica_aprovacao_service.py`](relatorios/services/politica_aprovacao_service.py).

Regras confirmadas:

- `TIPOS_DESPESA_SEM_POLITICA = {PASSAGEM, TRANSPORTE}`;
- politica por tecnico para `ALIMENTACAO` e `HOSPEDAGEM`;
- limite de politica = `politica.valor * quantidade_tecnicos` para tipos por tecnico;
- hospedagem multiplica tambem por diarias quando periodo valido.

Formula atual:

```text
limite_efetivo = politica.valor

se tipo usa politica por tecnico:
    limite_efetivo *= quantidade_tecnicos_participantes

se tipo == hospedagem e diarias > 0:
    limite_efetivo *= diarias

valor_aprovado_inicial = min(valor_solicitado, limite_efetivo)
```

[CONFIRMADO NO CODIGO] `ItemDespesa.valor_politica` chama `calcular_limite_politica_despesa()` com:

- `tipo_despesa`;
- `quantidade_tecnicos=self.quantidade_tecnicos_participantes`;
- `diarias=self.quantidade_diarias_hospedagem`.

[CONFIRMADO NO CODIGO] `politica_aprovacao_service.aplicar_politica_valor_aprovado_inicial()` persiste `valor_aprovado` somente quando ha reducao por politica, preservando manual se solicitado.

Importante:

- nao duplicar calculo da politica no JavaScript;
- frontend deve exibir resultado calculado pelo backend;
- financeiro pode editar valor aprovado posteriormente.

---

## 9. Cliente Unico e Multi-cliente

[CONFIRMADO NO CODIGO] Service relevante: [`relatorios/services/clientes_relatorio_service.py`](relatorios/services/clientes_relatorio_service.py).

Funcoes importantes:

- `normalizar_ids_clientes`;
- `resolver_cliente_empresa_grupo`;
- `obter_clientes_relatorio`;
- `sync_clientes_relatorio`;
- `sync_clientes_despesa`;
- `sync_clientes_trecho`;
- `_propagar_cliente_unico_relatorio`.

Regra V2.0:

- quando o relatorio tem exatamente um cliente valido, esse cliente e propagado para despesas, trechos e vinculos/rateios sem sobrescrever cenarios multi-cliente legitimos;
- quando ha multiplos clientes, nao escolher automaticamente;
- troca de cliente unico deve atualizar itens existentes associados ao cliente anterior.

Empresas internas:

[CONFIRMADO NO CODIGO] `EmpresaGrupo` inclui `BLAZIUS E LORENZETTI`, `CONTROLSUL`, `FISCALMAX`.

[CONFIRMADO NO CODIGO] `resolver_cliente_empresa_grupo` busca cliente correspondente por nome/razao/nome fantasia.

[CONTEXTO HISTORICO] Houve problemas com FiscalMax sem cadastro de cliente correspondente; a correcao operacional em producao e cadastrar/regularizar Cliente FiscalMax ou garantir que a busca resolva exatamente um cliente ativo.

---

## 10. Tecnico Unico e Multi-tecnico

[CONFIRMADO NO CODIGO] Service relevante: [`relatorios/services/tecnicos_despesa_service.py`](relatorios/services/tecnicos_despesa_service.py).

Regras:

- tecnicos de despesa devem pertencer ao relatorio;
- se nenhum tecnico for informado para a despesa e houver exatamente um tecnico permitido no relatorio, o service aplica automaticamente esse tecnico;
- duplicidades sao removidas por normalizacao;
- `DespesaTecnico` tem unicidade por despesa/tecnico;
- se tecnico sai do relatorio, `remover_tecnicos_despesas_fora_relatorio` remove participacoes orfas.

Impacto:

- politica por tecnico depende de `DespesaTecnico`;
- nao usar quantidade total de tecnicos do relatorio para politica quando a despesa tem participantes especificos.

---

## 11. KM

Arquivos principais:

- [`relatorios/models.py`](relatorios/models.py): `TrechoKm`, `TrechoKMCliente`, `TrechoRateioKM`;
- [`relatorios/services/km_financeiro_service.py`](relatorios/services/km_financeiro_service.py);
- [`relatorios/services/trecho_km_calculo_service.py`](relatorios/services/trecho_km_calculo_service.py);
- [`relatorios/services/rateio_service.py`](relatorios/services/rateio_service.py);
- [`templates/relatorios/partials/_linha_trecho.html`](templates/relatorios/partials/_linha_trecho.html);
- [`templates/relatorios/relatorio_form.html`](templates/relatorios/relatorio_form.html).

Regras:

- `TrechoKm.km` guarda a distancia;
- `TrechoKm.valor_km` representa valor/km de cobranca;
- `TrechoKm.valor_km_control_sul` representa valor/km de reembolso tecnico;
- `TrechoRateioKM` guarda rateio por cliente com `km_cliente`, `valor_km`, `valor_final`;
- cliente unico pode preencher trecho automaticamente;
- multi-cliente exige selecao/rateio explicito.

### Ida e volta

[CONFIRMADO NO CODIGO] Implementacao V2.0 aparece em [`templates/relatorios/relatorio_form.html`](templates/relatorios/relatorio_form.html) com `criarTrechoIdaVolta()` e listener para `.btn-ida-volta`.

Comportamento esperado:

- botao fica na linha do trecho;
- trecho A -> B gera B -> A;
- copia data, KM, clientes e dados pertinentes;
- recalcula totais pela logica existente;
- impede duplicacao quando o inverso ja existe.

[CONFIRMADO NO CODIGO] O tour V2.0 referencia `.btn-ida-volta` em [`static/js/relatorio_novidades_tour.js`](static/js/relatorio_novidades_tour.js).

---

## 12. Anexos e Comprovantes

Esta e uma area sensivel do projeto.

### Estrutura atual

[CONFIRMADO NO CODIGO]

- `ItemDespesa.comprovante` continua existindo por compatibilidade;
- `TrechoKm.comprovante` continua existindo por compatibilidade;
- novos anexos adicionais usam `AnexoRelatorio`;
- `AnexoRelatorio` pode apontar para `despesa` ou `trecho`;
- storage de anexos usa [`relatorios/storage.py`](relatorios/storage.py) com `ANEXOS_ROOT` e `ANEXOS_URL`;
- limite total do relatorio vem de `RELATORIO_ANEXOS_MAX_TOTAL_MB`, default `1024`.

### Frontend

[CONFIRMADO NO CODIGO] Arquivo principal: [`static/js/relatorio_upload_monitor.js`](static/js/relatorio_upload_monitor.js).

Pontos confirmados:

- usa `DataTransfer`;
- exige input `multiple` para acumulacao;
- mantem estado de arquivos selecionados;
- renderiza lista visual;
- sincroniza input file real;
- cria/atualiza `upload_expected_manifest`;
- valida `FormData` antes de submit;
- calcula bytes locais + persistidos;
- remove arquivo pendente do estado local/DataTransfer/input;
- lida com remocao AJAX de persistidos.

[CONFIRMADO NO CODIGO] [`static/js/custom.js`](static/js/custom.js) usa `form.requestSubmit(btnEnviar)` para preservar listeners de submit; isso foi importante para evitar submit direto sem passar pelo monitor de anexos.

### Problemas historicos corrigidos

[CONTEXTO HISTORICO]

| Problema | Causa | Correcao |
| --- | --- | --- |
| Arquivo aparecia como "Aguardando envio", mas nao chegava no backend | Submit direto pulava listeners/manifesto e/ou input perdia FileList | `requestSubmit`, `DataTransfer`, validacao de `FormData` |
| UI mostrava anexo removido ainda pendente | Estado local e `DataTransfer` nao eram sincronizados | Remocao atualiza estado, input e utilizacao |
| Utilizacao de anexos contava errado | Fonte baseada em DOM/bytes antigos era incoerente | Fonte unica no JS: persistidos + locais ativos |
| Cache carregava JS antigo | static com querystring/versionamento | `base.html` e `relatorio_form.html` carregam assets com versoes |
| Ultimo anexo persistido podia continuar contado | item DOM persistido nao era removido corretamente | remocao AJAX/estado corrige DOM e bytes |

### Regras para novo agente

- Nunca considerar anexo enviado so porque apareceu na UI.
- Estado "Enviado" so apos confirmacao real do backend.
- Backend e autoridade final: `request.FILES` deve conter arquivos esperados.
- Nao armazenar arquivos em cookies/localStorage.
- Se alterar JS de anexos, atualizar versao/cache busting e rodar `collectstatic`.

---

## 13. Hospedagem por Periodo

[CONFIRMADO NO CODIGO] `ItemDespesa` possui:

- `data_inicio_hospedagem`;
- `data_fim_hospedagem`;
- propriedade `quantidade_diarias_hospedagem`.

[CONFIRMADO NO CODIGO] Service [`relatorios/services/periodo_despesa_service.py`](relatorios/services/periodo_despesa_service.py) calcula diarias.

Validacoes confirmadas no model:

- se tipo hospedagem e periodo aplicavel, entrada obrigatoria;
- saida obrigatoria;
- saida deve ser posterior a entrada.

Politica:

- hospedagem usa politica por tecnico;
- se diarias > 0, limite tambem multiplica por diarias.

---

## 14. Historico e Auditoria

Arquivo principal:

- [`relatorios/services/historico_service.py`](relatorios/services/historico_service.py)

Model:

- `HistoricoRelatorio`.

Eventos confirmados:

- criado;
- enviado;
- ajuste solicitado;
- reenviado;
- aprovado;
- rejeitado;
- reaberto;
- item rejeitado;
- item reativado;
- valor alterado;
- email enviado/falha.

Uso:

- timeline do relatorio;
- auditoria de workflow;
- auditoria financeira;
- reabertura administrativa registra evento sem apagar aprovacao anterior.

Regra:

- eventos historicos nao devem ser apagados para "limpar" fluxo;
- novas correcoes devem registrar eventos quando alteram status/valores relevantes.

---

## 15. Snapshot Financeiro

[CONFIRMADO NO CODIGO] Service: [`relatorios/services/snapshot_service.py`](relatorios/services/snapshot_service.py).

Objetivo:

- congelar dados financeiros em relatorios finalizados (`APROVADO` ou `REJEITADO`);
- proteger consulta/PDF/historico contra mudancas em dados vivos posteriores;
- armazenar payload JSON e checksum.

Conteudo confirmado no payload:

- usuario/finalizador;
- clientes;
- tecnicos;
- cidades;
- despesas;
- trechos;
- rateios de despesas;
- rateios KM;
- valores de politica;
- valores KM tecnico/cliente;
- status/rejeicoes;
- anexos/comprovantes quando serializados pelos payloads;
- totais.

Protecao:

- `RelatorioSnapshotFinanceiro.save()` bloqueia atualizacao comum;
- `delete()` bloqueia exclusao;
- `checksum` e unico.

Criacao:

- `aprovar_relatorio()` chama `criar_snapshot_financeiro`;
- `rejeitar_relatorio()` chama `criar_snapshot_financeiro`.

Nao fazer:

- nao recalcular snapshot de relatorio aprovado sem decisao explicita;
- nao usar dados vivos para alterar historico aprovado;
- nao remover checksum.

---

## 16. PDFs

[CONFIRMADO NO CODIGO] Services:

- [`relatorios/services/pdf_cliente_service.py`](relatorios/services/pdf_cliente_service.py);
- [`relatorios/services/pdf_interno_service.py`](relatorios/services/pdf_interno_service.py).

Rotas:

- PDF cliente unico: `/relatorios/<pk>/pdf/cliente/<cliente_id>/`;
- PDFs clientes: `/relatorios/<pk>/pdf/clientes/`;
- PDF interno: `/relatorios/<pk>/pdf-interno/`;
- PDF reembolso: `/relatorios/<pk>/pdf-reembolso/`.

Template/CSS:

- [PENDENTE / NAO CONFIRMADO] Consultar os templates especificos antes de alterar layout; historicamente foram ajustados para WeasyPrint com `break-inside: avoid`, repeticao de `thead` e rodape seguro.

Regras de negocio:

- PDF cliente deve mostrar visao de cobranca cliente;
- PDF interno deve mostrar visao interna/reembolso tecnico;
- nao misturar `R$ 1,35` no PDF cliente quando nao for informacao desejada;
- nao alterar calculos dentro do template.

---

## 17. Validacoes

Principios:

- backend e autoridade final;
- frontend ajuda UX, mas nao e fonte de verdade;
- mensagens devem ser especificas e apontar item/campo;
- nao mascarar erros de formset.

Locais:

| Validacao | Local |
| --- | --- |
| Form principal e formsets | [`relatorios/forms.py`](relatorios/forms.py) |
| Datas/despesas/hospedagem/anexos | `ItemDespesa.clean`, forms |
| Envio/aprovacao | [`relatorios/services/validacoes_operacionais.py`](relatorios/services/validacoes_operacionais.py) |
| Rateios | [`relatorios/services/rateio_service.py`](relatorios/services/rateio_service.py) |
| KM | [`relatorios/services/trecho_km_calculo_service.py`](relatorios/services/trecho_km_calculo_service.py) |
| Workflow | [`relatorios/services/workflow_service.py`](relatorios/services/workflow_service.py) |
| Anexos | model/service/view + JS monitor |

Validacoes importantes:

- periodo do relatorio;
- relatorio precisa ter despesa/KM quando enviado/aprovado;
- tecnico reembolso quando ha pagamento ao tecnico;
- empresa do grupo em nao reembolsavel;
- clientes com participacao real;
- cliente sem valor KM somente quando ha KM aplicavel;
- tecnicos de despesa devem pertencer ao relatorio;
- rateios precisam bater com visao financeira;
- nota fiscal exige numero do documento;
- hospedagem exige entrada/saida quando aplicavel.

---

## 18. Permissoes e Seguranca

[CONFIRMADO NO CODIGO] Arquivo central: [`relatorios/services/autorizacao_service.py`](relatorios/services/autorizacao_service.py).

Grupos:

- `Financeiro`;
- `Tecnico`;
- `Gestor`;
- `Administrador ERP`;
- `Domain Admins`.

Helpers importantes:

| Helper | Uso |
| --- | --- |
| `usuario_tem_acesso_total` | superuser/domain admin/admin extra |
| `usuario_eh_admin_extra` | `EXTRA_ADMIN_USERS` |
| `usuario_pode_atuar_como_financeiro` | acoes financeiras |
| `usuario_pode_acessar_manutencao` | tela manutencao |
| `usuario_pode_reabrir_relatorio` | reabertura especial |
| `usuario_pode_editar_relatorio` | edicao geral por status/dono/responsavel |
| `usuario_pode_enviar_relatorio` | envio para conferencia |
| `usuario_pode_editar_relatorio_em_conferencia` | restricao para conferencia |

Admin extra:

[CONFIRMADO NO CODIGO] `EXTRA_ADMIN_USERS` aceita lista/string separada por virgula, normaliza login lowercase e remove dominio/email. Exemplo em `.env.example`: `EXTRA_ADMIN_USERS=joao.martins`.

Seguranca:

- nao expor `.env`;
- nao servir `ANEXOS_ROOT` como publico sem controle;
- anexos/help images devem passar por storage/view conforme implementacao;
- tela manutencao nao executa shell livre.

---

## 19. Frontend

Principais templates:

| Arquivo | Responsabilidade |
| --- | --- |
| [`templates/base.html`](templates/base.html) | layout base, navbar/sidebar, HTMX, JS globais |
| [`templates/relatorios/relatorio_form.html`](templates/relatorios/relatorio_form.html) | criacao/edicao do relatorio, formsets, JS inline do formulario |
| [`templates/relatorios/relatorio_detail.html`](templates/relatorios/relatorio_detail.html) | conferencia/detalhe |
| [`templates/relatorios/relatorio_consulta.html`](templates/relatorios/relatorio_consulta.html) | consulta final/readonly |
| [`templates/relatorios/partials/_linha_despesa.html`](templates/relatorios/partials/_linha_despesa.html) | linha de despesa |
| [`templates/relatorios/partials/_linha_trecho.html`](templates/relatorios/partials/_linha_trecho.html) | linha de KM |

JavaScript:

| Arquivo | Responsabilidade |
| --- | --- |
| [`static/js/custom.js`](static/js/custom.js) | comportamento global; usa `requestSubmit` |
| [`static/js/relatorio_upload_monitor.js`](static/js/relatorio_upload_monitor.js) | anexos, DataTransfer, manifesto, utilizacao |
| [`static/js/relatorio_autosave.js`](static/js/relatorio_autosave.js) | autosave de rascunhos |
| [`static/js/relatorio_form_tour.js`](static/js/relatorio_form_tour.js) | tour/guia do formulario |
| [`static/js/relatorio_novidades_tour.js`](static/js/relatorio_novidades_tour.js) | tour V2.0 |
| [`static/js/anexo_preview.js`](static/js/anexo_preview.js) | preview/estado de anexos legado |

Cache/static:

[CONFIRMADO NO CODIGO] `templates/base.html` usa `static_version|default:'20260807'`. `relatorio_form.html` usa querystrings especificas como `relatorio_upload_monitor.js?v=20260811-2` e `relatorio_novidades_tour.js?v=20260811`.

Regra:

- ao alterar JS/CSS em producao, rodar `collectstatic`;
- se usuario ainda vir comportamento antigo, conferir cache busting antes de culpar navegador/cookies.

---

## 20. Tour da V2.0

[CONFIRMADO NO CODIGO] Arquivo: [`static/js/relatorio_novidades_tour.js`](static/js/relatorio_novidades_tour.js).

Chaves confirmadas:

- `relatorioNovidadeMultiplosAnexos:v1`;
- `relatorioNovidadeHospedagemPeriodo:v1`;
- `relatorioNovidadeMultiplosTecnicos:v1`;
- `relatorioNovidadeMultiplasCidades:v1`;
- `relatorioNovidadeAutoClienteTecnico:v1`;
- `relatorioNovidadeIdaVolta:v1`.

Novidades:

- multiplos anexos;
- hospedagem por periodo;
- multiplos tecnicos;
- multiplas cidades;
- auto cliente/tecnico;
- ida e volta.

[PENDENTE / NAO CONFIRMADO] Verificar mecanismo exato de persistencia do tour antes de mudar. O JS contem chaves de controle; confirmar se usa endpoint/session/local storage olhando o arquivo completo.

---

## 21. Central de Ajuda e Manutencao

### Central de Ajuda

[CONFIRMADO NO CODIGO] Models:

- `CategoriaAjuda`;
- `ArtigoAjuda`;
- `ImagemAjuda`.

Features historicas:

[CONTEXTO HISTORICO]

- sidebar sticky;
- categorias clicaveis;
- artigos relacionados;
- edicao restrita a admins/domain admins/admin extra;
- editor visual;
- imagens em `HELP_IMAGES_ROOT`;
- sanitizacao HTML/Markdown com `bleach`/`markdown`.

### Manutencao

[CONFIRMADO NO CODIGO] Service: [`relatorios/services/manutencao_service.py`](relatorios/services/manutencao_service.py).

Features:

- rota `/manutencao/`;
- acesso restrito a admins;
- consulta de logs;
- listagem/reenviar e-mails pendentes/falhos via `EmailLog`;
- logs mascarados/limitados conforme service.

---

## 22. Legados e Dados Demo

[CONFIRMADO NO CODIGO] Comandos:

- `importar_relatorios_legados`;
- `limpar_dados_demo`;
- `popular_dados_demo`.

Legados:

- `RelatorioLegado` tem `is_legado=True`, `is_historico_frio=True`;
- nao devem entrar no workflow moderno;
- consulta propria em `/relatorios/legados/`;
- nao exigir validacoes modernas.

Demo:

[CONTEXTO HISTORICO] O comando seguro de limpeza e:

```bash
python manage.py limpar_dados_demo --dry-run
python manage.py limpar_dados_demo --confirmar
```

Regra: remover somente registros marcados explicitamente como demo; nao remover clientes/tecnicos/usuarios reais.

---

## 23. Estado Atual da V2.0

### Status

[CONFIRMADO NO CODIGO/GIT] Branch atual no momento da analise: `main`.

[CONFIRMADO NO CODIGO/GIT] Ultimo commit local:

```text
3cd7571 Release oficial v2.0
```

[CONFIRMADO NO CODIGO/GIT] Tags locais encontradas:

```text
v1.0-checkpoint
```

[PENDENTE / NAO CONFIRMADO] Nao foi confirmada tag `v2.0`.

### Funcionalidades V2.0

| Funcionalidade | Status | Evidencia |
| --- | --- | --- |
| Cliente unico | Implementado | `clientes_relatorio_service._propagar_cliente_unico_relatorio` |
| Troca de cliente unico | Implementado | propagacao para despesas/trechos/rateios no service |
| Tecnico unico | Implementado | `tecnicos_despesa_service.sync_tecnicos_despesa` |
| Ida e volta | Implementado | `criarTrechoIdaVolta` e `.btn-ida-volta` |
| Anexos robustos | Implementado, area sensivel | `relatorio_upload_monitor.js`, manifesto, DataTransfer |
| Utilizacao de anexos | Implementado | JS monitor e limite `RELATORIO_ANEXOS_MAX_TOTAL_MB` |
| Cidades multiplas | Implementado | `CidadeAtendimento`, formset `extra=0` |
| Politica por tecnico/diarias | Implementado | `politica_valor_service`, propriedades de `ItemDespesa` |
| Tour V2.0 | Implementado | `relatorio_novidades_tour.js` |

### Validacao

[CONTEXTO HISTORICO] O usuario informou que "tudo isso passou" antes de solicitar commit da release oficial.

[CONFIRMADO NO CODIGO/GIT] Foi criado o commit oficial de release.

[PENDENTE / NAO CONFIRMADO] Esta tarefa de documentacao nao reexecutou testes Django completos no servidor Linux.

---

## 24. Decisoes Arquiteturais e de Negocio

Nao reverter casualmente:

| Decisao | Motivo |
| --- | --- |
| Backend e autoridade final | Evitar inconsistencias por JS/cache/manipulacao |
| `valor_aprovado=NULL` significa integral | Regra usada por `valor_final` |
| Reembolso KM tecnico separado da cobranca cliente | Evita distorcoes no resumo/PDF/financeiro |
| Snapshot financeiro e imutavel | Auditoria de aprovados/rejeitados |
| Nao recalcular historico aprovado com dados vivos | Preservar fechamento contabil |
| `ItemDespesa.comprovante` permanece por compatibilidade | Migracao gradual para `AnexoRelatorio` |
| Alteracoes pequenas e isoladas | Sistema em producao e com regras sensiveis |
| Nao servir diretorios de anexos publicamente sem controle | Seguranca |
| Nao usar cookies/localStorage para armazenar arquivos | Arquivos grandes e sensiveis |
| Financeiro pode alterar valor aprovado apos politica | Politica define inicial; conferencia decide |
| Multi-cliente/multi-tecnico nao deve receber automacao arbitraria | Evitar sobrescrever rateios reais |

---

## 25. Problemas Historicos e Correcoes

| Problema | Causa | Correcao | Status |
| --- | --- | --- | --- |
| KM tecnico aparecia como diferenca removida | Resumo misturava total solicitado/aprovado com cobranca cliente/KM | Separar reembolso, cobranca e valor removido | Resolvido historicamente |
| KM interno nao somava no resumo | Resumo usava apenas trechos principais | Ajustar total de reembolso tecnico incluindo KM interno quando aplicavel | Resolvido historicamente |
| Empresas internas e valor KM | Busca/cadastro e regra de valor KM variavam | Centralizar valor KM e resolver empresas internas | Parcialmente confirmado; validar em producao |
| Usuario dono nao conseguia enviar | Autorizacao/status/criado_por precisavam diagnostico | Logs detalhados em autorizacao | Implementado |
| Conferencia editavel por usuario comum | Permissao de edicao em conferencia ampla demais | Restringir a financeiro/acesso total | Implementado |
| Anexos sumiam | Submit/JS/FileList/manifesto inconsistentes | `DataTransfer`, `requestSubmit`, manifesto, validacao FormData | Implementado, area critica |
| Multiplo comprovante substituia anterior | FileList/input nao acumulava | `DataTransfer` acumulativo e lista visual coerente | Implementado |
| Hospedagem gerou `TipoDespesa` NameError | Referencia/import quebrado | Usar enum real de models | Resolvido historicamente |
| UTF-8/mojibake em telas | Strings com encoding quebrado em arquivos | Varreduras e correcoes pontuais | Monitorar |
| Cidade extra vazia | Formset/JS criava linha indevida | `extra=0` e criar linha so quando nenhuma existe | Implementado |
| PDF cliente quebrava linhas | CSS WeasyPrint insuficiente | `break-inside/page-break-inside`, repeat header | Implementado historicamente |
| Reabrir aprovado | Necessidade administrativa excepcional | Endpoint/servico restrito a Gabriel | Implementado |

---

## 26. Deploy

[CONFIRMADO NO CODIGO] `.env.example` contem variaveis esperadas, sem valores secretos.

Variaveis importantes:

- `DATABASE_URL` ou `POSTGRES_*`;
- `LDAP_*`;
- `AD_GROUP_MAPPING`;
- `EXTRA_ADMIN_USERS`;
- `APP_LOG_DIR`;
- `ANEXOS_ROOT`;
- `ANEXOS_URL`;
- `RELATORIO_ANEXOS_MAX_TOTAL_MB`;
- `HELP_IMAGES_ROOT`;
- `HELP_IMAGES_MAX_UPLOAD_MB`;
- `VALOR_KM_CONTROLSUL`;
- `CLIENTES_API_*`;
- `EMAIL_*`;
- `APP_BASE_URL` / `SITE_URL`.

Procedimento base esperado em Linux:

```bash
cd /opt/app_relatorios/projeto_relatorios_claude
source venv/bin/activate

python manage.py check
python manage.py test relatorios
python manage.py collectstatic --noinput

# se houver migrations novas
python manage.py makemigrations --check --dry-run
python manage.py migrate

# reinicio do servico
sudo systemctl restart app_relatorios
sudo systemctl status app_relatorios --no-pager
```

[PENDENTE / NAO CONFIRMADO] O nome `app_relatorios` do servico foi usado historicamente. Para confirmar no servidor:

```bash
systemctl list-units --type=service | grep -i relatorio
sudo systemctl cat app_relatorios
```

Logs:

```bash
journalctl -u app_relatorios -n 200 --no-pager
tail -n 200 /home/app_relatorios_logs/app.log
tail -n 200 /home/app_relatorios_logs/errors.log
```

Permissao de anexos:

```bash
sudo systemctl cat app_relatorios | grep -E "User=|Group="
sudo -u <usuario_app> test -w /home/app_relatorios_files && echo OK
sudo -u <usuario_app> test -w /home/app_relatorios_files/help_images && echo OK
```

---

## 27. Testes

Comandos importantes:

```bash
python manage.py check
python manage.py test relatorios
python manage.py test
git diff --check
```

JavaScript:

```bash
node --check static/js/relatorio_upload_monitor.js
node --check static/js/relatorio_novidades_tour.js
node --check static/js/relatorio_autosave.js
node --check static/js/custom.js
```

[CONTEXTO HISTORICO] No Windows local, Django pode falhar por:

```text
ModuleNotFoundError: No module named 'ldap'
```

Neste caso, rodar no servidor Linux/venv com dependencias LDAP instaladas.

Testes manuais indispensaveis antes de alterar release:

- criar relatorio com cliente unico;
- trocar cliente unico e verificar despesas/KM/rateios;
- criar relatorio multi-cliente e garantir que nada e sobrescrito;
- criar despesa com tecnico unico e verificar politica;
- criar despesa multi-tecnico e verificar politica por participantes;
- adicionar/remover anexos pendentes e persistidos;
- enviar relatorio com anexos e verificar `request.FILES`/persistencia;
- criar ida e volta e salvar/reabrir;
- aprovar e verificar snapshot/PDF.

---

## 28. Git

[CONFIRMADO NO CODIGO/GIT] Estado observado antes de criar este documento:

- branch: `main`;
- ultimo commit: `3cd7571 Release oficial v2.0`;
- tag local: `v1.0-checkpoint`;
- working tree estava limpo antes da criacao de `CONTEXTO_PROJETO.md`.

[PENDENTE / NAO CONFIRMADO] A criacao deste documento deixa o working tree sujo com arquivo novo. Nao commitar sem pedido explicito.

Convencoes historicas:

- commits manuais em `main` foram usados neste repositorio;
- nao fazer push/tag/release sem confirmacao do usuario.

---

## 29. Mapa de Arquivos Importantes

| Arquivo | Responsabilidade |
| --- | --- |
| [`manage.py`](manage.py) | entrada Django, settings default prod |
| [`requirements.txt`](requirements.txt) | dependencias Python |
| [`app_relatorios/settings/base.py`](app_relatorios/settings/base.py) | settings comuns, env, LDAP, logs, storage |
| [`app_relatorios/settings/prod.py`](app_relatorios/settings/prod.py) | banco/producao |
| [`app_relatorios/urls.py`](app_relatorios/urls.py) | URLs raiz |
| [`relatorios/models.py`](relatorios/models.py) | models e regras/propriedades centrais |
| [`relatorios/forms.py`](relatorios/forms.py) | forms e formsets |
| [`relatorios/views.py`](relatorios/views.py) | views HTML/AJAX/PDF/upload |
| [`relatorios/urls.py`](relatorios/urls.py) | rotas do app |
| [`relatorios/storage.py`](relatorios/storage.py) | storages de anexos/help images |
| [`relatorios/services/autorizacao_service.py`](relatorios/services/autorizacao_service.py) | permissoes |
| [`relatorios/services/workflow_service.py`](relatorios/services/workflow_service.py) | transicoes de status |
| [`relatorios/services/rateio_service.py`](relatorios/services/rateio_service.py) | rateios |
| [`relatorios/services/km_financeiro_service.py`](relatorios/services/km_financeiro_service.py) | KM financeiro tecnico/cliente |
| [`relatorios/services/trecho_km_calculo_service.py`](relatorios/services/trecho_km_calculo_service.py) | calculos de trecho KM |
| [`relatorios/services/politica_valor_service.py`](relatorios/services/politica_valor_service.py) | resolucao de politicas |
| [`relatorios/services/politica_aprovacao_service.py`](relatorios/services/politica_aprovacao_service.py) | valor aprovado inicial por politica |
| [`relatorios/services/clientes_relatorio_service.py`](relatorios/services/clientes_relatorio_service.py) | cliente unico/multi-cliente |
| [`relatorios/services/tecnicos_despesa_service.py`](relatorios/services/tecnicos_despesa_service.py) | tecnicos participantes de despesa |
| [`relatorios/services/snapshot_service.py`](relatorios/services/snapshot_service.py) | snapshot financeiro |
| [`relatorios/services/pdf_cliente_service.py`](relatorios/services/pdf_cliente_service.py) | PDF cliente |
| [`relatorios/services/pdf_interno_service.py`](relatorios/services/pdf_interno_service.py) | PDF interno |
| [`relatorios/services/reabertura_relatorio_service.py`](relatorios/services/reabertura_relatorio_service.py) | reabertura administrativa |
| [`templates/base.html`](templates/base.html) | layout base e static global |
| [`templates/relatorios/relatorio_form.html`](templates/relatorios/relatorio_form.html) | tela principal de cadastro/edicao |
| [`templates/relatorios/relatorio_detail.html`](templates/relatorios/relatorio_detail.html) | conferencia/detalhe |
| [`templates/relatorios/relatorio_consulta.html`](templates/relatorios/relatorio_consulta.html) | consulta final |
| [`templates/relatorios/partials/_linha_despesa.html`](templates/relatorios/partials/_linha_despesa.html) | linha despesa |
| [`templates/relatorios/partials/_linha_trecho.html`](templates/relatorios/partials/_linha_trecho.html) | linha KM |
| [`static/js/relatorio_upload_monitor.js`](static/js/relatorio_upload_monitor.js) | fluxo robusto de anexos |
| [`static/js/relatorio_autosave.js`](static/js/relatorio_autosave.js) | autosave |
| [`static/js/relatorio_novidades_tour.js`](static/js/relatorio_novidades_tour.js) | tour V2.0 |
| [`static/js/custom.js`](static/js/custom.js) | JS global |

---

## 30. Proximos Passos

### Pendente Imediato

- [PENDENTE / NAO CONFIRMADO] Decidir se deve criar tag `v2.0`.
- [PENDENTE / NAO CONFIRMADO] Em qualquer publicacao futura, rodar `check`, `test relatorios`, `collectstatic` e validar upload real de anexos.
- [PENDENTE / NAO CONFIRMADO] Validar se `systemctl` realmente usa servico `app_relatorios` no servidor atual.

### Pendente V2.0

- [PENDENTE / NAO CONFIRMADO] Confirmar pos-release em producao: anexos, ida e volta, cliente/técnico unico, politica por tecnico.
- [PENDENTE / NAO CONFIRMADO] Conferir se tag/release formal sera criada alem do commit.

### Futuro

- Evoluir anexos para upload assincrono real, se necessario.
- Migrar definitivamente `ItemDespesa.comprovante` para `AnexoRelatorio`, somente com plano de migracao e compatibilidade.
- Relacionar trechos KM a cidades origem/destino.
- Melhorar auditoria formal de politica manual vs automatica, caso financeiro precise.
- Expandir dashboards historicos/legados.

### Ideias

- Compressao inteligente de imagens grandes antes do upload.
- Preview/galeria de comprovantes.
- Reprocessamento seguro de snapshot via comando administrativo controlado.
- Observabilidade de timeout/upload com correlacao frontend/backend.

---

## 31. Guia Para Novo Chat

Ao iniciar um novo chat/agente:

1. Leia este arquivo inteiro antes de alterar codigo.
2. Use este documento como baseline, mas confirme no codigo antes de mudar.
3. Nao assuma como implementado nada marcado como `[CONTEXTO HISTORICO]` ou `[PENDENTE / NAO CONFIRMADO]`.
4. Preserve as decisoes arquiteturais e de negocio listadas.
5. Evite refatoracoes amplas em fluxo financeiro, anexos, workflow e snapshot.
6. Nunca altere relatorios aprovados/snapshots sem autorizacao explicita.
7. Nao publique, nao crie tag e nao reinicie producao sem confirmacao.
8. Quando mexer em JS/CSS, lembre de cache busting e `collectstatic`.
9. Quando mexer em anexos, teste ponta a ponta: UI -> FormData -> `request.FILES` -> storage -> banco.
10. Quando mexer em financeiro, teste reembolso tecnico e cobranca cliente separadamente.

---

## 32. Informacoes Confirmadas, Historicas e Pendentes

### Confirmadas no codigo

- Django 6.0.4 e dependencias principais.
- Models centrais e choices.
- Services de workflow, autorizacao, politicas, KM, rateios, snapshot e anexos.
- V2.0 marcada em commit Git `3cd7571 Release oficial v2.0`.
- `EXTRA_ADMIN_USERS`, `ANEXOS_ROOT`, `HELP_IMAGES_ROOT`, `APP_LOG_DIR`, `VALOR_KM_CONTROLSUL`.
- Snapshot imutavel.
- Reabertura administrativa.
- Cliente/técnico unico services.
- Monitor de anexos com DataTransfer/manifesto.

### Contexto historico

- Usuario informou que validacoes passaram no servidor antes do commit oficial V2.0.
- Houve varias correcoes de KM, anexos, permissoes, layout e UTF-8 ao longo do projeto.
- Nome de servico de producao usado historicamente: `app_relatorios`.

### Pendentes / nao confirmados

- Existencia de tag `v2.0`.
- Resultado atual de testes completos no servidor apos a criacao deste documento.
- Nome definitivo do servico systemd em todos os ambientes.
- Persistencia exata do tour sem ler o JS completo.
- Se todos os PDFs ja usam snapshot em todos os cenarios possiveis.

