"""
Models v3 — Campo Manager
Mudanças:
- centro_custo movido de ItemDespesa para RelatorioTecnico
- campo reembolsavel removido de ItemDespesa
- RelatorioTecnicoEquipe mantido para múltiplos técnicos
"""

from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
from decimal import Decimal


def _valor_monetario(valor):
    return (valor or Decimal("0.00")).quantize(Decimal("0.01"))


# ─────────────────────────────────────────────────────────────────
# CHOICES
# ─────────────────────────────────────────────────────────────────


class StatusRelatorio(models.TextChoices):
    RASCUNHO = "rascunho", "Rascunho"
    CONFERENCIA = "conferencia_pendente", "Conferência pendente"
    AJUSTE = "ajuste_pendente", "Ajuste pendente"
    APROVADO = "aprovado", "Aprovado"
    REJEITADO = "rejeitado", "Rejeitado"


class StatusFinanceiroItem(models.TextChoices):
    APROVADO = "aprovado", "Aprovado"
    REJEITADO = "rejeitado", "Rejeitado"


class StatusRateio(models.TextChoices):
    AUTO = "auto", "Automático"
    ADJUSTED = "adjusted", "Ajustado"
    APPROVED = "approved", "Aprovado"


class TipoEventoHistorico(models.TextChoices):
    CRIADO = "criado", "Relatório criado"
    ENVIADO = "enviado", "Relatório enviado para conferência"
    AJUSTE_SOLICITADO = "ajuste_solicitado", "Financeiro solicitou ajustes"
    REENVIADO = "reenviado", "Relatório reenviado para conferência"
    APROVADO = "aprovado", "Relatório aprovado"
    REJEITADO = "rejeitado", "Relatório rejeitado definitivamente"
    ITEM_REJEITADO = "item_rejeitado", "Item rejeitado pelo financeiro"
    ITEM_REATIVADO = "item_reativado", "Item reativado pelo financeiro"
    VALOR_ALTERADO = "valor_alterado", "Valor aprovado alterado"


class TipoLocalidade(models.TextChoices):
    CAPITAL = "capital", "Capital"
    INTERIOR = "interior", "Interior"


class TipoDespesa(models.TextChoices):
    ALIMENTACAO = "alimentacao", "Alimentação"
    HOSPEDAGEM = "hospedagem", "Hospedagem"
    COMBUSTIVEL = "combustivel", "Combustível"
    PEDAGIO = "pedagio", "Pedágio"
    TRANSPORTE = "transporte", "Transporte"
    ESTACIONAMENTO = "estacionamento", "Estacionamento"
    MATERIAL = "material", "Material / Ferramentas"
    COMUNICACAO = "comunicacao", "Comunicação / Telefone"
    OUTROS = "outros", "Outros"


class QuemPagou(models.TextChoices):
    TECNICO = "tecnico", "Técnico"
    EMPRESA = "empresa", "Empresa"


class PapelTecnico(models.TextChoices):
    RESPONSAVEL = "responsavel", "Responsável"
    APOIO = "apoio", "Apoio"


class UF(models.TextChoices):
    AC = "AC", "Acre"
    AL = "AL", "Alagoas"
    AP = "AP", "Amapá"
    AM = "AM", "Amazonas"
    BA = "BA", "Bahia"
    CE = "CE", "Ceará"
    DF = "DF", "Distrito Federal"
    ES = "ES", "Espírito Santo"
    GO = "GO", "Goiás"
    MA = "MA", "Maranhão"
    MT = "MT", "Mato Grosso"
    MS = "MS", "Mato Grosso do Sul"
    MG = "MG", "Minas Gerais"
    PA = "PA", "Pará"
    PB = "PB", "Paraíba"
    PR = "PR", "Paraná"
    PE = "PE", "Pernambuco"
    PI = "PI", "Piauí"
    RJ = "RJ", "Rio de Janeiro"
    RN = "RN", "Rio Grande do Norte"
    RS = "RS", "Rio Grande do Sul"
    RO = "RO", "Rondônia"
    RR = "RR", "Roraima"
    SC = "SC", "Santa Catarina"
    SP = "SP", "São Paulo"
    SE = "SE", "Sergipe"
    TO = "TO", "Tocantins"


