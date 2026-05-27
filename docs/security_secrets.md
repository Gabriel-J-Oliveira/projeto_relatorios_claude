# Segredos, banco e variaveis de ambiente

Este guia documenta a politica tecnica inicial para credenciais, configuracoes sensiveis e dados locais da aplicacao.

## Segredos fora do codigo

Devem existir apenas em variaveis de ambiente, `.env` local protegido ou secret manager corporativo:

- `SECRET_KEY`;
- credenciais PostgreSQL;
- senha SMTP;
- credenciais LDAP/AD;
- tokens e chaves de APIs futuras;
- caminhos sensiveis de storage, logs e backups quando aplicavel.

Arquivos `.env` reais nao devem ser commitados. O repositório deve conter apenas `.env.example`, sem valores reais.

## Variaveis principais

Banco:

```env
DATABASE_URL=
POSTGRES_DB=
POSTGRES_USER=
POSTGRES_PASSWORD=
POSTGRES_HOST=
POSTGRES_PORT=
```

O settings de producao aceita `DATABASE_URL` ou o conjunto `POSTGRES_*`. Tambem existem aliases `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST` e `DB_PORT` para compatibilidade operacional.

Email:

```env
EMAIL_HOST=
EMAIL_PORT=
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
EMAIL_USE_TLS=
DEFAULT_FROM_EMAIL=
```

LDAP:

```env
LDAP_SERVER_URI=
LDAP_SERVER_URIS=
LDAP_BIND_DN=
LDAP_BIND_PASSWORD=
LDAP_USER_SEARCH_BASE_DN=
LDAP_GROUP_SEARCH_BASE_DN=
```

Seguranca HTTP:

```env
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
SECURE_SSL_REDIRECT=True
SECURE_HSTS_SECONDS=31536000
```

Ative HTTPS/HSTS somente depois de validar TLS no proxy reverso.

## Banco de dados

Recomendacao para producao:

- banco dedicado para a aplicacao;
- usuario dedicado, sem superuser;
- permissao apenas no database/schema da aplicacao;
- senha forte e armazenada fora do Git;
- conexao com `CONN_MAX_AGE` configurado;
- backups regulares e restore testado.

Exemplo conceitual de permissao minima:

```sql
CREATE USER app_relatorios_user WITH PASSWORD '<senha-forte>';
CREATE DATABASE app_relatorios OWNER app_relatorios_user;
GRANT CONNECT ON DATABASE app_relatorios TO app_relatorios_user;
GRANT USAGE, CREATE ON SCHEMA public TO app_relatorios_user;
```

Nao use usuario `postgres` ou outro superuser como usuario da aplicacao.

## Arquivos locais sensiveis

Nao versionar:

- `.env`;
- bancos locais como `db.sqlite3`;
- cookies ou dumps de browser;
- anexos/comprovantes;
- logs;
- backups `.dump`, `.backup`, `.bak`;
- arquivos temporarios de PDF/ZIP.

Diretorios recomendados:

```env
ANEXOS_ROOT=/home/app_relatorios_files
APP_LOG_DIR=/home/app_relatorios_logs
BACKUP_DIR=/home/app_relatorios_backups
```

Esses diretorios devem ter permissao restrita ao usuario do servico.

## Logs

Nunca registrar:

- senha do banco, SMTP ou LDAP;
- `SECRET_KEY`;
- cookies;
- tokens;
- headers `Authorization`;
- payload completo com dados sensiveis;
- conteudo de anexos ou PDFs.

Quando uma credencial precisar aparecer em diagnostico, registre apenas mascara:

```text
password=****
token=****
secret=****
```

## Arquivos sensiveis encontrados

Na revisao atual foram identificados como rastreados pelo Git:

- `db.sqlite3`;
- `app_relatorios/settings/cookies.txt`.

Eles devem ser removidos do versionamento em uma rotina controlada e tratados como potencialmente sensiveis. Isso nao remove o historico Git; se os dados forem reais, considere rotacionar tokens/sessoes e limpar historico conforme politica interna.

## Checks de producao

Executar antes do deploy:

```bash
python manage.py check
python manage.py check --deploy
```

`check --deploy` pode gerar warnings esperados quando HTTPS/HSTS ainda nao estiver ativo no ambiente. Avalie cada warning antes de liberar producao.

## Auditoria de dependencias

Opcionalmente:

```bash
python -m pip install pip-audit
pip-audit
```

Nao atualize pacotes automaticamente em producao sem validar compatibilidade.
