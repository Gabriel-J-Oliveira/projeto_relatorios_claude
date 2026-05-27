# Backup e restauracao do PostgreSQL

Este guia cobre backup manual do banco da aplicacao. Ele nao configura agenda automatica; em producao, use `cron`, `systemd timer` ou ferramenta corporativa de backup.

## Variaveis necessarias

O script usa variaveis de ambiente. Ele nao imprime senha e depende do mecanismo padrao do PostgreSQL para autenticacao (`PGPASSWORD`, `.pgpass` seguro, socket local ou credenciais do ambiente do servico).

```env
POSTGRES_DB=app_relatorios
POSTGRES_USER=app_relatorios_user
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
BACKUP_DIR=/home/app_relatorios_backups
```

Alternativamente, podem ser usados os aliases `DB_NAME`, `DB_USER`, `DB_HOST` e `DB_PORT`.

## Gerar backup

No servidor:

```bash
cd /caminho/do/projeto
set -a
. ./.env
set +a
./scripts/backup_postgres.sh
```

O arquivo gerado segue o padrao:

```text
backup_app_relatorios_YYYYMMDD_HHMMSS.dump
```

Formato usado: `pg_dump --format=custom`, adequado para `pg_restore`.

## Permissoes recomendadas

```bash
sudo mkdir -p /home/app_relatorios_backups
sudo chown -R <usuario_app>:<grupo_app> /home/app_relatorios_backups
sudo chmod 750 /home/app_relatorios_backups
```

Arquivos de backup devem ficar com permissao restrita, por exemplo `640`, e nunca devem ser servidos por Nginx.

## Restaurar em ambiente de teste

Antes de restaurar em producao, valide em homologacao/teste:

```bash
createdb -h 127.0.0.1 -U app_relatorios_user app_relatorios_restore_test
pg_restore \
  --host=127.0.0.1 \
  --port=5432 \
  --username=app_relatorios_user \
  --dbname=app_relatorios_restore_test \
  --clean \
  --if-exists \
  /home/app_relatorios_backups/backup_app_relatorios_YYYYMMDD_HHMMSS.dump
```

Depois rode:

```bash
python manage.py check
python manage.py migrate --check
```

## Restaurar em producao

Cuidados obrigatorios:

- comunicar janela de manutencao;
- parar Gunicorn/workers antes da restauracao;
- garantir backup recente antes de sobrescrever;
- restaurar primeiro em teste quando possivel;
- conferir owner/permissoes do banco;
- subir aplicacao e validar login, listagens, anexos, PDFs e dashboard.

Exemplo:

```bash
sudo systemctl stop gunicorn
pg_restore \
  --host=127.0.0.1 \
  --port=5432 \
  --username=app_relatorios_user \
  --dbname=app_relatorios \
  --clean \
  --if-exists \
  /home/app_relatorios_backups/backup_app_relatorios_YYYYMMDD_HHMMSS.dump
python manage.py migrate --check
python manage.py check
sudo systemctl start gunicorn
```

## Retencao recomendada

- backup diario;
- retencao minima entre 7 e 30 dias;
- copia fora do servidor em etapa futura;
- teste periodico de restauracao;
- acesso restrito ao usuario operacional e administradores autorizados.

## Observacoes

Backups contem dados pessoais, valores financeiros e metadados de relatorios. Trate os arquivos como dado sensivel. Nao envie por email sem criptografia e nao armazene em diretorios publicos.
