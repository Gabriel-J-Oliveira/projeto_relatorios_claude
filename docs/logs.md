# Logs técnicos do ERP de relatórios

Os logs técnicos/operacionais ficam separados da auditoria de negócio (`HistoricoRelatorio`). Eles servem para diagnóstico de produção, troubleshooting e operação.

## Diretório

Produção:

```bash
/home/app_relatorios_logs
```

Variáveis:

```env
APP_LOG_DIR=/home/app_relatorios_logs
APP_LOG_LEVEL=INFO
```

Se `APP_LOG_DIR` não puder ser criado ou escrito, o Django mantém fallback para console, evitando quebrar o carregamento da aplicação.

## Permissões Linux

Crie o diretório no servidor e dê permissão ao usuário do Gunicorn:

```bash
sudo mkdir -p /home/app_relatorios_logs
sudo chown -R <usuario_app>:<grupo_app> /home/app_relatorios_logs
sudo chmod 750 /home/app_relatorios_logs
```

## Arquivos gerados

- `app_relatorios.log`: eventos técnicos gerais da aplicação.
- `errors.log`: erros reais e exceptions.
- `emails.log`: envio de emails internos e falhas SMTP.
- `maps.log`: Nominatim, OSRM, geocoding e cálculo de rotas.
- `pdfs.log`: geração de PDFs, ZIPs e falhas WeasyPrint.
- `security.log`: autenticação, LDAP, autorização e acessos negados.

## Rotação

O Django usa `RotatingFileHandler` como proteção inicial:

- `maxBytes`: 10 MB
- `backupCount`: 10

Em produção, complemente com `logrotate` ou retenção via systemd/journald conforme a política da infraestrutura.

## Segurança

Não registrar:

- senhas SMTP, LDAP ou banco;
- tokens, cookies ou headers `Authorization`;
- PDFs, anexos ou base64;
- conteúdo sensível desnecessário.

Quando necessário, prefira registrar apenas ID do relatório, número, ID do usuário, tipo de operação e mensagem resumida.
