"""
Models v3 — Campo Manager
Mudanças:
- centro_custo movido de ItemDespesa para RelatorioTecnico
- campo reembolsavel removido de ItemDespesa
- RelatorioTecnicoEquipe mantido para múltiplos técnicos
"""

import mimetypes
import unicodedata

from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
from django.utils import timezone
from decimal import Decimal

from .storage import anexos_storage
from .validators import validar_anexo_upload


def _valor_monetario(valor):
    return (valor or Decimal("0.00")).quantize(Decimal("0.01"))


def _tipo_mime_por_nome(nome_arquivo):
    tipo_mime, _encoding = mimetypes.guess_type(nome_arquivo or "")
    return tipo_mime or "application/octet-stream"


def valor_km_control_sul():
    try:
        from relatorios.services.politica_valor_service import valor_km_control_sul as _valor

        return _valor()
    except Exception:
        return Decimal(str(getattr(settings, "VALOR_KM_CONTROLSUL", "1.35"))).quantize(
            Decimal("0.01")
        )


def _numero_documento_normalizado(valor):
    return " ".join(str(valor or "").strip().split()).upper()


def normalizar_texto_busca(valor):
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return " ".join(texto.lower().strip().split())


# ─────────────────────────────────────────────────────────────────
# CHOICES
# ─────────────────────────────────────────────────────────────────


class StatusRelatorio(models.TextChoices):
    RASCUNHO = "rascunho", "Rascunho"
    CONFERENCIA = "conferencia_pendente", "Conferência pendente"
    AJUSTE = "ajuste_pendente", "Ajuste pendente"
    APROVADO = "aprovado", "Aprovado"
    REJEITADO = "rejeitado", "Rejeitado"


class TipoRelatorio(models.TextChoices):
    INSTITUCIONAL = "institucional", "Institucional"
    OPERACIONAL = "operacional", "Operacional"


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
    EMAIL_ENVIADO = "email_enviado", "Email enviado"
    EMAIL_FALHA = "email_falha", "Falha no envio de email"


class TipoLocalidade(models.TextChoices):
    CAPITAL = "capital", "Capital"
    INTERIOR = "interior", "Interior"
    FRONTEIRA = "fronteira", "Fronteira"


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


class TipoDocumentoComprovante(models.TextChoices):
    NOTA_FISCAL = "nota_fiscal", "Nota Fiscal"
    RECIBO = "recibo", "Recibo"


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


