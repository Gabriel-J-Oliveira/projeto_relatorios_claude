# Central de Ajuda

Os artigos ficam em Markdown dentro de `relatorios/help_content/` e aparecem no sistema conforme o cadastro em `index.json`.

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
