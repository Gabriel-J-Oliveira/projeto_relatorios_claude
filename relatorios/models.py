"""
Models do sistema Campo Manager — v2.
Módulo de prestação de contas com despesas itemizadas e deslocamento por trecho.
"""

from django.db import models
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
from decimal import Decimal


# ─────────────────────────────────────────────────────────────────
# CHOICES
# ─────────────────────────────────────────────────────────────────


class StatusRelatorio(models.TextChoices):
    RASCUNHO = "rascunho", "Rascunho"
    PENDENTE = "pendente", "Pendente aprovação"
    APROVADO = "aprovado", "Aprovado"
    REJEITADO = "rejeitado", "Rejeitado"
    FATURADO = "faturado", "Faturado"


class TipoLocalidade(models.TextChoices):
    CAPITAL = "capital", "Capital"
    INTERIOR = "interior", "Interior"


class TipoDespesa(models.TextChoices):
    ALIMENTACAO = "alimentacao", "Alimentação"
    HOSPEDAGEM = "hospedagem", "Hospedagem"
    COMBUSTIVEL = "combustivel", "Combustível"
    PEDAGIO = "pedagio", "Pedágio"
    TRANSPORTE = "transporte", "Transporte (ônibus/taxi/uber)"
    ESTACIONAMENTO = "estacionamento", "Estacionamento"
    MATERIAL = "material", "Material / Ferramentas"
    COMUNICACAO = "comunicacao", "Comunicação / Telefone"
    OUTROS = "outros", "Outros"


