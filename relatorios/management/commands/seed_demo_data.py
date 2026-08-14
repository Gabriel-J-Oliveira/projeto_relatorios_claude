import hashlib
import json
import random
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from relatorios.models import (
    Adiantamento,
    Cliente,
    DespesaCliente,
    DespesaRateio,
    HistoricoRelatorio,
    ItemDespesa,
    QuemPagou,
    RelatorioCliente,
    RelatorioSnapshotFinanceiro,
    RelatorioTecnico,
    RelatorioTecnicoEquipe,
    StatusFinanceiroItem,
    StatusRateio,
    StatusRelatorio,
    Tecnico,
    TipoAdiantamento,
    TipoDespesa,
    TipoEventoHistorico,
    TipoLocalidade,
    TipoRelatorio,
    TrechoKMCliente,
    TrechoKm,
    TrechoRateioKM,
    UF,
)


PREFIXO = "DEMO"
QTD_RELATORIOS = 50


def money(valor) -> Decimal:
    return Decimal(valor or "0.00").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def dec(valor, casas="0.01") -> Decimal:
    return Decimal(str(valor)).quantize(Decimal(casas), rounding=ROUND_HALF_UP)


def dividir_valor(valor: Decimal, partes: int):
    valor = money(valor)
    if partes <= 0:
        return []

    centavos = int((valor * 100).to_integral_value())
    base = centavos // partes
    resto = centavos % partes

    resultado = []
    for i in range(partes):
        c = base + (1 if i < resto else 0)
        resultado.append(Decimal(c) / Decimal("100"))

    return resultado


def porcentagem_diferenca(km_api: Decimal, km_informado: Decimal):
    if not km_api or km_api <= 0:
        return None
    return ((abs(km_informado - km_api) / km_api) * Decimal("100")).quantize(
        Decimal("0.01")
    )


def rota_geojson(origem_lon, origem_lat, destino_lon, destino_lat):
    return {
        "type": "LineString",
        "coordinates": [
            [float(origem_lon), float(origem_lat)],
            [float(destino_lon), float(destino_lat)],
        ],
    }


CLIENTES_BASE = [
    ("DEMO Coopavel Cooperativa Agroindustrial", "Cascavel", UF.PR, "1.45"),
    ("DEMO Lar Cooperativa Agroindustrial", "Medianeira", UF.PR, "1.55"),
    ("DEMO Copagril Agroindustrial", "Marechal Cândido Rondon", UF.PR, "1.62"),
    ("DEMO Frimesa Cooperativa Central", "Medianeira", UF.PR, "1.48"),
    ("DEMO Integrada Cooperativa Agroindustrial", "Londrina", UF.PR, "1.70"),
    ("DEMO C. Vale Cooperativa", "Palotina", UF.PR, "1.66"),
    ("DEMO Agrícola Urtigão", "Campo Mourão", UF.PR, "1.50"),
    ("DEMO Control Sul Gestão Empresarial", "Curitiba", UF.PR, "1.80"),
    ("DEMO Transportes Oeste", "Toledo", UF.PR, "1.42"),
    ("DEMO Indústria Alimentar Paraná", "Maringá", UF.PR, "1.58"),
    ("DEMO AgroTech Sul", "Pato Branco", UF.PR, "1.73"),
    ("DEMO Cooperativa Vale Verde", "Guarapuava", UF.PR, "1.60"),
    ("DEMO Bioenergia Paraná", "Umuarama", UF.PR, "1.68"),
    ("DEMO Sementes Horizonte", "Francisco Beltrão", UF.PR, "1.52"),
    ("DEMO Logística Planalto", "Ponta Grossa", UF.PR, "1.64"),
    ("DEMO Agroindustrial Norte", "Apucarana", UF.PR, "1.59"),
    ("DEMO Grãos Brasil Sul", "Cianorte", UF.PR, "1.57"),
    ("DEMO Pecuária Campos Gerais", "Castro", UF.PR, "1.61"),
    ("DEMO Cooperativa São Miguel", "São Miguel do Iguaçu", UF.PR, "1.54"),
    ("DEMO Fertilizantes Oeste", "Assis Chateaubriand", UF.PR, "1.69"),
    ("DEMO Máquinas Agrícolas Paraná", "Cascavel", UF.PR, "1.75"),
    ("DEMO Frigorífico Rio Verde", "Toledo", UF.PR, "1.47"),
    ("DEMO Cereais Campo Mourão", "Campo Mourão", UF.PR, "1.56"),
    ("DEMO Alimentos Cataratas", "Foz do Iguaçu", UF.PR, "1.63"),
    ("DEMO Tecnologia Rural Sul", "Curitiba", UF.PR, "1.82"),
]


