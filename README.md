# ERP de Relatorios de Viagem

Este README serve como contexto portatil para humanos e chats/agentes de IA que
precisem continuar o desenvolvimento deste projeto sem depender do historico da
conversa original.

## Resumo Rapido

Este repositorio contem um ERP Django para cadastro, envio, conferencia,
aprovacao, consulta e emissao de PDFs de relatorios tecnicos/de viagem.

O projeto Django raiz e `app_relatorios`; o app principal de negocio e
`relatorios`.

Stack principal:

- Python/Django: Django 6.0.4
- Banco de producao: PostgreSQL
- Frontend: Django templates, Bootstrap, HTMX e JavaScript proprio
- PDF: WeasyPrint
- Auth: Django auth + LDAP/Active Directory opcional
- Servidor de producao atual: Gunicorn via `app_relatorios.service`
- Autorizacao: veja [Central de Permissoes](docs/central_permissoes.md).

Comando de validacao mais usado localmente:

```powershell
$env:DJANGO_SETTINGS_MODULE="app_relatorios.settings.test_sqlite"
$env:LDAP_AUTH_ENABLED="False"
.\venv\Scripts\python.exe manage.py check --settings=app_relatorios.settings.test_sqlite
.\venv\Scripts\python.exe manage.py makemigrations --check --settings=app_relatorios.settings.test_sqlite
```

Em producao/Linux:

```bash
cd /opt/app_relatorios/projeto_relatorios_claude
venv/bin/python manage.py check --settings=app_relatorios.settings.prod
venv/bin/python manage.py makemigrations --check --settings=app_relatorios.settings.prod
sudo systemctl restart app_relatorios.service
```

## Estado Atual Importante

Branch principal remota: `origin/main`.

Consulte `git log` e o servidor para verificar a versao implantada. A documentacao nao garante o estado atual do ambiente.

O checkout de producao fica em:

```bash
/opt/app_relatorios/projeto_relatorios_claude
```

O alias SSH local usado neste ambiente e:

```bash
ssh nightowl-release
```

Nao colocar segredos no README. Valores de banco, LDAP, SMTP e tokens ficam em
`.env` no ambiente.

## Estrutura do Projeto

```text
projeto_relatorios_claude/
├── app_relatorios/              # settings, urls raiz, wsgi/asgi
├── relatorios/                  # app principal
│   ├── migrations/              # historico de schema
│   ├── services/                # regras de negocio
│   ├── management/commands/     # comandos operacionais
│   ├── models.py
│   ├── forms.py
│   ├── urls.py
│   ├── views.py
│   └── tests.py
├── templates/                   # templates globais e do app
├── static/                      # arquivos static
├── staticfiles/                 # saida de collectstatic
├── deploy/                      # exemplos de gunicorn/nginx
├── docs/                        # documentacao auxiliar
├── media/                       # arquivos locais
├── manage.py
└── requirements.txt
```

Arquivos de maior importancia:

- `relatorios/models.py`: models principais, choices e propriedades.
- `relatorios/forms.py`: forms e formsets do relatorio.
- `relatorios/views.py`: views HTML/AJAX/PDF.
- `relatorios/services/workflow_service.py`: envio, aprovacao, rejeicao,
  devolucao para ajuste, reabertura e validacoes de workflow.
- `relatorios/services/autorizacao_service.py`: camada legada/flag-aware de
  autorizacao.
- `relatorios/services/permissoes_service.py`: catalogo central de permissoes,
  evaluators centrais, shadow/cutover e perfil somente leitura.
- `relatorios/services/usuarios_permissoes_service.py`: UI/operacoes da Central
  de Usuarios e Permissoes.
- `relatorios/services/clientes_relatorio_service.py`: vinculo de clientes ao
  relatorio, motivos por cliente e resolucao de empresas do grupo.
- `templates/relatorios/relatorio_form.html`: formulario principal; contem JS
  inline importante.
- `static/js/custom.js`: validacoes globais do formulario.

## Dominio Principal

O model central e `RelatorioTecnico`. Ele agrega:

- clientes do relatorio (`RelatorioCliente`);
- tecnicos/equipe (`RelatorioTecnicoEquipe`);
- cidades de atendimento;
- despesas (`ItemDespesa`);
- trechos de KM (`TrechoKm`);
- anexos (`AnexoRelatorio`);
- rateios por cliente;
- historico;
- snapshot financeiro;
- PDFs cliente/interno.

Status principais:

```text
rascunho
conferencia_pendente
ajuste_pendente
aprovado
rejeitado
```

Regra de workflow consolidada:

