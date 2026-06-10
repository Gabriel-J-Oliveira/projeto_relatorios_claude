from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from relatorios.models import (
    Cliente,
    DespesaCliente,
    DespesaRateio,
    ItemDespesa,
    Municipio,
    PapelTecnico,
    PoliticaValor,
    QuemPagou,
    RelatorioCliente,
    RelatorioTecnico,
    RelatorioTecnicoEquipe,
    StatusFinanceiroItem,
    StatusRateio,
    StatusRelatorio,
    Tecnico,
    TipoDespesa,
    TipoDocumentoComprovante,
    TipoEventoHistorico,
    TipoLocalidade,
    TipoRelatorio,
    TrechoKMCliente,
    TrechoKm,
    TrechoRateioKM,
    UF,
    valor_km_control_sul,
)
from relatorios.services.historico_service import registrar_evento
from relatorios.services.rateio_service import garantir_rateios_relatorio
from relatorios.services.snapshot_service import criar_snapshot_financeiro


PREFIXO_NUMERO = "DEMO"
CENTAVO = Decimal("0.01")


CLIENTES = [
    ("DEMO AGRÍCOLA URTIGÃO", "90000000000101", "Cascavel", "PR", "1.68"),
    ("DEMO CONTROLSUL GESTÃO EMPRESARIAL", "90000000000102", "Curitiba", "PR", "1.35"),
    ("DEMO ALIMENTOS ZAELI LTDA", "90000000000103", "Umuarama", "PR", "1.00"),
    ("DEMO ARCO-IRIS AGRO LTDA", "90000000000104", "Ponta Grossa", "PR", "2.00"),
    ("DEMO BOBATO SUPERMERCADOS", "90000000000105", "Maringá", "PR", "1.85"),
    ("DEMO FRONTEIRA AGRONEGÓCIOS", "90000000000106", "Foz do Iguaçu", "PR", "1.55"),
    ("DEMO CAMPO GRANDE HOLDING", "90000000000107", "Campo Grande", "MS", "1.75"),
    ("DEMO SÃO PAULO SERVIÇOS", "90000000000108", "São Paulo", "SP", "2.20"),
]

TECNICOS = [
    ("Demo Técnico Operacional", "demo.tecnico@controlsul.com.br"),
    ("Demo Técnica Fiscal", "demo.fiscal@controlsul.com.br"),
    ("Demo Consultor Contábil", "demo.contabil@controlsul.com.br"),
    ("Demo Consultora Tributária", "demo.tributaria@controlsul.com.br"),
    ("Demo Financeiro", "demo.financeiro@controlsul.com.br"),
]

MUNICIPIOS = [
    ("4106902", "Curitiba", "PR", "Paraná", True, TipoLocalidade.CAPITAL),
    ("4104808", "Cascavel", "PR", "Paraná", False, TipoLocalidade.INTERIOR),
    ("4115200", "Maringá", "PR", "Paraná", False, TipoLocalidade.INTERIOR),
    ("4119905", "Ponta Grossa", "PR", "Paraná", False, TipoLocalidade.INTERIOR),
    ("4108304", "Foz do Iguaçu", "PR", "Paraná", False, TipoLocalidade.FRONTEIRA),
    ("5002704", "Campo Grande", "MS", "Mato Grosso do Sul", True, TipoLocalidade.CAPITAL),
    ("3550308", "São Paulo", "SP", "São Paulo", True, TipoLocalidade.CAPITAL),
]


