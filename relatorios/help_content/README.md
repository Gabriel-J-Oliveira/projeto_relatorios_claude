# Central de Ajuda

Os artigos ficam em Markdown dentro de `relatorios/help_content/` e aparecem no sistema conforme o cadastro em `index.json`.

Também existe a camada editável em banco para administradores. Para carregar os arquivos atuais no banco, use:

```bash
python manage.py importar_artigos_ajuda
```

O conteúdo em banco sobrescreve o artigo de mesmo slug; os arquivos continuam como fallback/backup.

## Imagens

Salve imagens em:

`static/help/images/`

Use PNG ou JPG, preferencialmente com largura entre 1200px e 1600px para prints de tela.

Referencie no artigo com Markdown:

```md
![Tela de cadastro do relatório](/static/help/images/cadastro-relatorio-dados-gerais.png)
```

Quando a imagem ainda não existir, deixe um placeholder claro:

```md
[INSERIR IMAGEM: Tela de cadastro do relatório - Dados gerais]
```

O template renderiza imagens de forma responsiva, com borda suave e sombra leve.

Administradores também podem enviar imagens pelo editor visual do artigo. Essas imagens são salvas em `HELP_IMAGES_ROOT` (padrão: `/home/app_relatorios_files/help_images`) e servidas por endpoint autenticado da Central de Ajuda. Formatos aceitos: PNG, JPG, JPEG e WEBP.