TECNICOS_BASE = [
    ("DEMO João Silva", "demo.joao.silva@example.com"),
    ("DEMO Maria Souza", "demo.maria.souza@example.com"),
    ("DEMO Carlos Lima", "demo.carlos.lima@example.com"),
    ("DEMO Ana Pereira", "demo.ana.pereira@example.com"),
    ("DEMO Rafael Martins", "demo.rafael.martins@example.com"),
    ("DEMO Camila Rocha", "demo.camila.rocha@example.com"),
    ("DEMO Bruno Almeida", "demo.bruno.almeida@example.com"),
    ("DEMO Fernanda Costa", "demo.fernanda.costa@example.com"),
    ("DEMO Lucas Ferreira", "demo.lucas.ferreira@example.com"),
    ("DEMO Patricia Gomes", "demo.patricia.gomes@example.com"),
]


ROTAS_BASE = [
    {
        "origem": "Cascavel/PR",
        "destino": "Toledo/PR",
        "km": "46.20",
        "origem_lat": "-24.9555",
        "origem_lon": "-53.4552",
        "destino_lat": "-24.7136",
        "destino_lon": "-53.7431",
    },
    {
        "origem": "Toledo/PR",
        "destino": "Marechal Cândido Rondon/PR",
        "km": "38.40",
        "origem_lat": "-24.7136",
        "origem_lon": "-53.7431",
        "destino_lat": "-24.5570",
        "destino_lon": "-54.0568",
    },
    {
        "origem": "Cascavel/PR",
        "destino": "Foz do Iguaçu/PR",
        "km": "140.00",
        "origem_lat": "-24.9555",
        "origem_lon": "-53.4552",
        "destino_lat": "-25.5163",
        "destino_lon": "-54.5854",
    },
    {
        "origem": "Curitiba/PR",
        "destino": "Ponta Grossa/PR",
        "km": "115.00",
        "origem_lat": "-25.4284",
        "origem_lon": "-49.2733",
        "destino_lat": "-25.0945",
        "destino_lon": "-50.1633",
    },
    {
        "origem": "Maringá/PR",
        "destino": "Campo Mourão/PR",
        "km": "92.00",
        "origem_lat": "-23.4205",
        "origem_lon": "-51.9331",
        "destino_lat": "-24.0431",
        "destino_lon": "-52.3789",
    },
    {
        "origem": "Londrina/PR",
        "destino": "Apucarana/PR",
        "km": "58.00",
        "origem_lat": "-23.3045",
        "origem_lon": "-51.1696",
        "destino_lat": "-23.5508",
        "destino_lon": "-51.4600",
    },
    {
        "origem": "Guarapuava/PR",
        "destino": "Pato Branco/PR",
        "km": "250.00",
        "origem_lat": "-25.3902",
        "origem_lon": "-51.4623",
        "destino_lat": "-26.2292",
        "destino_lon": "-52.6706",
    },
    {
        "origem": "Francisco Beltrão/PR",
        "destino": "Cascavel/PR",
        "km": "180.00",
        "origem_lat": "-26.0817",
        "origem_lon": "-53.0535",
        "destino_lat": "-24.9555",
        "destino_lon": "-53.4552",
    },
    {
        "origem": "Umuarama/PR",
        "destino": "Cianorte/PR",
        "km": "85.00",
        "origem_lat": "-23.7656",
        "origem_lon": "-53.3201",
        "destino_lat": "-23.6633",
        "destino_lon": "-52.6050",
    },
    {
        "origem": "Castro/PR",
        "destino": "Ponta Grossa/PR",
        "km": "45.00",
        "origem_lat": "-24.7891",
        "origem_lon": "-50.0108",
        "destino_lat": "-25.0945",
        "destino_lon": "-50.1633",
    },
]


TIPOS_DESPESA = [
    TipoDespesa.ALIMENTACAO,
    TipoDespesa.HOSPEDAGEM,
    TipoDespesa.COMBUSTIVEL,
    TipoDespesa.PEDAGIO,
    TipoDespesa.ESTACIONAMENTO,
    TipoDespesa.TRANSPORTE,
    TipoDespesa.MATERIAL,
    TipoDespesa.OUTROS,
]