- `rascunho` e `ajuste_pendente`: edicao geral pode ser permitida conforme
  relacao/permissao.
- `conferencia_pendente`: relatorio geral fica congelado para edicao geral.
  Acoes financeiras especificas continuam por permissoes proprias.
- `aprovado` e `rejeitado`: edicao geral bloqueada.
- Full protegido nao ignora workflow/status.

## Reembolso, KM e Financeiro

Ha separacao entre:

- reembolso ao tecnico: usa `VALOR_KM_CONTROLSUL`, default `1.35`;
- cobranca ao cliente: usa `Cliente.valor_km` e regras financeiras;
- empresas internas do grupo: tratadas como clientes internos para relatorio
  nao reembolsavel.

Nao misturar dominios:

- `financeiro.acessar` nao controla configuracao de KM;
- `clientes.configurar_valor_km` controla valor KM de cliente;
- alteracao de valores, itens e rateios pertence ao lote 4C e ainda deve ser
  tratada com cuidado.

## Clientes e Empresas do Grupo

Para relatorio `nao_reembolsavel`, o campo `empresa_grupo` substitui a selecao
manual de cliente externo. O sistema tenta localizar o cadastro interno da
empresa do grupo em `Cliente`.

Resolver: `resolver_cliente_empresa_grupo()` prioriza a associacao explicita ativa em `EmpresaGrupoCliente`. Sem associacao, usa fallback textual e falha fechado em caso de ambiguidade ou cliente associado inativo.

## Autorizacao: Regras de Ouro

1. Backend e autoridade. Template/sidebar nunca podem ser unica protecao.
2. Workflow/status nunca vira toggle.
3. Full protegido vem somente de `ERP_FULL_ACCESS_USERS`.
4. Domain Admins, `is_superuser`, `is_staff`, Financeiro, Gestor,
   Administrador ERP e `EXTRA_ADMIN_USERS` nao sao Full no motor central.
5. Relatorio proprio = `criado_por_id == user.pk`.
6. Tecnico responsavel nao concede automaticamente acesso central a relatorio
   alheio.
7. Relatorio alheio exige `relatorios.visualizar_alheios`.
8. Edicao de alheio exige `visualizar_alheios` + `editar_alheios` + workflow.
9. Acoes financeiras exigem permissao especifica + visibilidade + workflow.
10. Com flag OFF, comportamento produtivo precisa continuar legado.

## Validacoes Frontend Recentes

O formulario de relatorio tem validacoes importantes em JS inline e em
`static/js/custom.js`.

Hotfixes recentes:

- Justificativa por cliente: `motivosInvalidos` separado de `invalido`.
  Erro de outro campo nao pode acusar motivo por cliente.
- Datas de hospedagem: datas ocultas de hospedagem so sao validadas quando a
  despesa pai e `hospedagem`. Campos antigos em alimentacao nao devem bloquear
  submit.
- Empresa do grupo: frontend reconhece aliases de empresas internas para
  selecionar o cliente automaticamente.

Ao mexer no formulario:

- nao esconder erro real trocando expectation para redirect;
- respeitar `CadastroObrigatorioMiddleware` nos testes;
- nao apagar valores persistidos sem decisao explicita;
- nao bypassar validacoes backend.

## Ambientes e Settings

Settings principais:

- `app_relatorios.settings.base`
- `app_relatorios.settings.dev`
- `app_relatorios.settings.prod`
- `app_relatorios.settings.test_sqlite` local, nao versionado, usado para
  testes comportamentais no Windows quando PostgreSQL local nao esta
  disponivel.

`manage.py` usa settings configurado por env. Em producao, use:

```bash
--settings=app_relatorios.settings.prod
```

No Windows local, usar sempre o Python explicito:

```powershell
.\venv\Scripts\python.exe
```

Variaveis relevantes:

- `SECRET_KEY`
- `ALLOWED_HOSTS`
- `DATABASE_URL` ou `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD`
- `LDAP_AUTH_ENABLED`
- `PERMISSOES_CENTRAL_ENABLED`
- `ERP_FULL_ACCESS_USERS`
- `EXTRA_ADMIN_USERS`
- `CLIENTES_API_URL`
- `CLIENTES_API_TOKEN`
- `VALOR_KM_CONTROLSUL`
- `APP_LOG_DIR`
- `ANEXOS_ROOT`
- `HELP_IMAGES_ROOT`

## Testes

Testes principais ficam em:

```text
relatorios/tests.py
```

O projeto possui muitos testes acumulados por lote. Alguns grupos relevantes:

- `ClienteEmpresaGrupoTests`
- testes de cutover de manutencao/cadastros/ajuda;
- testes de visibilidade de relatorios;
- testes de edicao/envio;
- testes de financeiro 4A e 4B;
- testes de perfil global somente leitura;
- testes de frontend por inspecao de template/JS quando nao ha runner JS.

Comandos locais usuais:

```powershell
$env:DJANGO_SETTINGS_MODULE="app_relatorios.settings.test_sqlite"
$env:LDAP_AUTH_ENABLED="False"
.\venv\Scripts\python.exe manage.py test relatorios.tests.ClienteEmpresaGrupoTests --settings=app_relatorios.settings.test_sqlite --verbosity=2
.\venv\Scripts\python.exe manage.py check --settings=app_relatorios.settings.test_sqlite
.\venv\Scripts\python.exe manage.py makemigrations --check --settings=app_relatorios.settings.test_sqlite
git diff --check
```

No SQLite local, `test_sqlite.py` pode usar:

```python
from .base import *  # noqa

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
MIGRATION_MODULES = {"relatorios": None}
```

Esse arquivo deve ser local e excluido em `.git/info/exclude`, nao no
`.gitignore` versionado.

## Deploy / Atualizacao do Servidor

Fluxo usado nos ultimos hotfixes:

Local:

```powershell
git status --short
git diff --check
git add <arquivos explicitos>
git commit -m "<mensagem>"
git push origin HEAD:main
```

Servidor:

```bash
ssh nightowl-release
cd /opt/app_relatorios/projeto_relatorios_claude
git fetch origin
git pull --ff-only origin main
venv/bin/python manage.py check --settings=app_relatorios.settings.prod
venv/bin/python manage.py makemigrations --check --settings=app_relatorios.settings.prod
sudo systemctl restart app_relatorios.service
systemctl is-active app_relatorios.service
```

Quando alterar arquivos em `static/`, pode ser necessario:

```bash
venv/bin/python manage.py collectstatic --noinput --settings=app_relatorios.settings.prod
sudo systemctl restart app_relatorios.service
```

Quando alterar apenas Python/templates inline, geralmente nao precisa
`collectstatic`, mas precisa reiniciar/recarregar a aplicacao para garantir que
Gunicorn carregue o codigo/template novo.

Observacao operacional: o servidor ja possuia arquivos rastreados `__pycache__`
modificados e `static/admin/` nao versionado. Nao usar `git reset --hard` ou
`git clean` sem autorizacao explicita.

## Logs

Diretorio de logs de producao documentado:

```bash
/home/app_relatorios_logs
```

Arquivos esperados:

- `app_relatorios.log`
- `errors.log`
- `emails.log`
- `maps.log`
- `pdfs.log`
- `security.log`

Service:

```bash
journalctl -u app_relatorios.service --since "10 minutes ago" --no-pager
```

## Cuidados ao Continuar o Projeto

Antes de implementar:

1. Leia o codigo real. Nao presuma comportamento pelo historico.
2. Rode `git status --short`.
3. Se houver sujeira nao relacionada, nao reverta sem autorizacao.
4. Para mudancas em permissao, preservar flag OFF 100% legado.
5. Para frontend do formulario, validar se existe POST real; muitos bugs sao
   bloqueios JS antes do backend.
6. Para testes HTTP, garantir cadastro completo:
   - `first_name`
   - `last_name`
   - `email`
   - `PerfilUsuario.cadastro_confirmado_em`
7. Para producao, nao rodar `migrate`, `collectstatic`, restart ou deploy sem
   pedido explicito.



## Integracoes e APIs

- API de clientes: `relatorios/services/clientes_api_service.py`, configurada por `CLIENTES_API_URL`, `CLIENTES_API_TOKEN` e timeout. Consulte `docs/clientes_api_sync.md`.
- Geocodificacao Nominatim e rotas OSRM: `relatorios/services/maps_service.py`; endpoints internos em `/mapas/buscar-endereco/` e `/mapas/calcular-rota/`. Disponibilidade e politicas dos provedores externos devem ser verificadas.
- Municipios IBGE: comando `importar_municipios_ibge`.
- Mapas: Leaflet; ativos remotos e provedores de tiles dependem de conectividade e configuracao.
- Correio: SMTP configuravel por ambiente. Identidade: Django auth e LDAP/Active Directory opcional.
- Dependencias e versoes oficiais: `requirements.txt`; PostgreSQL, psycopg, Gunicorn, WhiteNoise e WeasyPrint. SQLite de teste e exclusivamente local.

Detalhes de autorizacao e cutover: [Central de Permissoes](docs/central_permissoes.md). Nunca publique credenciais ou arquivos .env.
