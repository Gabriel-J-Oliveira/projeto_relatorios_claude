# Hardening de seguranca da aplicacao

Este documento resume as protecoes aplicadas na camada Django. Ele nao substitui configuracoes de Nginx, firewall, backup, monitoramento ou politicas corporativas de seguranca.

## Endpoints sensiveis

As rotas que alteram estado devem usar `POST` com CSRF ativo. Isso inclui envio para conferencia, aprovacao, rejeicao, solicitacao de ajuste, rejeicao/reativacao de item, alteracao de valores financeiros, rateios e duplicacao de relatorio.

Rotas de leitura sensivel usam `GET` autenticado e validacao de escopo, incluindo:

- preview e download de anexos;
- comprovantes de despesas;
- PDF interno;
- PDF individual de cliente;
- ZIP com PDFs dos clientes;
- dashboard JSON;
- endpoints de mapas.

O backend valida ownership e escopo via services/helpers de autorizacao. Tecnicos acessam apenas relatorios proprios ou dos quais participam; financeiro/admin acessam conforme perfil; Domain Admin e superadmin possuem acesso global quando reconhecidos pelos helpers existentes.

## Uploads permitidos

Novos uploads aceitos:

- PDF: `.pdf`, `application/pdf`, assinatura `%PDF`;
- JPG/JPEG: `.jpg`/`.jpeg`, `image/jpeg`, assinatura JPEG;
- PNG: `.png`, `image/png`, assinatura PNG.

Tambem sao bloqueados:

- arquivo vazio;
- extensao nao permitida;
- MIME type nao permitido;
- arquivo acima de `ANEXO_MAX_UPLOAD_MB`;
- arquivo com extensao permitida mas assinatura incompativel.

Arquivos antigos ja persistidos continuam editaveis para compatibilidade, mas uploads novos passam pela validacao reforcada.

## Arquivos protegidos

Anexos e comprovantes nao devem ser expostos diretamente como fonte de autorizacao. As views de preview/download validam:

- usuario autenticado;
- permissao no relatorio;
- relacao entre anexo/despesa e relatorio;
- existencia do arquivo;
- tipo permitido para inline.

Visualizacao usa `Content-Disposition: inline`; download usa `attachment`. Quando o tipo nao e permitido para preview, o sistema forca download e `application/octet-stream`.

## XSS e conteudo renderizado

Dados digitados por usuario devem permanecer escapados pelo Django Template. Evite `|safe` e `mark_safe` com dados de usuario, especialmente em descricoes, observacoes, motivos, justificativas, historico e nomes de arquivos.

A Central de Ajuda usa conteudo versionado no projeto. Mesmo assim, nao deve receber HTML/script vindo de usuario.

## Variaveis de ambiente

Variaveis recomendadas para producao com HTTPS:

```env
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
SESSION_COOKIE_HTTPONLY=True
SESSION_COOKIE_SAMESITE=Lax
CSRF_COOKIE_SAMESITE=Lax
SECURE_CONTENT_TYPE_NOSNIFF=True
X_FRAME_OPTIONS=DENY
SECURE_REFERRER_POLICY=same-origin
SECURE_SSL_REDIRECT=True
SECURE_HSTS_SECONDS=31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS=True
SECURE_HSTS_PRELOAD=True
ANEXO_MAX_UPLOAD_MB=10
```

Ative `SECURE_SSL_REDIRECT` e HSTS somente quando HTTPS estiver definitivo e validado no proxy reverso.

## Logs de seguranca

Eventos bloqueados sao registrados no logger `security`, direcionado para `security.log` quando `APP_LOG_DIR` estiver gravavel.

Registrar apenas:

- id/username do usuario;
- id do relatorio;
- endpoint;
- metodo HTTP;
- IP;
- acao tentada;
- motivo do bloqueio.

Nunca registrar senha, cookies, tokens, headers de autorizacao, conteudo de arquivo, PDF completo, SMTP, LDAP ou string de conexao do banco.

## Validacoes manuais recomendadas

- tecnico tentando abrir/editar relatorio de outro usuario;
- tecnico tentando aprovar/rejeitar via POST manual;
- tecnico tentando alterar valor financeiro/rateio;
- usuario tentando baixar anexo de relatorio fora do escopo;
- usuario tentando gerar PDF cliente de cliente nao vinculado;
- upload PDF/JPG/PNG valido;
- upload DOCX/XLSX/EXE/arquivo vazio bloqueado;
- POST sensivel sem CSRF bloqueado;
- dashboard JSON sem vazamento global para tecnico;
- campos com HTML/script continuam escapados.

## Proximos passos

- testes automatizados de autorizacao por endpoint;
- varredura com ferramenta SAST;
- politica de antivirus/clamav para arquivos anexados;
- rate limiting nos endpoints de mapas/login;
- revisao de CSP quando todos os assets externos forem estabilizados.