DESCRICOES_DESPESA = {
    TipoDespesa.ALIMENTACAO: [
        "Almoço em atendimento externo",
        "Jantar durante viagem técnica",
        "Refeição em deslocamento",
    ],
    TipoDespesa.HOSPEDAGEM: [
        "Hospedagem para atendimento técnico",
        "Diária de hotel",
        "Hotel próximo ao cliente",
    ],
    TipoDespesa.COMBUSTIVEL: [
        "Abastecimento em deslocamento",
        "Combustível para visita técnica",
    ],
    TipoDespesa.PEDAGIO: [
        "Pedágio rodoviário",
        "Tarifa de pedágio",
    ],
    TipoDespesa.ESTACIONAMENTO: [
        "Estacionamento em cliente",
        "Estacionamento durante visita",
    ],
    TipoDespesa.TRANSPORTE: [
        "Transporte local",
        "Aplicativo de transporte",
    ],
    TipoDespesa.MATERIAL: [
        "Material auxiliar para atendimento",
        "Ferramenta emergencial",
    ],
    TipoDespesa.OUTROS: [
        "Despesa operacional complementar",
        "Custo administrativo de viagem",
    ],
}


def valor_despesa_por_tipo(tipo):
    if tipo == TipoDespesa.HOSPEDAGEM:
        return money(random.randint(280, 980))
    if tipo == TipoDespesa.COMBUSTIVEL:
        return money(random.randint(120, 420))
    if tipo == TipoDespesa.PEDAGIO:
        return money(random.randint(15, 140))
    if tipo == TipoDespesa.ALIMENTACAO:
        return money(random.randint(35, 180))
    if tipo == TipoDespesa.ESTACIONAMENTO:
        return money(random.randint(15, 80))
    if tipo == TipoDespesa.TRANSPORTE:
        return money(random.randint(30, 220))
    if tipo == TipoDespesa.MATERIAL:
        return money(random.randint(80, 550))
    return money(random.randint(40, 300))


