# Sincronizacao de Clientes ControlSul

O sistema usa a base local de `Cliente` para cadastro de relatorios. A API externa da ControlSul e usada apenas para sincronizar dados cadastrais periodicamente.

## Variaveis de ambiente

```env
CLIENTES_API_URL=https://api.controlsul.com/clients
CLIENTES_API_TOKEN=
CLIENTES_API_TIMEOUT=30
CLIENTES_API_ENABLED=True
```

Nunca registre nem versione o token real.

## Campos sincronizados

Chave de integracao: `cnpj_cpf`.

Campos atualizados pela API:

- `cnpj_cpf`
- `razao_social`
- `nome_fantasia`
- `nome`
- `cep`
- `uf`
- `cidade`
- `logradouro`
- `numero`
- `bairro`
- `complemento`
- `telefone`
- `ativo`
- `api_created_at`
- `api_updated_at`
- `sincronizado_em`
- `origem_api`
- `hash_dados_api`

Campos locais preservados:

- `valor_km`
- `valor_km_atualizado_em`
- `valor_km_atualizado_por`
- `valor_km_observacao`
- `email`
- `contato`
- regras, observacoes e classificacoes internas
- vinculos historicos com relatorios

Clientes inativos na API sao marcados como `ativo=False`. Clientes locais nunca sao apagados automaticamente.

Clientes novos vindos da API entram com `valor_km` vazio. Esse valor e informacao local do sistema de reembolso e deve ser preenchido pelo Financeiro/Admin.

## Comandos

Teste sem gravar:

```bash
python manage.py sincronizar_clientes_api --dry-run
```

Sincronizacao real:

```bash
python manage.py sincronizar_clientes_api
```

Opcoes uteis:

```bash
python manage.py sincronizar_clientes_api --limit 20 --dry-run
python manage.py sincronizar_clientes_api --force
python manage.py sincronizar_clientes_api --verbose
```

Diagnostico para clientes locais/legados:

```bash
python manage.py vincular_clientes_locais_api
python manage.py vincular_clientes_locais_api --mostrar-sem-correspondencia
```

Importar tabela manual/legada de valor KM com matching assistido:

```bash
python manage.py importar_valor_km_clientes caminho/arquivo.csv --dry-run
python manage.py importar_valor_km_clientes caminho/arquivo.csv --confirmar
```

CSV aceito:

```csv
cnpj_cpf,valor_km
80386923000162,1.85
```

ou:

```csv
cliente,valor_km
AGROPECUARIA VALE DO CABACAL S/A,1.85
```

Opcoes principais:

```bash
python manage.py importar_valor_km_clientes tabela.csv --dry-run --limite 20
python manage.py importar_valor_km_clientes tabela.csv --confirmar --threshold-auto 95 --threshold-pendente 85
python manage.py importar_valor_km_clientes tabela.csv --confirmar --sobrescrever
python manage.py importar_valor_km_clientes tabela.csv --confirmar --mapeamento imports/valor_km/mapeamento_valor_km_clientes.csv
```

Regras:

- sem `--confirmar`, nada e gravado;
- `--dry-run` gera os relatorios sem alterar o banco;
- por padrao, cliente que ja possui `valor_km` nao e sobrescrito;
- `--sobrescrever` permite alterar valor existente;
- match automatico ocorre por CNPJ/CPF, nome normalizado exato ou fuzzy com score alto;
- matches duvidosos ficam para revisao manual;
- clientes novos nao sao criados por esse command.

Arquivos gerados em `imports/valor_km/saida/`:

- `valor_km_matches_automaticos.csv`
- `valor_km_pendentes_revisao.csv`
- `valor_km_nao_encontrados.csv`
- `valor_km_resultado_importacao.csv`

Mapeamento manual opcional:

```csv
cliente_csv,cliente_id,valor_km
AGRÍCOLA URTIGÃO,123,1.68
```

## Agendamento

A API externa pode ficar offline diariamente entre 20:30 e 07:30. Isso nao impede o uso do sistema, porque os relatorios usam a base local.

Exemplo de cron de hora em hora:

```cron
0 * * * * /caminho/venv/bin/python /caminho/projeto/manage.py sincronizar_clientes_api >> /home/app_relatorios_logs/clientes_sync_cron.log 2>&1
```

Se a API estiver offline, o command falha de forma controlada, registra log e preserva a base local.

## Logs

Loggers usados:

- `relatorios.services.clientes_api_service`
- `relatorios.services.clientes_sync_service`

Eventos registrados:

- inicio e fim da sincronizacao
- totais criados, atualizados, inativados e ignorados
- API offline, timeout ou credencial invalida
- cliente invalido ignorado

O token e o header `Authorization` nao devem aparecer nos logs.

## Uso no sistema

A selecao de clientes em relatorios continua usando somente a base local. A busca considera:

- nome fantasia
- razao social
- nome legado
- CNPJ/CPF
- cidade
- UF

Clientes inativos nao aparecem para novos relatorios, mas relatorios antigos continuam abrindo normalmente.

Clientes ativos sem `valor_km`:

- aparecem em alerta/modal para Financeiro/Admin;
- nao aparecem para Tecnico como configuracao editavel;
- bloqueiam envio/aprovacao de relatorios ate o preenchimento;
- nao recebem fallback silencioso para o valor KM ControlSul.
