# Homologação da geocodificação

No `.env` privado (ignorado pelo Git), configure:

```ini
MAPS_GEOCODE_PROVIDER=geoapify
GEOAPIFY_API_KEY=
GEOAPIFY_DAILY_LIMIT=2700
MAPS_REDIS_URL=redis://:SENHA@127.0.0.1:6379/2
```

Use Redis autenticado e compartilhado por todos os workers, com persistência e
sem política de descarte de chaves da cota. O alias Django `geocoding` não
substitui o cache padrão do ERP. A cota reserva até cinco chamadas por janela
móvel de um segundo e usa o dia UTC. Se Redis não estiver disponível, o
formulário continua aceitando o endereço e KM manual.

Compare os provedores sem tocar em relatórios existentes:

```powershell
$env:LDAP_AUTH_ENABLED="False"
.\venv\Scripts\python.exe manage.py diagnostico_geocodificacao --settings=app_relatorios.settings.test_sqlite
```

O comando usa o backend, consome créditos e não registra a chave. Use
`--endereco` para limitar a amostra. Nominatim só recebe buscas explícitas;
não é fallback automático para a digitação.

No formulário, confirme cada ponta por uma sugestão ou por clique no mapa.
Resultados aproximados e de outros municípios são sinalizados, não excluídos.
Ao editar um trecho histórico, o mapa pode mostrar os dados salvos; uma nova
rota exige confirmar novamente as duas pontas. A busca por nomes depende da
cobertura dos dados do provedor. Links curtos do Google Maps não são seguidos.

## Regressão conhecida fora do escopo

`RelatorioTecnicoFlowTests.test_duplica_relatorio_com_linhas_sem_datas_e_sem_dados_financeiros`
já falha no baseline limpo `b7cd08a`: espera R$ 2,50/km herdados do trecho
original, mas a duplicação usa a tarifa padrão de R$ 1,35/km. A integração de
mapas não altera essa regra. A preservação das coordenadas e do KM na cópia
tem um teste separado.