class Command(BaseCommand):
    help = "Popula o banco com dados fictícios para apresentação do sistema."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Remove dados DEMO anteriores antes de criar novos.",
        )
        parser.add_argument(
            "--relatorios",
            type=int,
            default=QTD_RELATORIOS,
            help="Quantidade de relatórios DEMO a criar.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        random.seed(42)

        if options["reset"]:
            self.reset_demo_data()

        users, financeiro_user = self.criar_usuarios_demo()
        tecnicos = self.criar_tecnicos_demo()
        clientes = self.criar_clientes_demo()

        qtd = options["relatorios"]
        self.stdout.write(self.style.NOTICE(f"Criando {qtd} relatórios DEMO..."))

        for idx in range(1, qtd + 1):
            self.criar_relatorio_demo(
                idx=idx,
                users=users,
                financeiro_user=financeiro_user,
                tecnicos=tecnicos,
                clientes=clientes,
            )

        self.stdout.write(self.style.SUCCESS("Dados DEMO criados com sucesso!"))
        self.stdout.write("")
        self.stdout.write("Sugestão de execução:")
        self.stdout.write("  python manage.py seed_demo_data --reset")
        self.stdout.write("")
        self.stdout.write("Foram criados dados para dashboard, mapas, PDFs, rateio e workflow.")

    def reset_demo_data(self):
        self.stdout.write(self.style.WARNING("Removendo dados DEMO anteriores..."))

        RelatorioTecnico.objects.filter(centro_custo__startswith=PREFIXO).delete()
        Adiantamento.objects.filter(descricao__startswith=PREFIXO).delete()

        Tecnico.objects.filter(nome__startswith=f"{PREFIXO} ").delete()
        Cliente.objects.filter(nome__startswith=f"{PREFIXO} ").delete()

        User = get_user_model()
        User.objects.filter(username__startswith="demo.").delete()
        User.objects.filter(username="demo.financeiro").delete()

        self.stdout.write(self.style.SUCCESS("Dados DEMO anteriores removidos."))

    def criar_usuarios_demo(self):
        User = get_user_model()
        users = {}

        for nome, email in TECNICOS_BASE:
            username = email.split("@")[0]
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "email": email,
                    "first_name": nome.replace("DEMO ", "").split()[0],
                    "last_name": " ".join(nome.replace("DEMO ", "").split()[1:]),
                    "is_staff": False,
                    "is_superuser": False,
                },
            )
            if created:
                user.set_password("demo12345")
                user.save(update_fields=["password"])
            users[email] = user

        financeiro, created = User.objects.get_or_create(
            username="demo.financeiro",
            defaults={
                "email": "demo.financeiro@example.com",
                "first_name": "DEMO",
                "last_name": "Financeiro",
                "is_staff": True,
                "is_superuser": False,
            },
        )
        if created:
            financeiro.set_password("demo12345")
            financeiro.save(update_fields=["password"])

        return users, financeiro

    def criar_tecnicos_demo(self):
        tecnicos = []

        for nome, email in TECNICOS_BASE:
            tecnico, _created = Tecnico.objects.get_or_create(
                email=email,
                defaults={
                    "nome": nome,
                    "telefone": f"(45) 9{random.randint(1000, 9999)}-{random.randint(1000, 9999)}",
                    "ativo": True,
                },
            )
            tecnicos.append(tecnico)

        return tecnicos

    def criar_clientes_demo(self):
        clientes = []

        for idx, (nome, cidade, uf, valor_km) in enumerate(CLIENTES_BASE, start=1):
            cliente, _created = Cliente.objects.get_or_create(
                nome=nome,
                defaults={
                    "cnpj_cpf": f"99.999.{idx:03d}/0001-{idx % 97:02d}",
                    "cidade": cidade,
                    "uf": uf,
                    "contato": "Departamento Administrativo",
                    "telefone": f"(45) 3{random.randint(100, 999)}-{random.randint(1000, 9999)}",
                    "email": f"{slugify(nome.replace('DEMO ', ''))}@demo.com.br",
                    "ativo": True,
                    "valor_km": Decimal(valor_km),
                },
            )
            clientes.append(cliente)

        return clientes

    def criar_relatorio_demo(self, idx, users, financeiro_user, tecnicos, clientes):
        hoje = timezone.localdate()
        data_inicio = hoje - timedelta(days=random.randint(1, 90))
        data_fim = data_inicio + timedelta(days=random.randint(0, 4))

        tecnico_responsavel = random.choice(tecnicos)
        criado_por = users.get(tecnico_responsavel.email)

        qtd_clientes = random.choices([1, 2, 3, 4], weights=[45, 30, 18, 7])[0]
        clientes_relatorio = random.sample(clientes, qtd_clientes)
        cliente_principal = clientes_relatorio[0]

        status = random.choices(
            [
                StatusRelatorio.APROVADO,
                StatusRelatorio.CONFERENCIA,
                StatusRelatorio.AJUSTE,
                StatusRelatorio.REJEITADO,
                StatusRelatorio.RASCUNHO,
            ],
            weights=[45, 22, 16, 9, 8],
        )[0]

        numero = None
        if status != StatusRelatorio.RASCUNHO:
            numero = f"DEMO-{hoje.year}-{idx:04d}"

        relatorio = RelatorioTecnico.objects.create(
            numero=numero,
            status=status,
            cliente=cliente_principal,
            tecnico_responsavel=tecnico_responsavel,
            cidade_atendimento=cliente_principal.cidade or "Cascavel",
            uf_atendimento=cliente_principal.uf or UF.PR,
            tipo_localidade=random.choice([TipoLocalidade.INTERIOR, TipoLocalidade.CAPITAL]),
            data_inicio=data_inicio,
            data_fim=data_fim,
            motivo=random.choice(
                [
                    "Atendimento técnico presencial e conferência operacional.",
                    "Implantação de controles e validação de processos.",
                    "Visita técnica para levantamento de requisitos.",
                    "Acompanhamento operacional e treinamento de equipe.",
                    "Suporte presencial em ambiente do cliente.",
                ]
            ),
            centro_custo=f"{PREFIXO} / Operações Técnicas",
            tipo_relatorio=random.choice(
                [TipoRelatorio.OPERACIONAL, TipoRelatorio.INSTITUCIONAL]
            ),
            valor_adiantamento=random.choice(
                [
                    Decimal("0.00"),
                    Decimal("300.00"),
                    Decimal("500.00"),
                    Decimal("800.00"),
                    Decimal("1200.00"),
                ]
            ),
            km_excedente_interno=random.choice(
                [
                    Decimal("0.00"),
                    Decimal("0.00"),
                    Decimal("8.00"),
                    Decimal("12.50"),
                    Decimal("25.00"),
                ]
            ),
            observacao_km_excedente=random.choice(
                [
                    "",
                    "Deslocamentos internos entre hotel, cliente e restaurante.",
                    "Visitas locais complementares durante o atendimento.",
                    "Deslocamento urbano para evento e retorno ao hotel.",
                ]
            ),
            observacoes=random.choice(
                [
                    "",
                    "Comprovantes anexados conforme disponibilidade.",
                    "Atendimento realizado conforme agenda acordada.",
                    "Relatório gerado para demonstração do dashboard.",
                ]
            ),
            criado_por=criado_por,
        )

        for ordem, cliente in enumerate(clientes_relatorio):
            RelatorioCliente.objects.create(
                relatorio=relatorio,
                cliente=cliente,
                ordem=ordem,
            )

        equipe_possivel = [t for t in tecnicos if t.pk != tecnico_responsavel.pk]
        for tecnico in random.sample(equipe_possivel, random.randint(0, 2)):
            RelatorioTecnicoEquipe.objects.get_or_create(
                relatorio=relatorio,
                tecnico=tecnico,
            )

        self.criar_historico_inicial(relatorio, criado_por)

        qtd_despesas = random.randint(2, 6)
        qtd_trechos = random.randint(1, 4)

        atribuicoes = self.gerar_atribuicoes_clientes(
            clientes_relatorio,
            qtd_despesas + qtd_trechos,
        )

        for ordem in range(qtd_despesas):
            participantes = atribuicoes.pop(0)
            self.criar_despesa_demo(
                relatorio=relatorio,
                ordem=ordem,
                participantes=participantes,
                data_inicio=data_inicio,
                data_fim=data_fim,
                financeiro_user=financeiro_user,
                status_relatorio=status,
            )

        for ordem in range(qtd_trechos):
            participantes = atribuicoes.pop(0)
            self.criar_trecho_demo(
                relatorio=relatorio,
                ordem=ordem,
                participantes=participantes,
                data_inicio=data_inicio,
                data_fim=data_fim,
                financeiro_user=financeiro_user,
                status_relatorio=status,
            )

        if random.random() < 0.35:
            Adiantamento.objects.create(
                tecnico=tecnico_responsavel,
                relatorio=relatorio,
                tipo=random.choice([TipoAdiantamento.ADIANTAMENTO, TipoAdiantamento.REEMBOLSO]),
                valor=random.choice(
                    [
                        Decimal("200.00"),
                        Decimal("350.00"),
                        Decimal("500.00"),
                        Decimal("750.00"),
                    ]
                ),
                data=data_inicio,
                descricao=f"{PREFIXO} adiantamento vinculado ao relatório",
            )

        if status in [StatusRelatorio.CONFERENCIA, StatusRelatorio.AJUSTE, StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO]:
            self.registrar_evento(
                relatorio,
                criado_por,
                TipoEventoHistorico.ENVIADO,
                "Relatório enviado para conferência.",
            )

        if status == StatusRelatorio.AJUSTE:
            relatorio.motivo_rejeicao = random.choice(
                [
                    "Solicitar detalhamento adicional de despesas.",
                    "Necessário revisar comprovantes e rateio.",
                    "Conferir quilometragem informada em trecho específico.",
                ]
            )
            relatorio.save(update_fields=["motivo_rejeicao", "atualizado_em"])
            self.registrar_evento(
                relatorio,
                financeiro_user,
                TipoEventoHistorico.AJUSTE_SOLICITADO,
                relatorio.motivo_rejeicao,
            )

        if status == StatusRelatorio.REJEITADO:
            relatorio.motivo_rejeicao = random.choice(
                [
                    "Relatório recusado para demonstração de fluxo.",
                    "Inconsistências financeiras não resolvidas.",
                    "Documentação insuficiente para aprovação.",
                ]
            )
            relatorio.save(update_fields=["motivo_rejeicao", "atualizado_em"])
            self.registrar_evento(
                relatorio,
                financeiro_user,
                TipoEventoHistorico.REJEITADO,
                relatorio.motivo_rejeicao,
            )

        if status == StatusRelatorio.APROVADO:
            aprovado_em = timezone.now() - timedelta(days=random.randint(0, 20))
            RelatorioTecnico.objects.filter(pk=relatorio.pk).update(
                aprovado_em=aprovado_em,
                aprovado_por=financeiro_user,
            )
            relatorio.refresh_from_db()

            self.registrar_evento(
                relatorio,
                financeiro_user,
                TipoEventoHistorico.APROVADO,
                "Relatório aprovado pelo financeiro.",
            )
            self.criar_snapshot_demo(relatorio, financeiro_user)

    def gerar_atribuicoes_clientes(self, clientes_relatorio, total_itens):
        atribuicoes = [[cliente] for cliente in clientes_relatorio]

        while len(atribuicoes) < total_itens:
            qtd = random.randint(1, min(len(clientes_relatorio), 3))
            atribuicoes.append(random.sample(clientes_relatorio, qtd))

        random.shuffle(atribuicoes)
        return atribuicoes

    def criar_despesa_demo(
        self,
        relatorio,
        ordem,
        participantes,
        data_inicio,
        data_fim,
        financeiro_user,
        status_relatorio,
    ):
        tipo = random.choice(TIPOS_DESPESA)
        valor = valor_despesa_por_tipo(tipo)
        data = data_inicio + timedelta(days=random.randint(0, max((data_fim - data_inicio).days, 0)))

        rejeitada = status_relatorio in [StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO] and random.random() < 0.12
        ajustada = not rejeitada and status_relatorio == StatusRelatorio.APROVADO and random.random() < 0.22

        valor_aprovado = None
        if rejeitada:
            valor_aprovado = Decimal("0.00")
        elif ajustada:
            desconto = Decimal(random.choice(["0.05", "0.10", "0.15", "0.20"]))
            valor_aprovado = money(valor * (Decimal("1.00") - desconto))

        despesa = ItemDespesa.objects.create(
            relatorio=relatorio,
            ordem=ordem,
            data=data,
            tipo=tipo,
            descricao=random.choice(DESCRICOES_DESPESA[tipo]),
            valor=valor,
            valor_aprovado=valor_aprovado,
            status_financeiro=(
                StatusFinanceiroItem.REJEITADO if rejeitada else StatusFinanceiroItem.APROVADO
            ),
            rejeitado=rejeitada,
            motivo_rejeicao=(
                "Item rejeitado para demonstração de auditoria."
                if rejeitada
                else ""
            ),
            motivo_recusa=(
                "Valor ajustado pelo financeiro para demonstração."
                if ajustada
                else ""
            ),
            rejeitado_por=financeiro_user if rejeitada else None,
            rejeitado_em=timezone.now() if rejeitada else None,
            quem_pagou=random.choice([QuemPagou.TECNICO, QuemPagou.EMPRESA]),
            observacoes=random.choice(
                [
                    "",
                    "Despesa compartilhada entre clientes.",
                    "Valor ajustado na conferência financeira.",
                    "Despesa registrada em atendimento externo.",
                ]
            ),
        )

        for cliente in participantes:
            DespesaCliente.objects.create(despesa=despesa, cliente=cliente)

        partes_original = dividir_valor(valor, len(participantes))
        total_final = despesa.valor_final
        partes_final = dividir_valor(total_final, len(participantes))

        for cliente, valor_original, valor_final in zip(
            participantes,
            partes_original,
            partes_final,
        ):
            status_rateio = StatusRateio.AUTO
            motivo = ""
            alterado_por = None

            if rejeitada:
                status_rateio = StatusRateio.APPROVED
                motivo = "Item rejeitado pelo financeiro."
                alterado_por = financeiro_user
            elif ajustada:
                status_rateio = StatusRateio.ADJUSTED
                motivo = "Ajuste financeiro aplicado para demonstração."
                alterado_por = financeiro_user
            elif status_relatorio == StatusRelatorio.APROVADO:
                status_rateio = StatusRateio.APPROVED

            DespesaRateio.objects.create(
                despesa=despesa,
                cliente=cliente,
                valor_original=valor_original,
                valor_final=valor_final,
                percentual=dec((valor_original / valor) * Decimal("100"), "0.0001") if valor else None,
                status=status_rateio,
                alterado_por=alterado_por,
                motivo_ajuste=motivo,
            )

        if rejeitada:
            self.registrar_evento(
                relatorio,
                financeiro_user,
                TipoEventoHistorico.ITEM_REJEITADO,
                f"Despesa rejeitada: {despesa.descricao}.",
                {"despesa_id": despesa.pk, "valor": str(valor)},
            )
        elif ajustada:
            self.registrar_evento(
                relatorio,
                financeiro_user,
                TipoEventoHistorico.VALOR_ALTERADO,
                f"Valor aprovado da despesa ajustado: {despesa.descricao}.",
                {
                    "despesa_id": despesa.pk,
                    "valor_original": str(valor),
                    "valor_aprovado": str(valor_aprovado),
                },
            )

    def criar_trecho_demo(
        self,
        relatorio,
        ordem,
        participantes,
        data_inicio,
        data_fim,
        financeiro_user,
        status_relatorio,
    ):
        rota = random.choice(ROTAS_BASE)
        km_api = Decimal(rota["km"])

        # Em alguns trechos, cria divergência visível >15%.
        if random.random() < 0.18:
            km_informado = money(km_api * Decimal(random.choice(["1.18", "1.22", "1.30"])))
        else:
            km_informado = money(km_api * Decimal(random.choice(["0.98", "1.00", "1.03"])))

        data = data_inicio + timedelta(days=random.randint(0, max((data_fim - data_inicio).days, 0)))
        cliente_base = participantes[0]
        valor_km_base = cliente_base.valor_km or Decimal("0.00")

        rejeitado = status_relatorio in [StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO] and random.random() < 0.08

        trecho = TrechoKm.objects.create(
            relatorio=relatorio,
            ordem=ordem,
            data=data,
            origem=rota["origem"],
            origem_endereco_completo=f"Centro, {rota['origem']}",
            origem_lat=Decimal(rota["origem_lat"]),
            origem_lon=Decimal(rota["origem_lon"]),
            destino=rota["destino"],
            destino_endereco_completo=f"Centro, {rota['destino']}",
            destino_lat=Decimal(rota["destino_lat"]),
            destino_lon=Decimal(rota["destino_lon"]),
            km=km_informado,
            km_calculado_api=km_api,
            km_informado=km_informado,
            diferenca_km_percentual=porcentagem_diferenca(km_api, km_informado),
            fonte_calculo_rota="OSRM",
            calculado_em=timezone.now(),
            rota_geojson=rota_geojson(
                rota["origem_lon"],
                rota["origem_lat"],
                rota["destino_lon"],
                rota["destino_lat"],
            ),
            valor_km=valor_km_base,
            status_financeiro=(
                StatusFinanceiroItem.REJEITADO if rejeitado else StatusFinanceiroItem.APROVADO
            ),
            rejeitado=rejeitado,
            motivo_rejeicao=(
                "Trecho rejeitado para demonstração de auditoria."
                if rejeitado
                else ""
            ),
            rejeitado_por=financeiro_user if rejeitado else None,
            rejeitado_em=timezone.now() if rejeitado else None,
            observacao=random.choice(
                [
                    "",
                    "Rota calculada automaticamente para demonstração.",
                    "Deslocamento técnico entre clientes.",
                    "KM ajustado manualmente pelo técnico.",
                ]
            ),
        )

        for cliente in participantes:
            TrechoKMCliente.objects.create(trecho=trecho, cliente=cliente)

        for cliente in participantes:
            valor_km_cliente = cliente.valor_km or Decimal("0.00")
            valor_calculado = money(km_informado * valor_km_cliente)

            if rejeitado:
                valor_final = Decimal("0.00")
                status_rateio = StatusRateio.APPROVED
                motivo = "Trecho rejeitado pelo financeiro."
                alterado_por = financeiro_user
            else:
                ajustado = status_relatorio == StatusRelatorio.APROVADO and random.random() < 0.15
                if ajustado:
                    valor_final = money(valor_calculado * Decimal("0.95"))
                    status_rateio = StatusRateio.ADJUSTED
                    motivo = "Ajuste financeiro de KM para demonstração."
                    alterado_por = financeiro_user
                else:
                    valor_final = valor_calculado
                    status_rateio = (
                        StatusRateio.APPROVED
                        if status_relatorio == StatusRelatorio.APROVADO
                        else StatusRateio.AUTO
                    )
                    motivo = ""
                    alterado_por = None

            TrechoRateioKM.objects.create(
                trecho=trecho,
                cliente=cliente,
                km_original=km_informado,
                km_final=km_informado,
                valor_rateado=valor_calculado,
                km_cliente=km_informado,
                valor_km=valor_km_cliente,
                valor_calculado=valor_calculado,
                valor_final=valor_final,
                status=status_rateio,
                alterado_por=alterado_por,
                motivo_ajuste=motivo,
            )

        if rejeitado:
            self.registrar_evento(
                relatorio,
                financeiro_user,
                TipoEventoHistorico.ITEM_REJEITADO,
                f"Trecho KM rejeitado: {trecho.origem} → {trecho.destino}.",
                {"trecho_id": trecho.pk, "km": str(km_informado)},
            )

        if trecho.km_divergente_rota:
            self.registrar_evento(
                relatorio,
                None,
                TipoEventoHistorico.VALOR_ALTERADO,
                f"KM informado diverge da rota calculada em {trecho.diferenca_km_percentual}%.",
                {
                    "trecho_id": trecho.pk,
                    "km_calculado_api": str(km_api),
                    "km_informado": str(km_informado),
                    "diferenca_percentual": str(trecho.diferenca_km_percentual),
                },
            )

    def criar_historico_inicial(self, relatorio, usuario):
        self.registrar_evento(
            relatorio,
            usuario,
            TipoEventoHistorico.CRIADO,
            "Relatório criado.",
        )

    def registrar_evento(self, relatorio, usuario, tipo_evento, descricao, dados_json=None):
        HistoricoRelatorio.objects.create(
            relatorio=relatorio,
            usuario=usuario if getattr(usuario, "is_authenticated", False) else None,
            acao=tipo_evento,
            tipo_evento=tipo_evento,
            descricao=descricao,
            dados_json=dados_json or {},
        )

    def criar_snapshot_demo(self, relatorio, financeiro_user):
        relatorio.refresh_from_db()

        clientes = []
        for cliente in relatorio.clientes_exibicao():
            clientes.append(
                {
                    "id": cliente.pk,
                    "nome": cliente.nome,
                    "cidade": cliente.cidade,
                    "uf": cliente.uf,
                    "valor_km": str(cliente.valor_km or Decimal("0.00")),
                }
            )

        tecnicos = [
            {
                "id": tecnico.pk,
                "nome": tecnico.nome,
                "email": tecnico.email,
            }
            for tecnico in relatorio.tecnicos_exibicao()
        ]

        despesas = []
        for despesa in relatorio.despesas.prefetch_related("rateios__cliente").all():
            despesas.append(
                {
                    "id": despesa.pk,
                    "data": despesa.data.isoformat() if despesa.data else None,
                    "tipo": despesa.tipo,
                    "descricao": despesa.descricao,
                    "valor": str(despesa.valor),
                    "valor_final": str(despesa.valor_final),
                    "rejeitado": despesa.rejeitado,
                    "quem_pagou": despesa.quem_pagou,
                    "rateios": [
                        {
                            "cliente": rateio.cliente.nome,
                            "valor_original": str(rateio.valor_original),
                            "valor_final": str(rateio.valor_final),
                            "status": rateio.status,
                        }
                        for rateio in despesa.rateios.all()
                    ],
                }
            )

        trechos = []
        for trecho in relatorio.trechos.prefetch_related("rateios__cliente").all():
            trechos.append(
                {
                    "id": trecho.pk,
                    "data": trecho.data.isoformat() if trecho.data else None,
                    "origem": trecho.origem,
                    "destino": trecho.destino,
                    "km": str(trecho.km),
                    "km_calculado_api": str(trecho.km_calculado_api or ""),
                    "km_informado": str(trecho.km_informado or ""),
                    "diferenca_km_percentual": str(trecho.diferenca_km_percentual or ""),
                    "rota_geojson": trecho.rota_geojson,
                    "rejeitado": trecho.rejeitado,
                    "rateios": [
                        {
                            "cliente": rateio.cliente.nome,
                            "km_cliente": str(rateio.km_cliente),
                            "valor_km": str(rateio.valor_km),
                            "valor_calculado": str(rateio.valor_calculado),
                            "valor_final": str(rateio.valor_final),
                            "status": rateio.status,
                        }
                        for rateio in trecho.rateios.all()
                    ],
                }
            )

        payload = {
            "schema_version": 1,
            "relatorio": {
                "id": relatorio.pk,
                "numero": relatorio.numero,
                "status": relatorio.status,
                "periodo": {
                    "inicio": relatorio.data_inicio.isoformat(),
                    "fim": relatorio.data_fim.isoformat(),
                },
                "motivo": relatorio.motivo,
                "centro_custo": relatorio.centro_custo,
                "valor_adiantamento": str(relatorio.valor_adiantamento),
                "km_excedente_interno": str(relatorio.km_excedente_interno),
                "observacao_km_excedente": relatorio.observacao_km_excedente,
            },
            "clientes": clientes,
            "tecnicos": tecnicos,
            "despesas": despesas,
            "trechos": trechos,
            "totais": {
                "total_solicitado": str(relatorio.total_solicitado),
                "total_aprovado": str(relatorio.total_aprovado),
                "diferenca_removida": str(relatorio.diferenca_removida),
                "saldo_aprovado": str(relatorio.saldo_aprovado),
                "total_km_percorrido": str(relatorio.total_km_percorrido),
            },
            "finalizacao": {
                "finalizado_em": (relatorio.aprovado_em or timezone.now()).isoformat(),
                "finalizado_por": financeiro_user.get_username() if financeiro_user else None,
            },
        }

        checksum_base = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        checksum = hashlib.sha256(checksum_base).hexdigest()

        RelatorioSnapshotFinanceiro.objects.get_or_create(
            relatorio=relatorio,
            defaults={
                "schema_version": 1,
                "numero": relatorio.numero or relatorio.identificador,
                "status": relatorio.status,
                "total_solicitado": relatorio.total_solicitado,
                "total_aprovado": relatorio.total_aprovado,
                "diferenca_removida": relatorio.diferenca_removida,
                "payload": payload,
                "checksum": checksum,
                "finalizado_em": relatorio.aprovado_em or timezone.now(),
                "finalizado_por": financeiro_user,
            },
        )