class Command(BaseCommand):
    help = "Popula o banco com 20 relatórios completos de demonstração."

    def add_arguments(self, parser):
        parser.add_argument("--confirmar", action="store_true", help="Obrigatório para gravar dados.")
        parser.add_argument("--limpar-demo", action="store_true", help="Remove dados DEMO anteriores antes de criar.")
        parser.add_argument("--usuario", default="demo.financeiro", help="Username usado como criador/aprovador.")

    def handle(self, *args, **options):
        if not options["confirmar"]:
            raise CommandError("Use --confirmar para gravar. Opcional: --limpar-demo para recriar do zero.")

        with transaction.atomic():
            if options["limpar_demo"]:
                self._limpar_demo()

            user = self._usuario(options["usuario"])
            municipios = self._municipios()
            clientes = self._clientes()
            tecnicos = self._tecnicos()
            self._politicas()

            criados = []
            for idx in range(20):
                relatorio = self._criar_relatorio(idx, user, municipios, clientes, tecnicos)
                criados.append(relatorio)

        self.stdout.write(self.style.SUCCESS(f"Dados demo criados com sucesso: {len(criados)} relatórios."))
        self.stdout.write("Prefixo dos relatórios: DEMO-0001 a DEMO-0020.")

    def _limpar_demo(self):
        RelatorioTecnico.objects.filter(numero__startswith=f"{PREFIXO_NUMERO}-").delete()
        Cliente.objects.filter(cnpj_cpf__startswith="900000000001").delete()
        Tecnico.objects.filter(email__startswith="demo.").delete()
        get_user_model().objects.filter(username__startswith="demo.").delete()

    def _usuario(self, username):
        User = get_user_model()
        user, _ = User.objects.get_or_create(
            username=username,
            defaults={
                "first_name": "Demo",
                "last_name": "Financeiro",
                "email": "demo.financeiro@controlsul.com.br",
                "is_staff": True,
            },
        )
        user.is_active = True
        user.is_staff = True
        user.save(update_fields=["is_active", "is_staff"])
        for nome_grupo in ["Financeiro", "Administrador ERP", "Tecnico"]:
            grupo, _ = Group.objects.get_or_create(name=nome_grupo)
            user.groups.add(grupo)
        return user

    def _municipios(self):
        municipios = {}
        for codigo, nome, uf, uf_nome, capital, localidade in MUNICIPIOS:
            municipio, _ = Municipio.objects.update_or_create(
                codigo_ibge=codigo,
                defaults={
                    "nome": nome,
                    "nome_normalizado": self._normalizar(nome),
                    "uf": uf,
                    "uf_nome": uf_nome,
                    "eh_capital": capital,
                    "tipo_localidade_padrao": localidade,
                    "ativo": True,
                },
            )
            municipios[nome] = municipio
        return municipios

    def _clientes(self):
        clientes = []
        for nome, cnpj, cidade, uf, valor_km in CLIENTES:
            cliente, _ = Cliente.objects.update_or_create(
                cnpj_cpf=cnpj,
                defaults={
                    "nome": nome,
                    "razao_social": nome,
                    "nome_fantasia": nome,
                    "cidade": cidade,
                    "uf": uf,
                    "cep": "80000000",
                    "logradouro": "Rua Demo",
                    "numero": "100",
                    "bairro": "Centro",
                    "telefone": "45999990000",
                    "ativo": True,
                    "valor_km": Decimal(valor_km),
                    "origem_api": True,
                    "sincronizado_em": timezone.now(),
                    "valor_km_pendente_api_novo": False,
                },
            )
            clientes.append(cliente)
        return clientes

    def _tecnicos(self):
        tecnicos = []
        for nome, email in TECNICOS:
            tecnico, _ = Tecnico.objects.update_or_create(
                email=email,
                defaults={"nome": nome, "telefone": "45999990000", "ativo": True},
            )
            tecnicos.append(tecnico)
        return tecnicos

    def _politicas(self):
        hoje = timezone.localdate()
        politicas = [
            ("DEMO_REFEICAO_INTERIOR", PoliticaValor.TipoPolitica.REFEICAO, TipoDespesa.ALIMENTACAO, TipoLocalidade.INTERIOR, "", "Refeição Interior", "60.00"),
            ("DEMO_REFEICAO_CAPITAL", PoliticaValor.TipoPolitica.REFEICAO, TipoDespesa.ALIMENTACAO, TipoLocalidade.CAPITAL, "", "Refeição Capital", "80.00"),
            ("DEMO_HOSPEDAGEM_CURITIBA", PoliticaValor.TipoPolitica.HOSPEDAGEM, TipoDespesa.HOSPEDAGEM, "", "Curitiba", "Hospedagem Curitiba", "400.00"),
            ("DEMO_HOSPEDAGEM_CASCAVEL", PoliticaValor.TipoPolitica.HOSPEDAGEM, TipoDespesa.HOSPEDAGEM, "", "Cascavel", "Hospedagem Cascavel", "300.00"),
            ("DEMO_KM_DIARIO_CURITIBA", PoliticaValor.TipoPolitica.KM_DIARIO, TipoDespesa.TRANSPORTE, "", "Curitiba", "Média KM diário Curitiba", "80.00"),
        ]
        for chave, tipo_politica, tipo_despesa, localidade, cidade, descricao, valor in politicas:
            PoliticaValor.objects.update_or_create(
                chave=chave,
                defaults={
                    "tipo_politica": tipo_politica,
                    "tipo_despesa": tipo_despesa,
                    "tipo_localidade": localidade,
                    "cidade": cidade,
                    "descricao": descricao,
                    "limite_valor": Decimal(valor),
                    "vigencia_inicio": hoje - timedelta(days=365),
                    "ativo": True,
                },
            )
        PoliticaValor.objects.update_or_create(
            chave="DEMO_VALOR_KM_CONTROLSUL",
            defaults={
                "tipo_politica": PoliticaValor.TipoPolitica.VALOR_KM,
                "descricao": "Valor KM ControlSul",
                "valor_km": Decimal("1.3500"),
                "vigencia_inicio": hoje - timedelta(days=365),
                "ativo": True,
            },
        )

    def _criar_relatorio(self, idx, user, municipios, clientes, tecnicos):
        numero = f"{PREFIXO_NUMERO}-{idx + 1:04d}"
        data_inicio = timezone.localdate() - timedelta(days=80 - (idx * 3))
        data_fim = data_inicio + timedelta(days=idx % 4)
        municipio = list(municipios.values())[idx % len(municipios)]
        tecnico = tecnicos[idx % len(tecnicos)]
        clientes_relatorio = [clientes[idx % len(clientes)]]
        if idx % 3 != 0:
            clientes_relatorio.append(clientes[(idx + 2) % len(clientes)])
        if idx % 7 == 0:
            clientes_relatorio.append(clientes[(idx + 4) % len(clientes)])
        clientes_relatorio = list(dict.fromkeys(clientes_relatorio))

        relatorio = RelatorioTecnico.objects.create(
            numero=numero,
            status=StatusRelatorio.RASCUNHO,
            cliente=clientes_relatorio[0],
            tecnico_responsavel=tecnico,
            municipio_atendimento=municipio,
            cidade_atendimento=municipio.nome,
            uf_atendimento=municipio.uf,
            tipo_localidade=municipio.tipo_localidade_padrao,
            data_inicio=data_inicio,
            data_fim=data_fim,
            motivo=f"Atendimento demo {idx + 1}",
            tipo_relatorio=[
                TipoRelatorio.ADMINISTRATIVO,
                TipoRelatorio.INSTITUCIONAL,
                TipoRelatorio.OPERACIONAL,
                TipoRelatorio.TREINAMENTO,
            ][idx % 4],
            valor_adiantamento=Decimal("250.00") if idx % 5 == 0 else Decimal("0.00"),
            km_excedente_interno=Decimal("12.00") if idx % 4 == 0 else Decimal("0.00"),
            observacao_km_excedente="Deslocamentos internos entre hotel, cliente e restaurante." if idx % 4 == 0 else "",
            observacoes="Relatório de demonstração gerado por script.",
            criado_por=user,
        )
        relatorio.sincronizar_municipio_atendimento()
        relatorio.save()

        for ordem, cliente in enumerate(clientes_relatorio, start=1):
            RelatorioCliente.objects.create(
                relatorio=relatorio,
                cliente=cliente,
                ordem=ordem,
                motivo_viagem=f"Demonstração de rotina operacional para {cliente.nome}.",
            )

        if idx % 2 == 0:
            RelatorioTecnicoEquipe.objects.create(
                relatorio=relatorio,
                tecnico=tecnicos[(idx + 1) % len(tecnicos)],
                papel=PapelTecnico.APOIO,
            )

        self._despesas(relatorio, clientes_relatorio, idx)
        self._trechos(relatorio, clientes_relatorio, idx)
        garantir_rateios_relatorio(relatorio)
        self._aplicar_variacoes_financeiras(relatorio, user, idx)
        garantir_rateios_relatorio(relatorio)
        self._status_final(relatorio, user, idx)
        return relatorio

    def _despesas(self, relatorio, clientes_relatorio, idx):
        tipos = [
            (TipoDespesa.ALIMENTACAO, "Almoço em viagem", "85.00", QuemPagou.TECNICO),
            (TipoDespesa.HOSPEDAGEM, "Hotel em atendimento", "420.00", QuemPagou.TECNICO),
            (TipoDespesa.PEDAGIO, "Pedágio rodoviário", "38.70", QuemPagou.EMPRESA),
            (TipoDespesa.TRANSPORTE, "Táxi/Uber local", "96.40", QuemPagou.TECNICO),
        ]
        for ordem, (tipo, descricao, valor, quem_pagou) in enumerate(tipos[: 2 + (idx % 3)], start=1):
            despesa = ItemDespesa.objects.create(
                relatorio=relatorio,
                ordem=ordem,
                data=relatorio.data_inicio + timedelta(days=min(ordem - 1, (relatorio.data_fim - relatorio.data_inicio).days)),
                tipo=tipo,
                descricao=f"{descricao} DEMO {idx + 1}",
                valor=Decimal(valor) + Decimal(idx * 3),
                quem_pagou=quem_pagou,
                tipo_documento_comprovante=TipoDocumentoComprovante.NOTA_FISCAL if ordem % 2 else TipoDocumentoComprovante.RECIBO,
                numero_documento_comprovante=f"DEMO-NF-{idx + 1:04d}-{ordem}",
                observacoes="Despesa de demonstração.",
            )
            for cliente in clientes_relatorio if ordem % 2 else clientes_relatorio[:1]:
                DespesaCliente.objects.create(despesa=despesa, cliente=cliente)

    def _trechos(self, relatorio, clientes_relatorio, idx):
        trechos = [
            ("Escritório ControlSul", relatorio.cidade_atendimento, Decimal("120.00") + idx),
            (relatorio.cidade_atendimento, "Cliente principal", Decimal("42.50") + idx),
        ]
        if idx % 2 == 0:
            trechos.append(("Hotel", "Cliente secundário", Decimal("18.20") + idx))
        for ordem, (origem, destino, km) in enumerate(trechos, start=1):
            trecho = TrechoKm.objects.create(
                relatorio=relatorio,
                ordem=ordem,
                data=relatorio.data_inicio + timedelta(days=min(ordem - 1, (relatorio.data_fim - relatorio.data_inicio).days)),
                origem=origem,
                destino=destino,
                km=km,
                km_calculado_api=km - Decimal("4.00") if ordem == 1 else km,
                km_informado=km,
                diferenca_km_percentual=Decimal("12.00") if ordem == 1 else Decimal("0.00"),
                fonte_calculo_rota="demo",
                calculado_em=timezone.now(),
                valor_km=valor_km_control_sul(),
                observacao="Trecho de demonstração.",
            )
            for cliente in clientes_relatorio if ordem == 1 else clientes_relatorio[:1]:
                TrechoKMCliente.objects.create(trecho=trecho, cliente=cliente)

    def _aplicar_variacoes_financeiras(self, relatorio, user, idx):
        if idx % 4 == 0:
            despesa = relatorio.despesas.order_by("ordem").first()
            if despesa:
                despesa.valor_aprovado = (despesa.valor - Decimal("15.00")).quantize(CENTAVO)
                despesa.save(update_fields=["valor_aprovado"])
                garantir_rateios_relatorio(relatorio)
        if idx % 6 == 0:
            despesa = relatorio.despesas.order_by("-ordem").first()
            if despesa:
                despesa.rejeitado = True
                despesa.status_financeiro = StatusFinanceiroItem.REJEITADO
                despesa.motivo_rejeicao = "Comprovante insuficiente para demonstração."
                despesa.rejeitado_por = user
                despesa.rejeitado_em = timezone.now()
                despesa.save()
        if idx % 5 == 0:
            rateio = TrechoRateioKM.objects.filter(trecho__relatorio=relatorio).first()
            if rateio:
                rateio.valor_final = (rateio.valor_final - Decimal("10.00")).quantize(CENTAVO)
                if rateio.valor_final < 0:
                    rateio.valor_final = Decimal("0.00")
                rateio.valor_rateado = rateio.valor_final
                rateio.status = StatusRateio.ADJUSTED
                rateio.motivo_ajuste = "Ajuste financeiro demonstrativo de KM."
                rateio.alterado_por = user
                rateio.save()

    def _status_final(self, relatorio, user, idx):
        status_cycle = [
            StatusRelatorio.RASCUNHO,
            StatusRelatorio.CONFERENCIA,
            StatusRelatorio.AJUSTE,
            StatusRelatorio.APROVADO,
            StatusRelatorio.REJEITADO,
        ]
        status = status_cycle[idx % len(status_cycle)]
        if status == StatusRelatorio.RASCUNHO:
            registrar_evento(relatorio, user, TipoEventoHistorico.CRIADO, "Relatório demo criado.", {})
            return

        if status in {StatusRelatorio.CONFERENCIA, StatusRelatorio.AJUSTE, StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}:
            relatorio.status = StatusRelatorio.CONFERENCIA
            relatorio.save(update_fields=["status"])
            registrar_evento(relatorio, user, TipoEventoHistorico.ENVIADO, "Relatório demo enviado para conferência.", {})

        if status == StatusRelatorio.AJUSTE:
            relatorio.status = StatusRelatorio.AJUSTE
            relatorio.motivo_rejeicao = "Ajuste solicitado em relatório demo."
            relatorio.save(update_fields=["status", "motivo_rejeicao"])
            registrar_evento(relatorio, user, TipoEventoHistorico.AJUSTE_SOLICITADO, relatorio.motivo_rejeicao, {})
            return

        if status == StatusRelatorio.APROVADO:
            relatorio.status = StatusRelatorio.APROVADO
            relatorio.aprovado_por = user
            relatorio.aprovado_em = timezone.now()
            relatorio.save(update_fields=["status", "aprovado_por", "aprovado_em"])
            registrar_evento(relatorio, user, TipoEventoHistorico.APROVADO, "Relatório demo aprovado.", {})
            criar_snapshot_financeiro(relatorio, user)
            return

        if status == StatusRelatorio.REJEITADO:
            relatorio.status = StatusRelatorio.REJEITADO
            relatorio.motivo_rejeicao = "Relatório demo rejeitado definitivamente."
            relatorio.aprovado_por = user
            relatorio.aprovado_em = timezone.now()
            relatorio.save(update_fields=["status", "motivo_rejeicao", "aprovado_por", "aprovado_em"])
            registrar_evento(relatorio, user, TipoEventoHistorico.REJEITADO, relatorio.motivo_rejeicao, {})
            criar_snapshot_financeiro(relatorio, user)

    @staticmethod
    def _normalizar(valor):
        import unicodedata

        texto = unicodedata.normalize("NFKD", valor or "")
        texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
        return " ".join(texto.lower().split())