# ─────────────────────────────────────────────────────────────────
# TECNICO
# ─────────────────────────────────────────────────────────────────


class Tecnico(models.Model):
    nome = models.CharField("Nome completo", max_length=150)
    email = models.EmailField("E-mail", unique=True)
    telefone = models.CharField("Telefone", max_length=20, blank=True)
    ativo = models.BooleanField("Ativo", default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Técnico"
        verbose_name_plural = "Técnicos"
        ordering = ["nome"]

    def __str__(self):
        return self.nome


# ─────────────────────────────────────────────────────────────────
# CLIENTE
# ─────────────────────────────────────────────────────────────────
class Cliente(models.Model):
    nome = models.CharField("Nome / Razão Social", max_length=200)

    cnpj_cpf = models.CharField(
        "CNPJ / CPF",
        max_length=20,
        blank=True,
        null=True,
        unique=True,
    )

    cidade = models.CharField(
        "Cidade",
        max_length=100,
        blank=True,
        null=True,
    )

    uf = models.CharField(
        "UF",
        max_length=2,
        choices=UF.choices,
        blank=True,
        null=True,
    )

    contato = models.CharField(
        "Contato",
        max_length=100,
        blank=True,
        null=True,
    )

    telefone = models.CharField(
        "Telefone",
        max_length=20,
        blank=True,
        null=True,
    )

    email = models.EmailField(
        "E-mail",
        blank=True,
        null=True,
    )

    ativo = models.BooleanField("Ativo", default=True)

    valor_km = models.DecimalField(
        "Valor por KM (R$)",
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
    )

    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Cliente"
        verbose_name_plural = "Clientes"
        ordering = ["nome"]

    def __str__(self):
        return self.nome

    @property
    def cidade_uf(self):
        if self.cidade and self.uf:
            return f"{self.cidade}/{self.uf}"
        return self.cidade or self.uf or "-"


# ─────────────────────────────────────────────────────────────────
# POLÍTICA DE VALORES
# ─────────────────────────────────────────────────────────────────


class PoliticaValor(models.Model):
    tipo_despesa = models.CharField(
        "Tipo de despesa",
        max_length=30,
        choices=TipoDespesa.choices,
        blank=True,
    )
    descricao = models.CharField("Descrição", max_length=100)
    limite_valor = models.DecimalField(
        "Limite de valor (R$)",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    valor_km = models.DecimalField(
        "Valor por km (R$)",
        max_digits=6,
        decimal_places=4,
        null=True,
        blank=True,
    )
    vigencia_inicio = models.DateField("Vigência início")
    vigencia_fim = models.DateField("Vigência fim", null=True, blank=True)
    ativo = models.BooleanField("Ativo", default=True)

    class Meta:
        verbose_name = "Política de Valor"
        verbose_name_plural = "Políticas de Valores"
        ordering = ["-vigencia_inicio"]

    def __str__(self):
        return f"{self.descricao} — {self.limite_valor or self.valor_km}"

    @classmethod
    def limite_para(cls, tipo_despesa, data):
        p = (
            cls.objects.filter(
                tipo_despesa=tipo_despesa,
                ativo=True,
                vigencia_inicio__lte=data,
            )
            .filter(
                models.Q(vigencia_fim__isnull=True) | models.Q(vigencia_fim__gte=data)
            )
            .first()
        )
        return p.limite_valor if p else None

    @classmethod
    def valor_km_vigente(cls, data):
        p = (
            cls.objects.filter(
                valor_km__isnull=False,
                ativo=True,
                vigencia_inicio__lte=data,
            )
            .filter(
                models.Q(vigencia_fim__isnull=True) | models.Q(vigencia_fim__gte=data)
            )
            .first()
        )
        return p.valor_km if p else Decimal("0.00")


# ─────────────────────────────────────────────────────────────────
# RELATÓRIO TÉCNICO
# ─────────────────────────────────────────────────────────────────


class RelatorioTecnico(models.Model):
    # Identificação
    numero = models.CharField(
        "Número",
        max_length=30,
        unique=True,
        null=True,
        blank=True,
    )
    status = models.CharField(
        "Status",
        max_length=30,
        choices=StatusRelatorio.choices,
        default=StatusRelatorio.RASCUNHO,
    )

    # Vínculos
    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.PROTECT,
        related_name="relatorios",
        verbose_name="Cliente",
    )
    tecnico_responsavel = models.ForeignKey(
        Tecnico,
        on_delete=models.PROTECT,
        related_name="relatorios_responsavel",
        verbose_name="Técnico responsável",
    )
    tecnicos_adicionais = models.ManyToManyField(
        Tecnico,
        through="RelatorioTecnicoEquipe",
        related_name="relatorios_equipe",
        blank=True,
        verbose_name="Equipe adicional",
    )

    # Atendimento
    cidade_atendimento = models.CharField("Cidade de atendimento", max_length=100)
    uf_atendimento = models.CharField(
        "UF",
        max_length=2,
        choices=UF.choices,
        default=UF.PR,
    )
    tipo_localidade = models.CharField(
        "Tipo de localidade",
        max_length=10,
        choices=TipoLocalidade.choices,
        default=TipoLocalidade.INTERIOR,
    )
    data_inicio = models.DateField("Data início")
    data_fim = models.DateField("Data fim")
    motivo = models.TextField("Motivo / Descrição do serviço")

    # Centro de custo único para todo o relatório (movido de ItemDespesa)
    centro_custo = models.CharField(
        "Centro de custo / Classificação",
        max_length=100,
        blank=True,
        help_text="Aplicado a todos os itens deste relatório.",
    )

    # Financeiro
    valor_adiantamento = models.DecimalField(
        "Adiantamento recebido (R$)",
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )

    observacoes = models.TextField("Observações gerais", blank=True)
    motivo_rejeicao = models.TextField("Justificativa financeira", blank=True)
    aprovado_em = models.DateTimeField("Aprovado em", null=True, blank=True)
    aprovado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="relatorios_aprovados",
        verbose_name="Aprovado por",
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="relatorios_criados",
        verbose_name="Criado por",
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Relatório Técnico"
        verbose_name_plural = "Relatórios Técnicos"
        ordering = ["-data_inicio", "-criado_em"]

    def __str__(self):
        return f"{self.identificador} — {self.cliente}"

    @property
    def identificador(self):
        return self.numero or f"Rascunho #{self.pk or 'novo'}"

    def clientes_relacionados(self):
        clientes = Cliente.objects.filter(relatorios_cliente__relatorio=self).order_by(
            "relatorios_cliente__ordem",
            "nome",
        )
        if clientes.exists():
            return clientes
        if self.cliente_id:
            return Cliente.objects.filter(pk=self.cliente_id)
        return Cliente.objects.none()

    def tem_multiplos_clientes(self):
        return self.clientes_relacionados().count() > 1

    def clientes_exibicao(self):
        prefetched = getattr(self, "_prefetched_objects_cache", {})
        if "clientes_vinculados" in prefetched:
            vinculos = sorted(
                prefetched["clientes_vinculados"],
                key=lambda vinculo: (vinculo.ordem, vinculo.cliente.nome),
            )
        else:
            vinculos = list(
                self.clientes_vinculados.select_related("cliente").order_by(
                    "ordem", "cliente__nome"
                )
            )
        clientes = [vinculo.cliente for vinculo in vinculos]
        if not clientes and self.cliente_id:
            clientes = [self.cliente]
        return clientes

    def cliente_principal_exibicao(self):
        clientes = self.clientes_exibicao()
        return clientes[0] if clientes else None

    def clientes_secundarios_exibicao(self):
        return self.clientes_exibicao()[1:]

    def clientes_total_exibicao(self):
        return len(self.clientes_exibicao())

    def tecnicos_exibicao(self):
        tecnicos = []
        vistos = set()
        if self.tecnico_responsavel_id:
            tecnicos.append(self.tecnico_responsavel)
            vistos.add(self.tecnico_responsavel_id)
        prefetched = getattr(self, "_prefetched_objects_cache", {})
        if "equipe" in prefetched:
            equipe = sorted(
                prefetched["equipe"],
                key=lambda membro: membro.tecnico.nome,
            )
        else:
            equipe = self.equipe.select_related("tecnico").order_by("tecnico__nome")
        for membro in equipe:
            if membro.tecnico_id in vistos:
                continue
            vistos.add(membro.tecnico_id)
            tecnicos.append(membro.tecnico)
        return tecnicos

    def tecnico_principal_exibicao(self):
        tecnicos = self.tecnicos_exibicao()
        return tecnicos[0] if tecnicos else None

    def tecnicos_secundarios_exibicao(self):
        return self.tecnicos_exibicao()[1:]

    def tecnicos_total_exibicao(self):
        return len(self.tecnicos_exibicao())

    # ── Financeiro ──────────────────────────────────────────────

    @property
    def total_despesas_tecnico(self):
        total = self.despesas.filter(quem_pagou=QuemPagou.TECNICO).aggregate(
            t=models.Sum("valor")
        )["t"] or Decimal("0.00")
        return _valor_monetario(total)

    @property
    def total_despesas_empresa(self):
        total = self.despesas.filter(quem_pagou=QuemPagou.EMPRESA).aggregate(
            t=models.Sum("valor")
        )["t"] or Decimal("0.00")
        return _valor_monetario(total)

    @property
    def total_km(self):
        total = self.trechos.aggregate(t=models.Sum("valor_calculado"))["t"] or Decimal(
            "0.00"
        )
        return _valor_monetario(total)

    @property
    def total_despesas(self):
        return _valor_monetario(
            self.total_despesas_tecnico + self.total_despesas_empresa + self.total_km
        )

    @property
    def total_solicitado(self):
        return self.total_despesas

    @property
    def total_aprovado_despesas(self):
        total = sum(
            (despesa.valor_final for despesa in self.despesas.all()),
            Decimal("0.00"),
        )
        return _valor_monetario(total)

    @property
    def total_aprovado_km(self):
        total = sum(
            (trecho.valor_final for trecho in self.trechos.all()),
            Decimal("0.00"),
        )
        return _valor_monetario(total)

    @property
    def total_aprovado(self):
        return _valor_monetario(self.total_aprovado_despesas + self.total_aprovado_km)

    @property
    def diferenca_removida(self):
        diferenca = self.total_solicitado - self.total_aprovado
        return _valor_monetario(diferenca if diferenca > 0 else Decimal("0.00"))

    @property
    def saldo(self):
        return _valor_monetario(
            self.total_despesas_tecnico + self.total_km - self.valor_adiantamento
        )

    @property
    def saldo_aprovado(self):
        total_empresa_aprovado = sum(
            (
                despesa.valor_final
                for despesa in self.despesas.filter(quem_pagou=QuemPagou.EMPRESA)
            ),
            Decimal("0.00"),
        )
        return _valor_monetario(
            self.total_aprovado
            - _valor_monetario(total_empresa_aprovado)
            - self.valor_adiantamento
        )

    @property
    def total_km_percorrido(self):
        return self.trechos.aggregate(t=models.Sum("km"))["t"] or Decimal("0.00")

    @property
    def status_badge_cor(self):
        return {
            StatusRelatorio.RASCUNHO: "secondary",
            StatusRelatorio.CONFERENCIA: "warning",
            StatusRelatorio.AJUSTE: "orange",
            StatusRelatorio.APROVADO: "success",
            StatusRelatorio.REJEITADO: "danger",
        }.get(self.status, "secondary")

    def clean(self):
        if self.data_fim and self.data_inicio:
            if self.data_fim < self.data_inicio:
                raise ValidationError(
                    {"data_fim": "Data fim não pode ser anterior à data início."}
                )

    def pode_enviar(self):
        erros = []
        if not self.despesas.exists() and not self.trechos.exists():
            erros.append("Adicione pelo menos uma despesa ou trecho de KM.")
        return erros


class HistoricoRelatorio(models.Model):
    relatorio = models.ForeignKey(
        RelatorioTecnico,
        on_delete=models.CASCADE,
        related_name="historicos",
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="historicos_relatorios",
    )
    acao = models.CharField("Ação", max_length=100)
    tipo_evento = models.CharField(
        "Tipo de evento",
        max_length=30,
        choices=TipoEventoHistorico.choices,
        default=TipoEventoHistorico.CRIADO,
        db_index=True,
    )
    descricao = models.TextField("Descrição", blank=True)
    created_at = models.DateTimeField("Criado em", auto_now_add=True)
    data_hora = models.DateTimeField("Data/hora", auto_now_add=True, db_index=True)
    dados_json = models.JSONField("Dados JSON", default=dict, blank=True)

    class Meta:
        verbose_name = "Histórico do Relatório"
        verbose_name_plural = "Históricos dos Relatórios"
        ordering = ["-data_hora", "-created_at"]

    def __str__(self):
        return f"{self.relatorio.numero} — {self.acao}"

    @property
    def badge_cor(self):
        return {
            TipoEventoHistorico.CRIADO: "secondary",
            TipoEventoHistorico.ENVIADO: "warning",
            TipoEventoHistorico.AJUSTE_SOLICITADO: "orange",
            TipoEventoHistorico.REENVIADO: "warning",
            TipoEventoHistorico.APROVADO: "success",
            TipoEventoHistorico.REJEITADO: "danger",
            TipoEventoHistorico.ITEM_REJEITADO: "danger",
            TipoEventoHistorico.ITEM_REATIVADO: "primary",
            TipoEventoHistorico.VALOR_ALTERADO: "info",
        }.get(self.tipo_evento, "secondary")


def registrar_historico(relatorio, usuario, acao, descricao, dados_json=None):
    from .services.historico_service import registrar_evento

    return registrar_evento(
        relatorio=relatorio,
        usuario=usuario,
        tipo_evento=acao,
        descricao=descricao,
        dados_json=dados_json,
    )


class SequencialRelatorio(models.Model):
    chave = models.CharField("Chave", max_length=50, unique=True)
    proximo_numero = models.PositiveIntegerField("Próximo número", default=1)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Sequencial de Relatório"
        verbose_name_plural = "Sequenciais de Relatórios"

    def __str__(self):
        return f"{self.chave}: {self.proximo_numero}"


# ─────────────────────────────────────────────────────────────────
# EQUIPE DO RELATÓRIO
# ─────────────────────────────────────────────────────────────────


class RelatorioTecnicoEquipe(models.Model):
    relatorio = models.ForeignKey(
        RelatorioTecnico,
        on_delete=models.CASCADE,
        related_name="equipe",
    )
    tecnico = models.ForeignKey(
        Tecnico,
        on_delete=models.PROTECT,
    )
    papel = models.CharField(
        "Papel",
        max_length=15,
        choices=PapelTecnico.choices,
        default=PapelTecnico.APOIO,
    )

    class Meta:
        verbose_name = "Técnico da Equipe"
        verbose_name_plural = "Técnicos da Equipe"
        unique_together = [("relatorio", "tecnico")]

    def __str__(self):
        return f"{self.tecnico.nome} ({self.get_papel_display()})"


class RelatorioCliente(models.Model):
    relatorio = models.ForeignKey(
        RelatorioTecnico,
        on_delete=models.CASCADE,
        related_name="clientes_vinculados",
    )
    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.PROTECT,
        related_name="relatorios_cliente",
    )
    ordem = models.PositiveSmallIntegerField("Ordem", default=0)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Cliente do Relatorio"
        verbose_name_plural = "Clientes do Relatorio"
        ordering = ["ordem", "cliente__nome"]
        unique_together = [("relatorio", "cliente")]

    def __str__(self):
        return f"{self.relatorio.identificador} - {self.cliente}"