class QuemPagou(models.TextChoices):
    TECNICO = "tecnico", "Técnico (reembolsável)"
    EMPRESA = "empresa", "Empresa (cartão/direto)"


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
        "CNPJ / CPF", max_length=20, blank=True, null=True, unique=True
    )
    cidade = models.CharField("Cidade", max_length=100)
    uf = models.CharField("UF", max_length=2, choices=UF.choices, default=UF.PR)
    contato = models.CharField("Contato", max_length=100, blank=True)
    telefone = models.CharField("Telefone", max_length=20, blank=True)
    email = models.EmailField("E-mail", blank=True)
    ativo = models.BooleanField("Ativo", default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Cliente"
        verbose_name_plural = "Clientes"
        ordering = ["nome"]

    def __str__(self):
        return self.nome

    @property
    def cidade_uf(self):
        return f"{self.cidade}/{self.uf}"


# ─────────────────────────────────────────────────────────────────
# POLITICA DE VALORES (limites e tarifas configuráveis)
# ─────────────────────────────────────────────────────────────────


class PoliticaValor(models.Model):
    """
    Tabela de limites e tarifas.
    Permite configurar: limite de refeição, diária de hotel, valor por km.
    Uma política vigente por vez por tipo.
    """

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
        help_text="Valor máximo por ocorrência. Deixe em branco para sem limite.",
    )
    valor_km = models.DecimalField(
        "Valor por km (R$)",
        max_digits=6,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Preencha apenas para política de quilometragem.",
    )
    vigencia_inicio = models.DateField("Vigência início")
    vigencia_fim = models.DateField("Vigência fim", null=True, blank=True)
    ativo = models.BooleanField("Ativo", default=True)

    class Meta:
        verbose_name = "Política de Valor"
        verbose_name_plural = "Políticas de Valores"
        ordering = ["-vigencia_inicio"]

    def __str__(self):
        return f"{self.descricao} — R$ {self.limite_valor or self.valor_km}"

    @classmethod
    def limite_para(cls, tipo_despesa, data):
        """Retorna o limite vigente para um tipo de despesa em uma data."""
        politica = (
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
        return politica.limite_valor if politica else None

    @classmethod
    def valor_km_vigente(cls, data):
        """Retorna o valor por km vigente em uma data."""
        politica = (
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
        return politica.valor_km if politica else Decimal("0.00")


# ─────────────────────────────────────────────────────────────────
# RELATORIO TECNICO (cabeçalho)
# ─────────────────────────────────────────────────────────────────


class RelatorioTecnico(models.Model):
    # Identificação
    numero = models.CharField("Número", max_length=30, unique=True)
    status = models.CharField(
        "Status",
        max_length=20,
        choices=StatusRelatorio.choices,
        default=StatusRelatorio.RASCUNHO,
    )

    # Vinculações
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

    # Dados do atendimento
    cidade_atendimento = models.CharField("Cidade de atendimento", max_length=100)
    uf_atendimento = models.CharField(
        "UF", max_length=2, choices=UF.choices, default=UF.PR
    )
    tipo_localidade = models.CharField(
        "Tipo de localidade",
        max_length=10,
        choices=TipoLocalidade.choices,
        default=TipoLocalidade.INTERIOR,
    )
    data_inicio = models.DateField("Data início do atendimento")
    data_fim = models.DateField("Data fim do atendimento")
    motivo = models.TextField("Motivo / Descrição do serviço")
    area_gasto = models.CharField(
        "Área / Centro de custo",
        max_length=100,
        blank=True,
        help_text="Ex: Manutenção, Instalação, Comercial...",
    )

    # Financeiro — cabeçalho
    valor_adiantamento = models.DecimalField(
        "Adiantamento recebido (R$)",
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )

    # Observações gerais
    observacoes = models.TextField("Observações gerais", blank=True)

    # Auditoria
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Relatório Técnico"
        verbose_name_plural = "Relatórios Técnicos"
        ordering = ["-data_inicio", "-criado_em"]

    def __str__(self):
        return f"{self.numero} — {self.cliente}"

    # ── Propriedades financeiras calculadas ──────────────────────

    @property
    def total_despesas_tecnico(self):
        """Soma de despesas pagas pelo técnico (reembolsáveis)."""
        return self.despesas.filter(quem_pagou=QuemPagou.TECNICO).aggregate(
            total=models.Sum("valor")
        )["total"] or Decimal("0.00")

    @property
    def total_despesas_empresa(self):
        """Soma de despesas pagas diretamente pela empresa."""
        return self.despesas.filter(quem_pagou=QuemPagou.EMPRESA).aggregate(
            total=models.Sum("valor")
        )["total"] or Decimal("0.00")

    @property
    def total_km(self):
        """Soma do valor calculado de todos os trechos de KM."""
        return self.trechos.aggregate(total=models.Sum("valor_calculado"))[
            "total"
        ] or Decimal("0.00")

    @property
    def total_despesas(self):
        """Total geral: despesas + km."""
        return self.total_despesas_tecnico + self.total_despesas_empresa + self.total_km

    @property
    def saldo(self):
        """
        Saldo a acertar.
        Positivo  → empresa deve reembolsar o técnico.
        Negativo  → técnico deve devolver à empresa.
        """
        return self.total_despesas_tecnico + self.total_km - self.valor_adiantamento

    @property
    def total_km_percorrido(self):
        return self.trechos.aggregate(total=models.Sum("km"))["total"] or Decimal(
            "0.00"
        )

    @property
    def status_badge_cor(self):
        mapa = {
            StatusRelatorio.RASCUNHO: "secondary",
            StatusRelatorio.PENDENTE: "warning",
            StatusRelatorio.APROVADO: "success",
            StatusRelatorio.REJEITADO: "danger",
            StatusRelatorio.FATURADO: "info",
        }
        return mapa.get(self.status, "secondary")

    def clean(self):
        if self.data_fim and self.data_inicio:
            if self.data_fim < self.data_inicio:
                raise ValidationError(
                    {"data_fim": "A data fim não pode ser anterior à data início."}
                )

    def pode_fechar(self):
        """Verifica se o relatório pode ser enviado para aprovação."""
        erros = []
        if not self.despesas.exists() and not self.trechos.exists():
            erros.append("Adicione pelo menos uma despesa ou trecho de KM.")
        return erros


# ─────────────────────────────────────────────────────────────────
# EQUIPE DO RELATÓRIO (técnicos adicionais)
# ─────────────────────────────────────────────────────────────────


class RelatorioTecnicoEquipe(models.Model):
    relatorio = models.ForeignKey(
        RelatorioTecnico,
        on_delete=models.CASCADE,
        related_name="equipe",
        verbose_name="Relatório",
    )
    tecnico = models.ForeignKey(
        Tecnico,
        on_delete=models.PROTECT,
        verbose_name="Técnico",
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


# ─────────────────────────────────────────────────────────────────
# ITEM DE DESPESA (linhas da prestação de contas)
# ─────────────────────────────────────────────────────────────────


class ItemDespesa(models.Model):
    relatorio = models.ForeignKey(
        RelatorioTecnico,
        on_delete=models.CASCADE,
        related_name="despesas",
        verbose_name="Relatório",
    )
    ordem = models.PositiveSmallIntegerField("Ordem", default=0)
    data = models.DateField("Data")
    tipo = models.CharField(
        "Tipo de despesa",
        max_length=20,
        choices=TipoDespesa.choices,
    )
    descricao = models.CharField(
        "Descrição / Fornecedor",
        max_length=255,
    )
    valor = models.DecimalField(
        "Valor (R$)",
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    quem_pagou = models.CharField(
        "Quem pagou",
        max_length=10,
        choices=QuemPagou.choices,
        default=QuemPagou.TECNICO,
    )
    reembolsavel = models.BooleanField(
        "Reembolsável",
        default=True,
        help_text="Despesas pagas pela empresa não são reembolsáveis.",
    )
    centro_custo = models.CharField(
        "Centro de custo / Classificação",
        max_length=100,
        blank=True,
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
        return f"{self.get_tipo_display()} — R$ {self.valor} ({self.data})"

    def clean(self):
        erros = {}

        # Despesa paga pela empresa não pode ser reembolsável
        if self.quem_pagou == QuemPagou.EMPRESA and self.reembolsavel:
            erros["reembolsavel"] = "Despesas pagas pela empresa não são reembolsáveis."

        # Data deve estar dentro do período do relatório
        if self.relatorio_id and self.data:
            rel = self.relatorio
            if self.data < rel.data_inicio or self.data > rel.data_fim:
                erros["data"] = (
                    f"A data deve estar entre "
                    f"{rel.data_inicio.strftime('%d/%m/%Y')} e "
                    f"{rel.data_fim.strftime('%d/%m/%Y')}."
                )

        # Verifica limite da política de valores
        if self.tipo and self.data and self.valor:
            limite = PoliticaValor.limite_para(self.tipo, self.data)
            if limite and self.valor > limite:
                erros["valor"] = (
                    f"O limite para {self.get_tipo_display()} é "
                    f"R$ {limite:.2f}. Valor informado: R$ {self.valor:.2f}."
                )

        if erros:
            raise ValidationError(erros)

    @property
    def eh_reembolsavel_ao_tecnico(self):
        return self.quem_pagou == QuemPagou.TECNICO and self.reembolsavel


# ─────────────────────────────────────────────────────────────────
# TRECHO DE KM (deslocamento por trecho)
# ─────────────────────────────────────────────────────────────────


class TrechoKm(models.Model):
    relatorio = models.ForeignKey(
        RelatorioTecnico,
        on_delete=models.CASCADE,
        related_name="trechos",
        verbose_name="Relatório",
    )
    ordem = models.PositiveSmallIntegerField("Ordem", default=0)
    data = models.DateField("Data")
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
        help_text="Preenchido automaticamente pela política vigente.",
    )
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

    def save(self, *args, **kwargs):
        # Preenche valor_km pela política vigente se não informado
        if not self.valor_km and self.data:
            self.valor_km = PoliticaValor.valor_km_vigente(self.data)
        # Calcula valor automaticamente
        self.valor_calculado = (self.km * self.valor_km).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)

    def clean(self):
        if self.relatorio_id and self.data:
            rel = self.relatorio
            if self.data < rel.data_inicio or self.data > rel.data_fim:
                raise ValidationError(
                    {
                        "data": (
                            f"A data deve estar entre "
                            f"{rel.data_inicio.strftime('%d/%m/%Y')} e "
                            f"{rel.data_fim.strftime('%d/%m/%Y')}."
                        )
                    }
                )


# ─────────────────────────────────────────────────────────────────
# ADIANTAMENTO (mantido separado para histórico financeiro)
# ─────────────────────────────────────────────────────────────────


class TipoAdiantamento(models.TextChoices):
    ADIANTAMENTO = "adiantamento", "Adiantamento"
    REEMBOLSO = "reembolso", "Reembolso"


class Adiantamento(models.Model):
    tecnico = models.ForeignKey(
        Tecnico,
        on_delete=models.PROTECT,
        related_name="adiantamentos",
        verbose_name="Técnico",
    )
    relatorio = models.ForeignKey(
        RelatorioTecnico,
        on_delete=models.SET_NULL,
        related_name="adiantamentos",
        null=True,
        blank=True,
        verbose_name="Relatório vinculado",
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
