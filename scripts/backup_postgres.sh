#!/usr/bin/env bash
set -euo pipefail

# Backup seguro do PostgreSQL em formato custom do pg_dump.
# Requer variaveis de ambiente carregadas pelo servico/shell, sem imprimir senha.

BACKUP_DIR="${BACKUP_DIR:-/home/app_relatorios_backups}"
DB_NAME="${POSTGRES_DB:-${DB_NAME:-}}"
DB_USER="${POSTGRES_USER:-${DB_USER:-}}"
DB_HOST="${POSTGRES_HOST:-${DB_HOST:-127.0.0.1}}"
DB_PORT="${POSTGRES_PORT:-${DB_PORT:-5432}}"

if [[ -z "${DB_NAME}" ]]; then
  echo "Erro: defina POSTGRES_DB ou DB_NAME." >&2
  exit 1
fi

if [[ -z "${DB_USER}" ]]; then
  echo "Erro: defina POSTGRES_USER ou DB_USER." >&2
  exit 1
fi

if ! command -v pg_dump >/dev/null 2>&1; then
  echo "Erro: pg_dump nao encontrado no PATH." >&2
  exit 1
fi

mkdir -p "${BACKUP_DIR}"
chmod 750 "${BACKUP_DIR}" 2>/dev/null || true

timestamp="$(date +%Y%m%d_%H%M%S)"
destino="${BACKUP_DIR}/backup_app_relatorios_${timestamp}.dump"

echo "Iniciando backup do banco ${DB_NAME} em ${destino}"
pg_dump \
  --format=custom \
  --no-owner \
  --no-acl \
  --host="${DB_HOST}" \
  --port="${DB_PORT}" \
  --username="${DB_USER}" \
  --file="${destino}" \
  "${DB_NAME}"

chmod 640 "${destino}" 2>/dev/null || true
echo "Backup concluido: ${destino}"