class PerfilUsuario(models.Model):
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="perfil_usuario",
    )
    cadastro_confirmado_em = models.DateTimeField(
        "Cadastro confirmado em",
        null=True,
        blank=True,
        db_index=True,
    )
    tours_guiados_vistos = models.JSONField(
        "Tours guiados vistos",
        default=dict,
        blank=True,
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Perfil de usuário"
        verbose_name_plural = "Perfis de usuários"

    def __str__(self):
        return f"Perfil de {self.usuario}"


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
    razao_social = models.CharField(
        "Razao social",
        max_length=200,
        blank=True,
    )
    nome_fantasia = models.CharField(
        "Nome fantasia",
        max_length=200,
        blank=True,
    )

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
    cep = models.CharField(
        "CEP",
        max_length=12,
        blank=True,
    )

    uf = models.CharField(
        "UF",
        max_length=2,
        choices=UF.choices,
        blank=True,
        null=True,
    )
    logradouro = models.CharField(
        "Logradouro",
        max_length=200,
        blank=True,
    )
    numero = models.CharField(
        "Numero",
        max_length=30,
        blank=True,
    )
    bairro = models.CharField(
        "Bairro",
        max_length=100,
        blank=True,
    )
    complemento = models.CharField(
        "Complemento",
        max_length=150,
        blank=True,
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
    valor_km_atualizado_em = models.DateTimeField(
        "Valor KM atualizado em",
        blank=True,
        null=True,
    )
    valor_km_atualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Valor KM atualizado por",
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="clientes_valor_km_atualizados",
    )
    valor_km_observacao = models.TextField("Observacao do valor KM", blank=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    api_created_at = models.DateTimeField("Criado na API", blank=True, null=True)
    api_updated_at = models.DateTimeField("Atualizado na API", blank=True, null=True)
    sincronizado_em = models.DateTimeField(
        "Sincronizado em",
        blank=True,
        null=True,
        db_index=True,
    )
    origem_api = models.BooleanField("Origem API", default=False, db_index=True)
    hash_dados_api = models.CharField(
        "Hash dos dados da API",
        max_length=64,
        blank=True,
        db_index=True,
    )

    class Meta:
        verbose_name = "Cliente"
        verbose_name_plural = "Clientes"
        ordering = ["nome"]

    def __str__(self):
        return self.nome_exibicao

    @property
    def nome_exibicao(self):
        return self.nome_fantasia or self.razao_social or self.nome

    @property
    def cidade_uf(self):
        if self.cidade and self.uf:
            return f"{self.cidade}/{self.uf}"
        return self.cidade or self.uf or "-"


class Municipio(models.Model):
    codigo_ibge = models.CharField("Código IBGE", max_length=7, unique=True)
    nome = models.CharField("Município", max_length=120)
    nome_normalizado = models.CharField("Nome normalizado", max_length=120, db_index=True)
    uf = models.CharField("UF", max_length=2, choices=UF.choices, db_index=True)
    uf_nome = models.CharField("Nome da UF", max_length=80)
    eh_capital = models.BooleanField("É capital", default=False)
    tipo_localidade_padrao = models.CharField(
        "Localidade padrão",
        max_length=12,
        choices=TipoLocalidade.choices,
        default=TipoLocalidade.INTERIOR,
    )
    aliases = models.JSONField("Aliases", default=list, blank=True)
    ativo = models.BooleanField("Ativo", default=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Município"
        verbose_name_plural = "Municípios"
        ordering = ["nome", "uf"]
        indexes = [
            models.Index(fields=["uf", "nome_normalizado"]),
            models.Index(fields=["ativo", "nome_normalizado"]),
        ]

    def __str__(self):
        return f"{self.nome}/{self.uf}"

    def save(self, *args, **kwargs):
        self.nome_normalizado = normalizar_texto_busca(self.nome)
        if self.eh_capital and self.tipo_localidade_padrao == TipoLocalidade.INTERIOR:
            self.tipo_localidade_padrao = TipoLocalidade.CAPITAL
        super().save(*args, **kwargs)

    @property
    def label(self):
        return f"{self.nome}/{self.uf}"


# ─────────────────────────────────────────────────────────────────
# POLÍTICA DE VALORES
# ─────────────────────────────────────────────────────────────────


class PoliticaValor(models.Model):
    class TipoPolitica(models.TextChoices):
        GERAL = "geral", "Geral"
        REFEICAO = "refeicao", "Refeição"
        PASSAGEM = "passagem", "Passagem"
        HOSPEDAGEM = "hospedagem", "Hospedagem"
        KM_DIARIO = "km_diario", "KM diário / Uber / Táxi"
        VALOR_KM = "valor_km", "Valor KM"

    chave = models.CharField("Chave da política", max_length=80, blank=True, db_index=True)
    tipo_politica = models.CharField(
        "Tipo da política",
        max_length=20,
        choices=TipoPolitica.choices,
        default=TipoPolitica.GERAL,
    )
    tipo_despesa = models.CharField(
        "Tipo de despesa",
        max_length=30,
        choices=TipoDespesa.choices,
        blank=True,
    )
    tipo_localidade = models.CharField(
        "Tipo de localidade",
        max_length=12,
        choices=TipoLocalidade.choices,
        blank=True,
    )
    cidade = models.CharField("Cidade", max_length=80, blank=True)
    origem = models.CharField("Origem", max_length=80, blank=True)
    destino = models.CharField("Destino", max_length=80, blank=True)
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
        chave = f"{self.chave} - " if self.chave else ""
        return f"{chave}{self.descricao} — {self.limite_valor or self.valor_km}"

    @classmethod
    def limite_para(cls, tipo_despesa, data, tipo_localidade=""):
        from relatorios.services.politica_valor_service import resolver_politica_despesa

        politica = resolver_politica_despesa(
            tipo_despesa=tipo_despesa,
            data=data,
            tipo_localidade=tipo_localidade,
        )
        return politica.valor if politica else None

    @classmethod
    def vigente_por_chave(cls, chave, data):
        p = (
            cls.objects.filter(
                chave=chave,
                ativo=True,
                vigencia_inicio__lte=data,
            )
            .filter(
                models.Q(vigencia_fim__isnull=True) | models.Q(vigencia_fim__gte=data)
            )
            .order_by("-vigencia_inicio")
            .first()
        )
        return p

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
    municipio_atendimento = models.ForeignKey(
        Municipio,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="relatorios_atendimento",
        verbose_name="Município de atendimento",
    )
    cidade_atendimento_normalizada = models.CharField(
        "Cidade normalizada",
        max_length=120,
        blank=True,
    )
    uf_atendimento_normalizada = models.CharField(
        "UF normalizada",
        max_length=2,
        blank=True,
    )
    tipo_localidade_calculada = models.CharField(
        "Localidade calculada",
        max_length=12,
        choices=TipoLocalidade.choices,
        blank=True,
    )
    localidade_override = models.BooleanField("Localidade alterada manualmente", default=False)
    motivo_override_localidade = models.TextField("Motivo da alteração de localidade", blank=True)
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

    tipo_relatorio = models.CharField(
        "Tipo de relatorio",
        max_length=20,
        choices=TipoRelatorio.choices,
        default=TipoRelatorio.OPERACIONAL,
    )

    # Financeiro
    valor_adiantamento = models.DecimalField(
        "Adiantamento recebido (R$)",
        max_digits=10,
        decimal_places=2,
        blank=True,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    km_excedente_interno = models.DecimalField(
        "KM excedente / deslocamento interno",
        max_digits=8,
        decimal_places=2,
        blank=True,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    observacao_km_excedente = models.CharField(
        "Observação do KM excedente",
        max_length=255,
        blank=True,
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
        indexes = [
            models.Index(fields=["status", "data_inicio"]),
            models.Index(fields=["data_inicio", "data_fim"]),
            models.Index(fields=["criado_em"]),
        ]

    def __str__(self):
        return f"{self.identificador} — {self.cliente}"

    def sincronizar_municipio_atendimento(self):
        municipio = self.municipio_atendimento
        if not municipio:
            self.cidade_atendimento_normalizada = normalizar_texto_busca(self.cidade_atendimento)
            self.uf_atendimento_normalizada = self.uf_atendimento or ""
            self.tipo_localidade_calculada = self.tipo_localidade or ""
            return
        self.cidade_atendimento = municipio.nome
        self.uf_atendimento = municipio.uf
        self.cidade_atendimento_normalizada = municipio.nome_normalizado
        self.uf_atendimento_normalizada = municipio.uf
        self.tipo_localidade_calculada = municipio.tipo_localidade_padrao
        if not self.localidade_override:
            self.tipo_localidade = municipio.tipo_localidade_padrao

    @property
    def tipo_localidade_efetiva(self):
        if self.localidade_override and self.tipo_localidade:
            return self.tipo_localidade
        if self.municipio_atendimento_id:
            return self.municipio_atendimento.tipo_localidade_padrao
        return self.tipo_localidade

    @property
    def cidade_politica(self):
        if self.municipio_atendimento_id:
            return self.municipio_atendimento.nome
        return self.cidade_atendimento

    @property
    def uf_politica(self):
        if self.municipio_atendimento_id:
            return self.municipio_atendimento.uf
        return self.uf_atendimento

    def save(self, *args, **kwargs):
        self.sincronizar_municipio_atendimento()
        super().save(*args, **kwargs)

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
        prefetched = getattr(self, "_prefetched_objects_cache", {})
        if "despesas" in prefetched:
            total = sum(
                (despesa.valor for despesa in prefetched["despesas"] if despesa.quem_pagou == QuemPagou.TECNICO),
                Decimal("0.00"),
            )
            return _valor_monetario(total)
        total = self.despesas.filter(quem_pagou=QuemPagou.TECNICO).aggregate(
            t=models.Sum("valor")
        )["t"] or Decimal("0.00")
        return _valor_monetario(total)

    @property
    def total_despesas_empresa(self):
        prefetched = getattr(self, "_prefetched_objects_cache", {})
        if "despesas" in prefetched:
            total = sum(
                (despesa.valor for despesa in prefetched["despesas"] if despesa.quem_pagou == QuemPagou.EMPRESA),
                Decimal("0.00"),
            )
            return _valor_monetario(total)
        total = self.despesas.filter(quem_pagou=QuemPagou.EMPRESA).aggregate(
            t=models.Sum("valor")
        )["t"] or Decimal("0.00")
        return _valor_monetario(total)

    @property
    def total_km(self):
        total = Decimal("0.00")
        for trecho in self.trechos.all():
            calculos = list(trecho.rateios.all())
            if calculos:
                total += sum(
                    (calculo.valor_calculado for calculo in calculos),
                    Decimal("0.00"),
                )
            else:
                total += trecho.valor_calculado or Decimal("0.00")
        total += self.total_km_excedente
        return _valor_monetario(total)

    @property
    def total_km_reembolso_tecnico(self):
        total = Decimal("0.00")
        for trecho in self.trechos.all():
            if trecho.rejeitado or trecho.status_financeiro == StatusFinanceiroItem.REJEITADO:
                continue
            total += trecho.valor_reembolso_tecnico
        total += self.total_km_excedente_reembolso_tecnico
        return _valor_monetario(total)

    @property
    def total_km_excesso_reducao_clientes(self):
        return _valor_monetario(self.total_km - self.total_km_reembolso_tecnico)

    def rateio_km_excedente_clientes(self):
        km_total = self.km_excedente_interno or Decimal("0.00")
        clientes = list(self.clientes_exibicao())
        if km_total <= 0 or not clientes:
            return []

        base = (km_total / Decimal(len(clientes))).quantize(Decimal("0.01"))
        linhas = []
        acumulado = Decimal("0.00")
        for idx, cliente in enumerate(clientes):
            km_cliente = base
            if idx == len(clientes) - 1:
                km_cliente = (km_total - acumulado).quantize(Decimal("0.01"))
            acumulado += km_cliente
            valor_km = cliente.valor_km or Decimal("0.00")
            valor_reembolso = valor_km_control_sul()
            linhas.append(
                {
                    "cliente": cliente,
                    "km": km_cliente,
                    "valor_km": valor_km,
                    "valor_calculado": _valor_monetario(km_cliente * valor_km),
                    "valor_km_control_sul": valor_reembolso,
                    "valor_reembolso_tecnico": _valor_monetario(km_cliente * valor_reembolso),
                    "excesso_reducao": _valor_monetario(
                        (km_cliente * valor_km) - (km_cliente * valor_reembolso)
                    ),
                }
            )
        return linhas

    @property
    def total_km_excedente(self):
        return _valor_monetario(
            sum(
                (linha["valor_calculado"] for linha in self.rateio_km_excedente_clientes()),
                Decimal("0.00"),
            )
        )

    @property
    def total_km_excedente_reembolso_tecnico(self):
        return _valor_monetario(
            sum(
                (linha["valor_reembolso_tecnico"] for linha in self.rateio_km_excedente_clientes()),
                Decimal("0.00"),
            )
        )

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
        total = Decimal("0.00")
        for trecho in self.trechos.all():
            calculos = list(trecho.rateios.all())
            if calculos:
                total += sum(
                    (calculo.valor_final for calculo in calculos),
                    Decimal("0.00"),
                )
            else:
                total += trecho.valor_final
        total += self.total_km_excedente
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
            self.total_despesas_tecnico + self.total_km_reembolso_tecnico - self.valor_adiantamento
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
            - self.total_km
            + self.total_km_reembolso_tecnico
            - self.valor_adiantamento
        )

    @property
    def total_km_percorrido(self):
        prefetched = getattr(self, "_prefetched_objects_cache", {})
        if "trechos" in prefetched:
            total = sum((trecho.km for trecho in prefetched["trechos"]), Decimal("0.00"))
        else:
            total = self.trechos.aggregate(t=models.Sum("km"))["t"] or Decimal("0.00")
        return total + (self.km_excedente_interno or Decimal("0.00"))

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
        erros = {}
        if self.data_fim and self.data_inicio:
            if self.data_fim < self.data_inicio:
                erros["data_fim"] = "Data fim não pode ser anterior à data início."
        hoje = timezone.localdate()
        if self.data_inicio and self.data_inicio > hoje:
            erros["data_inicio"] = "Data início não pode ser futura."
        if self.data_fim and self.data_fim > hoje:
            erros["data_fim"] = "Data fim não pode ser futura."

        if self.km_excedente_interno is not None and self.km_excedente_interno < 0:
            erros["km_excedente_interno"] = "KM excedente nao pode ser negativo."

        if erros:
            raise ValidationError(erros)

    def pode_enviar(self):
        erros = []
        if (
            not self.despesas.exists()
            and not self.trechos.exists()
            and (self.km_excedente_interno or Decimal("0.00")) <= 0
        ):
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
            TipoEventoHistorico.EMAIL_ENVIADO: "success",
            TipoEventoHistorico.EMAIL_FALHA: "danger",
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


class RelatorioSnapshotFinanceiro(models.Model):
    relatorio = models.OneToOneField(
        RelatorioTecnico,
        on_delete=models.CASCADE,
        related_name="snapshot_financeiro",
    )
    schema_version = models.PositiveSmallIntegerField(default=1)
    numero = models.CharField(max_length=30, db_index=True)
    status = models.CharField(max_length=30, choices=StatusRelatorio.choices)
    total_solicitado = models.DecimalField(max_digits=12, decimal_places=2)
    total_aprovado = models.DecimalField(max_digits=12, decimal_places=2)
    diferenca_removida = models.DecimalField(max_digits=12, decimal_places=2)
    payload = models.JSONField(default=dict, blank=True)
    checksum = models.CharField(max_length=64, unique=True)
    finalizado_em = models.DateTimeField()
    finalizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="snapshots_financeiros_finalizados",
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Snapshot financeiro do relatorio"
        verbose_name_plural = "Snapshots financeiros dos relatorios"
        ordering = ["-finalizado_em"]
        indexes = [
            models.Index(fields=["status", "finalizado_em"]),
        ]

    def __str__(self):
        return f"Snapshot {self.numero} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        if self.pk and not getattr(self, "_permitir_atualizacao_snapshot", False):
            raise ValidationError("Snapshot financeiro e imutavel.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Snapshot financeiro nao pode ser excluido.")


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
    motivo_viagem = models.TextField("Motivo da viagem", blank=True)
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
        storage=anexos_storage,
        blank=True,
        null=True,
    )
    tipo_documento_comprovante = models.CharField(
        "Tipo do documento",
        max_length=20,
        choices=TipoDocumentoComprovante.choices,
        blank=True,
    )
    numero_documento_comprovante = models.CharField(
        "Nº do documento",
        max_length=80,
        blank=True,
    )
    observacoes = models.TextField("Observações", blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Item de Despesa"
        verbose_name_plural = "Itens de Despesa"
        ordering = ["ordem", "data", "tipo"]
        indexes = [
            models.Index(fields=["relatorio", "data"]),
            models.Index(fields=["relatorio", "tipo"]),
            models.Index(fields=["status_financeiro", "rejeitado"]),
        ]

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

    @property
    def valor_politica(self):
        politica = self.politica_aplicavel
        return politica.valor if politica else None

    @property
    def politica_aplicavel(self):
        if not self.tipo or not self.data:
            return None
        from relatorios.services.politica_valor_service import resolver_politica_despesa

        relatorio = self.relatorio if self.relatorio_id else None
        return resolver_politica_despesa(
            tipo_despesa=self.tipo,
            data=self.data,
            tipo_localidade=getattr(relatorio, "tipo_localidade_efetiva", ""),
            cidade=getattr(relatorio, "cidade_politica", ""),
            municipio=getattr(relatorio, "municipio_atendimento", None),
            descricao=self.descricao,
            valor_informado=self.valor,
        )

    @property
    def excesso_politica(self):
        limite = self.valor_politica
        if limite is None:
            return Decimal("0.00")
        return _valor_monetario(max(self.valor - limite, Decimal("0.00")))

    @property
    def acima_politica(self):
        return self.excesso_politica > Decimal("0.00")

    @property
    def politica_localidade_label(self):
        politica = self.politica_aplicavel
        if politica:
            return politica.descricao
        return "Sem politica definida"

    @property
    def politica_chave(self):
        politica = self.politica_aplicavel
        return politica.chave if politica else ""

    @property
    def politica_tipo(self):
        politica = self.politica_aplicavel
        return politica.tipo_politica if politica else ""

    def clean(self):
        erros = {}
        if self.relatorio_id and self.data:
            rel = self.relatorio
            if self.data < rel.data_inicio or self.data > rel.data_fim:
                erros["data"] = (
                    f"Data fora do período do relatório "
                    f"({rel.data_inicio:%d/%m/%Y} a {rel.data_fim:%d/%m/%Y})."
                )
        if self.data and self.data > timezone.localdate():
            erros["data"] = "Data não pode ser futura."
        try:
            validar_anexo_upload(self.comprovante)
        except ValidationError as exc:
            erros["comprovante"] = exc.messages[0] if exc.messages else str(exc)
        numero_normalizado = _numero_documento_normalizado(self.numero_documento_comprovante)
        if self.numero_documento_comprovante and self.numero_documento_comprovante != numero_normalizado:
            self.numero_documento_comprovante = numero_normalizado

        if (
            self.tipo_documento_comprovante == TipoDocumentoComprovante.NOTA_FISCAL
            and not self.numero_documento_comprovante
        ):
            erros["numero_documento_comprovante"] = (
                "Informe o número do documento para Nota Fiscal."
            )
        if numero_normalizado:
            duplicados = ItemDespesa.objects.filter(
                numero_documento_comprovante__iexact=numero_normalizado
            )
            if self.pk:
                duplicados = duplicados.exclude(pk=self.pk)
            if duplicados.exists():
                erros["numero_documento_comprovante"] = (
                    "Já existe uma despesa cadastrada com este número de nota/documento."
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
    origem_endereco_completo = models.CharField(
        "Endereço completo da origem",
        max_length=255,
        blank=True,
    )
    origem_lat = models.DecimalField(
        "Latitude origem",
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True,
    )
    origem_lon = models.DecimalField(
        "Longitude origem",
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True,
    )
    destino = models.CharField("Destino", max_length=150)
    destino_endereco_completo = models.CharField(
        "Endereço completo do destino",
        max_length=255,
        blank=True,
    )
    destino_lat = models.DecimalField(
        "Latitude destino",
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True,
    )
    destino_lon = models.DecimalField(
        "Longitude destino",
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True,
    )
    km = models.DecimalField(
        "Quilômetros",
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    km_calculado_api = models.DecimalField(
        "KM calculado pela API",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )
    km_informado = models.DecimalField(
        "KM informado",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )
    diferenca_km_percentual = models.DecimalField(
        "Diferença KM (%)",
        max_digits=7,
        decimal_places=2,
        null=True,
        blank=True,
    )
    fonte_calculo_rota = models.CharField(
        "Fonte do cálculo da rota",
        max_length=30,
        blank=True,
        default="",
    )
    calculado_em = models.DateTimeField("Calculado em", null=True, blank=True)
    rota_geojson = models.JSONField("Geometria da rota", default=dict, blank=True)
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
    comprovante = models.FileField(
        "Comprovante",
        upload_to="comprovantes_km/%Y/%m/",
        storage=anexos_storage,
        blank=True,
        null=True,
    )
    tipo_documento_comprovante = models.CharField(
        "Tipo do documento",
        max_length=20,
        choices=TipoDocumentoComprovante.choices,
        blank=True,
    )
    numero_documento_comprovante = models.CharField(
        "Nº do documento",
        max_length=80,
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
        indexes = [
            models.Index(fields=["relatorio", "data"]),
            models.Index(fields=["status_financeiro", "rejeitado"]),
            models.Index(fields=["diferenca_km_percentual"]),
        ]

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
    def valor_km_control_sul(self):
        return valor_km_control_sul()

    @property
    def valor_reembolso_tecnico(self):
        if self.rejeitado or self.status_financeiro == StatusFinanceiroItem.REJEITADO:
            return Decimal("0.00")
        return _valor_monetario(self.km * self.valor_km_control_sul)

    @property
    def excesso_reducao_km(self):
        return _valor_monetario(self.valor_final_clientes - self.valor_reembolso_tecnico)

    @property
    def valor_calculado_clientes(self):
        calculos = list(self.rateios.all())
        if not calculos:
            return self.valor_calculado
        total = sum(
            (calculo.valor_calculado for calculo in calculos),
            Decimal("0.00"),
        )
        return _valor_monetario(total)

    @property
    def valor_final_clientes(self):
        calculos = list(self.rateios.all())
        if not calculos:
            return self.valor_final
        total = sum(
            (calculo.valor_final for calculo in calculos),
            Decimal("0.00"),
        )
        return _valor_monetario(total)

    @property
    def tem_multiplos_clientes(self):
        return self.clientes_vinculados.count() > 1

    @property
    def valor_ajustado(self):
        return (
            not self.rejeitado
            and self.status_financeiro == StatusFinanceiroItem.APROVADO
            and self.valor_km_aprovado is not None
            and self.valor_km_aprovado != self.valor_km
        )

    @property
    def km_divergente_rota(self):
        return (
            self.diferenca_km_percentual is not None
            and self.diferenca_km_percentual > Decimal("15.00")
        )

    def atualizar_dados_geograficos(self):
        self.km_informado = self.km
        if not self.km_calculado_api or self.km_calculado_api <= 0:
            self.diferenca_km_percentual = None
            if not self.fonte_calculo_rota:
                self.fonte_calculo_rota = "manual"
            return

        diferenca = abs(self.km_informado - self.km_calculado_api)
        self.diferenca_km_percentual = (
            (diferenca / self.km_calculado_api) * Decimal("100")
        ).quantize(Decimal("0.01"))
        if not self.fonte_calculo_rota:
            self.fonte_calculo_rota = "OSRM"
        if not self.calculado_em:
            self.calculado_em = timezone.now()

    def save(self, *args, **kwargs):
        if not self.valor_km:
            self.valor_km = valor_km_control_sul()
        self.atualizar_dados_geograficos()
        self.valor_calculado = (self.km * self.valor_km).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)

    def clean(self):
        erros = {}
        if self.km_calculado_api is not None and self.km_calculado_api < 0:
            erros["km_calculado_api"] = "KM calculado não pode ser negativo."
        if self.km_informado is not None and self.km_informado < 0:
            erros["km_informado"] = "KM informado não pode ser negativo."
        if self.relatorio_id and self.data:
            rel = self.relatorio
            if self.data < rel.data_inicio or self.data > rel.data_fim:
                erros["data"] = (
                    f"Data fora do período do relatório "
                    f"({rel.data_inicio:%d/%m/%Y} a {rel.data_fim:%d/%m/%Y})."
                )
        if self.data and self.data > timezone.localdate():
            erros["data"] = "Data não pode ser futura."
        try:
            validar_anexo_upload(self.comprovante)
        except ValidationError as exc:
            erros["comprovante"] = exc.messages[0] if exc.messages else str(exc)
        if erros:
            raise ValidationError(erros)

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

class AnexoRelatorio(models.Model):
    relatorio = models.ForeignKey(
        RelatorioTecnico,
        on_delete=models.CASCADE,
        related_name="anexos",
    )
    despesa = models.ForeignKey(
        ItemDespesa,
        on_delete=models.CASCADE,
        related_name="anexos",
        null=True,
        blank=True,
    )
    trecho = models.ForeignKey(
        TrechoKm,
        on_delete=models.CASCADE,
        related_name="anexos",
        null=True,
        blank=True,
    )
    arquivo = models.FileField(
        "Arquivo",
        upload_to="anexos_relatorios/%Y/%m/",
        storage=anexos_storage,
    )
    nome_original = models.CharField("Nome original", max_length=255)
    tipo_mime = models.CharField("Tipo MIME", max_length=120, blank=True)
    tamanho_bytes = models.PositiveBigIntegerField("Tamanho em bytes", default=0)
    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="anexos_relatorios_enviados",
    )
    criado_em = models.DateTimeField("Enviado em", auto_now_add=True)
    observacao = models.TextField("Observacao", blank=True)
    tipo_documento = models.CharField(
        "Tipo do documento",
        max_length=20,
        choices=TipoDocumentoComprovante.choices,
        blank=True,
    )
    numero_documento = models.CharField("Nº do documento", max_length=80, blank=True)

    class Meta:
        verbose_name = "Anexo do Relatorio"
        verbose_name_plural = "Anexos dos Relatorios"
        ordering = ["-criado_em", "-id"]
        indexes = [
            models.Index(fields=["relatorio", "criado_em"]),
            models.Index(fields=["despesa"]),
            models.Index(fields=["trecho"]),
        ]

    def __str__(self):
        return self.nome_original or self.arquivo.name

    def clean(self):
        if self.despesa_id and self.trecho_id:
            raise ValidationError(
                "O anexo deve estar vinculado a despesa ou trecho KM, nao ambos."
            )
        if self.despesa_id and self.relatorio_id and self.despesa.relatorio_id != self.relatorio_id:
            raise ValidationError("A despesa do anexo nao pertence ao relatorio.")
        if self.trecho_id and self.relatorio_id and self.trecho.relatorio_id != self.relatorio_id:
            raise ValidationError("O trecho KM do anexo nao pertence ao relatorio.")
        validar_anexo_upload(self.arquivo)
        if self.tipo_documento == TipoDocumentoComprovante.NOTA_FISCAL and not self.numero_documento:
            raise ValidationError("Informe o número do documento para Nota Fiscal.")

    @classmethod
    def registrar_comprovante(
        cls,
        *,
        relatorio,
        usuario=None,
        despesa=None,
        trecho=None,
        arquivo=None,
        arquivo_original=None,
    ):
        if not relatorio or not arquivo:
            return None
        origem = arquivo_original or arquivo
        defaults = {
            "arquivo": arquivo.name,
            "nome_original": getattr(origem, "name", "") or arquivo.name.rsplit("/", 1)[-1],
            "tipo_mime": getattr(origem, "content_type", "") or _tipo_mime_por_nome(arquivo.name),
            "tamanho_bytes": getattr(origem, "size", None) or getattr(arquivo, "size", None) or 0,
            "enviado_por": usuario if getattr(usuario, "is_authenticated", False) else None,
            "tipo_documento": getattr(despesa or trecho, "tipo_documento_comprovante", "") or "",
            "numero_documento": getattr(despesa or trecho, "numero_documento_comprovante", "") or "",
        }
        filtros = {"relatorio": relatorio}
        if despesa:
            filtros["despesa"] = despesa
            filtros["trecho__isnull"] = True
        elif trecho:
            filtros["trecho"] = trecho
            filtros["despesa__isnull"] = True
        else:
            return None
        anexo, _created = cls.objects.update_or_create(defaults=defaults, **filtros)
        return anexo


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
    km_original = models.DecimalField(max_digits=8, decimal_places=2)
    km_final = models.DecimalField(max_digits=8, decimal_places=2)
    valor_rateado = models.DecimalField(max_digits=10, decimal_places=2)
    km_cliente = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"))
    valor_km = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0.0000"))
    valor_calculado = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    valor_final = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
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
        return f"{self.trecho_id} - {self.cliente} - R$ {self.valor_final}"

    @property
    def valor_km_control_sul(self):
        return valor_km_control_sul()

    @property
    def valor_reembolso_tecnico(self):
        return _valor_monetario((self.km_cliente or Decimal("0.00")) * self.valor_km_control_sul)

    @property
    def excesso_reducao(self):
        return _valor_monetario((self.valor_final or Decimal("0.00")) - self.valor_reembolso_tecnico)


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