# ─────────────────────────────────────────────────────────────────
# ITEM DE DESPESA
# ─────────────────────────────────────────────────────────────────


class ItemDespesa(models.Model):
    relatorio = models.ForeignKey(
        RelatorioTecnico,
        on_delete=models.CASCADE,
        related_name="despesas",
    )
    ordem = models.PositiveSmallIntegerField("Ordem", default=0)
    data = models.DateField("Data", null=True, blank=True)
    tipo = models.CharField(
        "Tipo",
        max_length=20,
        choices=TipoDespesa.choices,
    )
    descricao = models.CharField("Descrição / Fornecedor", max_length=255)
    valor = models.DecimalField(
        "Valor (R$)",
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    valor_aprovado = models.DecimalField(
        "Valor aprovado (R$)",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    status_financeiro = models.CharField(
        "Status financeiro",
        max_length=10,
        choices=StatusFinanceiroItem.choices,
        default=StatusFinanceiroItem.APROVADO,
    )
    motivo_recusa = models.TextField("Motivo da recusa", blank=True)
    rejeitado = models.BooleanField("Rejeitado pelo financeiro", default=False)
    motivo_rejeicao = models.TextField("Motivo da rejeição", blank=True)
    rejeitado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="despesas_rejeitadas",
        verbose_name="Rejeitado por",
    )
    rejeitado_em = models.DateTimeField("Rejeitado em", null=True, blank=True)
    quem_pagou = models.CharField(
        "Quem pagou",
        max_length=10,
        choices=QuemPagou.choices,
        default=QuemPagou.TECNICO,
    )
    comprovante = models.FileField(
        "Comprovante",
        upload_to="comprovantes/%Y/%m/",
        blank=True,
        null=True,
    )
    observacoes = models.TextField("Observações", blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Item de Despesa"
        verbose_name_plural = "Itens de Despesa"
        ordering = ["ordem", "data", "tipo"]

    def __str__(self):
        return f"{self.get_tipo_display()} — R$ {self.valor}"

    @property
    def valor_final(self):
        if self.rejeitado or self.status_financeiro == StatusFinanceiroItem.REJEITADO:
            return Decimal("0.00")
        return _valor_monetario(
            self.valor_aprovado if self.valor_aprovado is not None else self.valor
        )

    @property
    def valor_ajustado(self):
        return (
            not self.rejeitado
            and self.status_financeiro == StatusFinanceiroItem.APROVADO
            and self.valor_aprovado is not None
            and self.valor_aprovado != self.valor
        )

    def clean(self):
        erros = {}
        if self.relatorio_id and self.data:
            rel = self.relatorio
            if self.data < rel.data_inicio or self.data > rel.data_fim:
                erros["data"] = (
                    f"Data fora do período do relatório "
                    f"({rel.data_inicio:%d/%m/%Y} a {rel.data_fim:%d/%m/%Y})."
                )
        if self.tipo and self.data and self.valor:
            limite = PoliticaValor.limite_para(self.tipo, self.data)
            if limite and self.valor > limite:
                erros["valor"] = (
                    f"Limite para {self.get_tipo_display()} é "
                    f"R$ {limite:.2f}. Informado: R$ {self.valor:.2f}."
                )
        if erros:
            raise ValidationError(erros)


class DespesaCliente(models.Model):
    despesa = models.ForeignKey(
        ItemDespesa,
        on_delete=models.CASCADE,
        related_name="clientes_vinculados",
    )
    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.PROTECT,
        related_name="despesas_cliente",
    )

    class Meta:
        verbose_name = "Cliente da Despesa"
        verbose_name_plural = "Clientes da Despesa"
        unique_together = [("despesa", "cliente")]

    def __str__(self):
        return f"{self.despesa_id} - {self.cliente}"


# ─────────────────────────────────────────────────────────────────
# TRECHO DE KM
# ─────────────────────────────────────────────────────────────────


class DespesaRateio(models.Model):
    despesa = models.ForeignKey(
        ItemDespesa,
        on_delete=models.CASCADE,
        related_name="rateios",
    )
    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.PROTECT,
        related_name="rateios_despesas",
    )
    valor_original = models.DecimalField(max_digits=10, decimal_places=2)
    valor_final = models.DecimalField(max_digits=10, decimal_places=2)
    percentual = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=10,
        choices=StatusRateio.choices,
        default=StatusRateio.AUTO,
    )
    alterado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rateios_despesa_alterados",
    )
    motivo_ajuste = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Rateio da Despesa"
        verbose_name_plural = "Rateios das Despesas"
        ordering = ["cliente__nome"]
        unique_together = [("despesa", "cliente")]

    def __str__(self):
        return f"{self.despesa_id} - {self.cliente} - R$ {self.valor_final}"


class TrechoKm(models.Model):
    relatorio = models.ForeignKey(
        RelatorioTecnico,
        on_delete=models.CASCADE,
        related_name="trechos",
    )
    ordem = models.PositiveSmallIntegerField("Ordem", default=0)
    data = models.DateField("Data", null=True, blank=True)
    origem = models.CharField("Origem", max_length=150)
    destino = models.CharField("Destino", max_length=150)
    km = models.DecimalField(
        "Quilômetros",
        max_digits=8,
        decimal_places=1,
        validators=[MinValueValidator(Decimal("0.1"))],
    )
    valor_km = models.DecimalField(
        "Valor por km (R$)",
        max_digits=6,
        decimal_places=4,
        default=Decimal("0.00"),
    )
    valor_km_aprovado = models.DecimalField(
        "Valor por km aprovado (R$)",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    status_financeiro = models.CharField(
        "Status financeiro",
        max_length=10,
        choices=StatusFinanceiroItem.choices,
        default=StatusFinanceiroItem.APROVADO,
    )
    motivo_recusa = models.TextField("Motivo da recusa", blank=True)
    rejeitado = models.BooleanField("Rejeitado pelo financeiro", default=False)
    motivo_rejeicao = models.TextField("Motivo da rejeição", blank=True)
    rejeitado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trechos_km_rejeitados",
        verbose_name="Rejeitado por",
    )
    rejeitado_em = models.DateTimeField("Rejeitado em", null=True, blank=True)
    valor_calculado = models.DecimalField(
        "Valor calculado (R$)",
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        editable=False,
    )
    observacao = models.CharField("Observação", max_length=255, blank=True)

    class Meta:
        verbose_name = "Trecho de KM"
        verbose_name_plural = "Trechos de KM"
        ordering = ["ordem", "data"]

    def __str__(self):
        return f"{self.origem} → {self.destino} ({self.km} km)"

    @property
    def valor_km_final(self):
        return (
            self.valor_km_aprovado
            if self.valor_km_aprovado is not None
            else self.valor_km
        )

    @property
    def valor_final(self):
        if self.rejeitado or self.status_financeiro == StatusFinanceiroItem.REJEITADO:
            return Decimal("0.00")
        return _valor_monetario(self.km * self.valor_km_final)

    @property
    def valor_ajustado(self):
        return (
            not self.rejeitado
            and self.status_financeiro == StatusFinanceiroItem.APROVADO
            and self.valor_km_aprovado is not None
            and self.valor_km_aprovado != self.valor_km
        )

    def save(self, *args, **kwargs):
        if not self.valor_km and self.data:
            self.valor_km = PoliticaValor.valor_km_vigente(self.data)
        self.valor_calculado = (self.km * self.valor_km).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)

    def clean(self):
        if self.relatorio_id and self.data:
            rel = self.relatorio
            if self.data < rel.data_inicio or self.data > rel.data_fim:
                raise ValidationError(
                    {
                        "data": (
                            f"Data fora do período do relatório "
                            f"({rel.data_inicio:%d/%m/%Y} a {rel.data_fim:%d/%m/%Y})."
                        )
                    }
                )

    @property
    def km_fora_politica(self):
        valor_km_cliente = None
        if self.relatorio and self.relatorio.cliente:
            valor_km_cliente = self.relatorio.cliente.valor_km
        if valor_km_cliente is None:
            return False
        return self.valor_km != valor_km_cliente


class TrechoKMCliente(models.Model):
    trecho = models.ForeignKey(
        TrechoKm,
        on_delete=models.CASCADE,
        related_name="clientes_vinculados",
    )
    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.PROTECT,
        related_name="trechos_km_cliente",
    )

    class Meta:
        verbose_name = "Cliente do Trecho KM"
        verbose_name_plural = "Clientes dos Trechos KM"
        unique_together = [("trecho", "cliente")]

    def __str__(self):
        return f"{self.trecho_id} - {self.cliente}"


# ─────────────────────────────────────────────────────────────────
# ADIANTAMENTO
# ─────────────────────────────────────────────────────────────────


class TrechoRateioKM(models.Model):
    trecho = models.ForeignKey(
        TrechoKm,
        on_delete=models.CASCADE,
        related_name="rateios",
    )
    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.PROTECT,
        related_name="rateios_trechos_km",
    )
    km_original = models.DecimalField(max_digits=8, decimal_places=1)
    km_final = models.DecimalField(max_digits=8, decimal_places=1)
    valor_rateado = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(
        max_length=10,
        choices=StatusRateio.choices,
        default=StatusRateio.AUTO,
    )
    alterado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rateios_km_alterados",
    )
    motivo_ajuste = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Rateio do Trecho KM"
        verbose_name_plural = "Rateios dos Trechos KM"
        ordering = ["cliente__nome"]
        unique_together = [("trecho", "cliente")]

    def __str__(self):
        return f"{self.trecho_id} - {self.cliente} - R$ {self.valor_rateado}"


class TipoAdiantamento(models.TextChoices):
    ADIANTAMENTO = "adiantamento", "Adiantamento"
    REEMBOLSO = "reembolso", "Reembolso"


class Adiantamento(models.Model):
    tecnico = models.ForeignKey(
        Tecnico,
        on_delete=models.PROTECT,
        related_name="adiantamentos",
    )
    relatorio = models.ForeignKey(
        RelatorioTecnico,
        on_delete=models.SET_NULL,
        related_name="adiantamentos",
        null=True,
        blank=True,
    )
    tipo = models.CharField(
        "Tipo",
        max_length=20,
        choices=TipoAdiantamento.choices,
        default=TipoAdiantamento.ADIANTAMENTO,
    )
    valor = models.DecimalField(
        "Valor (R$)",
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    data = models.DateField("Data")
    descricao = models.CharField("Descrição", max_length=255)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Adiantamento"
        verbose_name_plural = "Adiantamentos"
        ordering = ["-data"]

    def __str__(self):
        return f"{self.get_tipo_display()} — {self.tecnico} — R$ {self.valor}"
