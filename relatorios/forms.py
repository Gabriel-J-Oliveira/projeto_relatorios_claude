from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.forms import inlineformset_factory, BaseInlineFormSet
from django.utils import timezone
from .models import (
    RelatorioTecnico,
    ItemDespesa,
    TrechoKm,
    Tecnico,
    Cliente,
    Adiantamento,
    TipoDocumentoComprovante,
)

class ClienteSelectWithData(forms.Select):
    """
    Select customizado que injeta data-valor-km em cada <option>
    a partir de um mapa {pk: valor_km} passado externamente.
    """

    def __init__(self, *args, **kwargs):
        self.clientes_map = kwargs.pop("clientes_map", {})
        super().__init__(*args, **kwargs)

    def create_option(
        self, name, value, label, selected, index, subindex=None, attrs=None
    ):
        option = super().create_option(
            name, value, label, selected, index, subindex=subindex, attrs=attrs
        )

        if value:
            # Django >= 4.0 envolve o valor em ModelChoiceIteratorValue
            value_pk = value.value if hasattr(value, "value") else value
            try:
                valor_km = self.clientes_map.get(int(value_pk), 0) or 0
            except (TypeError, ValueError):
                valor_km = 0

            option["attrs"]["data-valor-km"] = str(valor_km)

        return option


# ─────────────────────────────────────────────
# MIXIN BOOTSTRAP
# ─────────────────────────────────────────────


class BootstrapMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            w = field.widget
            if isinstance(w, forms.CheckboxInput):
                w.attrs.setdefault("class", "form-check-input")
            elif isinstance(w, (forms.Select, forms.SelectMultiple)):
                w.attrs.setdefault("class", "form-select form-select-sm")
            elif isinstance(w, forms.Textarea):
                w.attrs.setdefault("class", "form-control form-control-sm")
                w.attrs.setdefault("rows", 3)
            else:
                w.attrs.setdefault("class", "form-control form-control-sm")


class CompletarCadastroUsuarioForm(BootstrapMixin, forms.Form):
    first_name = forms.CharField(
        label="Nome",
        max_length=150,
        required=True,
        error_messages={"required": "Informe seu nome."},
    )
    last_name = forms.CharField(
        label="Sobrenome",
        max_length=150,
        required=True,
        error_messages={"required": "Informe seu sobrenome."},
    )
    email = forms.EmailField(
        label="E-mail",
        max_length=254,
        required=True,
        error_messages={
            "required": "Informe seu e-mail.",
            "invalid": "Informe um e-mail válido.",
        },
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        initial = kwargs.pop("initial", {}) or {}
        if user is not None:
            initial.setdefault("first_name", user.first_name)
            initial.setdefault("last_name", user.last_name)
            initial.setdefault("email", user.email)
        super().__init__(*args, initial=initial, **kwargs)

    def clean_email(self):
        email = (self.cleaned_data["email"] or "").strip().lower()
        User = get_user_model()
        qs = User.objects.filter(email__iexact=email)
        if self.user is not None and self.user.pk:
            qs = qs.exclude(pk=self.user.pk)
        if qs.exists():
            raise forms.ValidationError("Este e-mail já está em uso por outro usuário.")
        return email


# ─────────────────────────────────────────────
# CABEÇALHO
# ─────────────────────────────────────────────

# forms.py


class RelatorioTecnicoForm(BootstrapMixin, forms.ModelForm):

    tecnicos_equipe = forms.ModelMultipleChoiceField(
        queryset=Tecnico.objects.filter(ativo=True).order_by("nome"),
        required=False,
        label="Equipe (técnicos adicionais)",
        widget=forms.SelectMultiple(
            attrs={"class": "form-select form-select-sm", "size": "5"}
        ),
        help_text="Segure Ctrl (ou Cmd) para selecionar múltiplos.",
    )

    class Meta:
        model = RelatorioTecnico
        fields = [
            "numero",
            "tecnico_responsavel",
            "cidade_atendimento",
            "uf_atendimento",
            "tipo_localidade",
            "data_inicio",
            "data_fim",
            "motivo",
            "centro_custo",
            "tipo_relatorio",
            "valor_adiantamento",
            "km_excedente_interno",
            "observacao_km_excedente",
            "observacoes",
        ]
        widgets = {
            "numero": forms.TextInput(
                attrs={
                    "class": "form-control form-control-sm",
                    "inputmode": "numeric",
                    "readonly": "readonly",
                }
            ),
            "data_inicio": forms.DateInput(
                attrs={"type": "date", "class": "form-control form-control-sm"},
                format="%Y-%m-%d",
            ),
            "data_fim": forms.DateInput(
                attrs={"type": "date", "class": "form-control form-control-sm"},
                format="%Y-%m-%d",
            ),
            "motivo": forms.Textarea(attrs={"rows": 4}),
            "observacoes": forms.Textarea(attrs={"rows": 3}),
            "valor_adiantamento": forms.TextInput(),
            "km_excedente_interno": forms.TextInput(),
            "observacao_km_excedente": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {
            "centro_custo": "Centro de Custo / Classificacao",
            "tipo_relatorio": "Tipo de relatorio",
        }
        help_texts = {
            "centro_custo": "Será aplicado a todas as despesas deste relatório."
        }

    def __init__(self, *args, **kwargs):
        kwargs.pop("numero_sugerido", None)
        super().__init__(*args, **kwargs)

        self.fields["tecnico_responsavel"].queryset = Tecnico.objects.filter(
            ativo=True
        ).order_by("nome")
        for name in ["data_inicio", "data_fim"]:
            self.fields[name].input_formats = ["%Y-%m-%d", "%d/%m/%Y"]
            self.fields[name].widget.attrs["max"] = timezone.localdate().isoformat()
        self.fields["numero"].required = False
        self.fields["numero"].disabled = True
        self.fields["numero"].widget.attrs["placeholder"] = "Gerado no envio"
        self.fields["numero"].help_text = "Gerado automaticamente ao enviar para conferência."
        self.fields["motivo"].required = False
        self.fields["valor_adiantamento"].required = False
        self.fields["km_excedente_interno"].required = False
        self.fields["observacao_km_excedente"].required = False
        if self.instance and self.instance.pk and not self.instance.numero:
            self.fields["numero"].initial = "Rascunho"
        self.fields["centro_custo"].widget.attrs[
            "placeholder"
        ] = "Ex: Manutenção, Instalação, Comercial..."
        self.fields["tipo_relatorio"].widget.attrs.update(
            {"class": "form-select form-select-sm"}
        )
        self.fields["valor_adiantamento"].widget.attrs.update(
            {
                "inputmode": "decimal",
                "class": "form-control form-control-sm campo-moeda",
                "placeholder": "0,00",
            }
        )
        self.fields["km_excedente_interno"].widget.attrs.update(
            {
                "inputmode": "decimal",
                "class": "form-control form-control-sm campo-km",
                "placeholder": "0,00",
            }
        )
        self.fields["observacao_km_excedente"].widget.attrs.update(
            {
                "class": "form-control form-control-sm",
                "placeholder": "Cliente → hotel, hotel → evento, evento → restaurante...",
            }
        )

        if self.instance and self.instance.pk:
            self.fields["tecnicos_equipe"].initial = (
                self.instance.tecnicos_adicionais.values_list("pk", flat=True)
            )

    def clean_numero(self):
        if self.instance.pk:
            return self.instance.numero

        numero = str(self.cleaned_data.get("numero") or "").strip()
        if not numero:
            return None

        qs = RelatorioTecnico.objects.filter(numero=numero)
        if qs.exists():
            raise forms.ValidationError("Número já cadastrado.")
        return numero

    def clean(self):
        cd = super().clean()
        ini = cd.get("data_inicio")
        fim = cd.get("data_fim")
        if ini and fim and fim < ini:
            self.add_error("data_fim", "Data fim não pode ser anterior à data início.")
        hoje = timezone.localdate()
        if ini and ini > hoje:
            self.add_error("data_inicio", "Data início não pode ser futura.")
        if fim and fim > hoje:
            self.add_error("data_fim", "Data fim não pode ser futura.")
        resp = cd.get("tecnico_responsavel")
        equipe = cd.get("tecnicos_equipe", [])
        if resp and resp in equipe:
            self.add_error(
                "tecnicos_equipe",
                "O técnico responsável não deve ser adicionado à equipe adicional.",
            )
        if cd.get("valor_adiantamento") is None:
            cd["valor_adiantamento"] = 0
        if cd.get("km_excedente_interno") is None:
            cd["km_excedente_interno"] = 0
        if cd.get("km_excedente_interno", 0) > 0 and not cd.get("observacao_km_excedente"):
            self.add_error(
                "observacao_km_excedente",
                "Informe uma observação para o KM excedente/deslocamento interno.",
            )
        return cd

    def save(self, commit=True):
        instance = super().save(commit=commit)
        if commit:
            self._salvar_equipe(instance)
        return instance

    def _salvar_equipe(self, instance):
        from .models import RelatorioTecnicoEquipe

        equipe_selecionada = set(
            t.pk for t in self.cleaned_data.get("tecnicos_equipe", [])
        )
        instance.equipe.exclude(tecnico_id__in=equipe_selecionada).delete()
        existentes = set(instance.equipe.values_list("tecnico_id", flat=True))
        for pk in equipe_selecionada - existentes:
            RelatorioTecnicoEquipe.objects.create(relatorio=instance, tecnico_id=pk)


# ─────────────────────────────────────────────
# ITEM DE DESPESA
# ─────────────────────────────────────────────


class ItemDespesaForm(BootstrapMixin, forms.ModelForm):

    class Meta:
        model = ItemDespesa
        fields = [
            "ordem",
            "data",
            "tipo",
            "descricao",
            "valor",
            "quem_pagou",
            "comprovante",
            "tipo_documento_comprovante",
            "numero_documento_comprovante",
            "observacoes",
        ]
        widgets = {
            "data": forms.DateInput(
                attrs={"type": "date", "class": "form-control form-control-sm"},
                format="%Y-%m-%d",
            ),
            "observacoes": forms.Textarea(attrs={"rows": 2}),
            "ordem": forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["data"].input_formats = ["%Y-%m-%d", "%d/%m/%Y"]
        self.fields["data"].widget.attrs["max"] = timezone.localdate().isoformat()
        for name in [
            "data",
            "tipo",
            "descricao",
            "valor",
            "quem_pagou",
            "comprovante",
            "tipo_documento_comprovante",
            "numero_documento_comprovante",
            "observacoes",
        ]:
            self.fields[name].required = False
        self.fields["descricao"].widget.attrs["placeholder"] = "Fornecedor / Detalhe"
        self.fields["valor"].widget.attrs["placeholder"] = "0,00"
        self.fields["valor"].widget.attrs.update(
            {
                "class": "form-control form-control-sm campo-valor-desp campo-moeda",
                "inputmode": "decimal",
            }
        )
        self.fields["comprovante"].widget.attrs.update(
            {
                "class": "form-control form-control-sm text-nowrap",
                "accept": "image/*,.pdf",
            }
        )
        self.fields["tipo_documento_comprovante"].widget.attrs.update(
            {"class": "form-select form-select-sm tipo-documento-comprovante"}
        )
        self.fields["numero_documento_comprovante"].widget.attrs.update(
            {
                "class": "form-control form-control-sm numero-documento-comprovante",
                "placeholder": "Nº do documento",
            }
        )


class BaseItemDespesaFormSet(BaseInlineFormSet):
    def clean(self):
        if any(self.errors):
            return

        relatorio = self.instance

        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue

            if form.cleaned_data.get("DELETE"):
                continue

            data = form.cleaned_data.get("data")
            tipo = form.cleaned_data.get("tipo")
            descricao = form.cleaned_data.get("descricao")
            valor = form.cleaned_data.get("valor")
            quem_pagou = form.cleaned_data.get("quem_pagou")
            comprovante = form.cleaned_data.get("comprovante")
            tipo_documento = form.cleaned_data.get("tipo_documento_comprovante")
            numero_documento = form.cleaned_data.get("numero_documento_comprovante")
            observacoes = form.cleaned_data.get("observacoes")

            # linha vazia
            if not form.instance.pk and not any(
                [data, tipo, descricao, valor, comprovante, observacoes]
            ):
                continue

            if not data:
                form.add_error("data", "Informe a data.")

            if tipo and not valor:
                form.add_error("valor", "Informe o valor.")

            if valor and not tipo:
                form.add_error("tipo", "Selecione o tipo.")

            if not descricao:
                form.add_error("descricao", "Informe a descrição.")

            if valor is not None and valor <= 0:
                form.add_error("valor", "Valor deve ser maior que zero.")
            if data and data > timezone.localdate():
                form.add_error("data", "Data não pode ser futura.")

            if (
                tipo_documento == TipoDocumentoComprovante.NOTA_FISCAL
                and not numero_documento
            ):
                form.add_error(
                    "numero_documento_comprovante",
                    "Informe o número do documento para Nota Fiscal.",
                )

            if data and relatorio and relatorio.data_inicio and relatorio.data_fim:
                if data < relatorio.data_inicio or data > relatorio.data_fim:
                    form.add_error(
                        "data",
                        (
                            f"Data fora do período do relatório "
                            f"({relatorio.data_inicio:%d/%m/%Y} a "
                            f"{relatorio.data_fim:%d/%m/%Y})."
                        ),
                    )


ItemDespesaFormSet = inlineformset_factory(
    RelatorioTecnico,
    ItemDespesa,
    form=ItemDespesaForm,
    formset=BaseItemDespesaFormSet,
    extra=0,
    min_num=0,
    can_delete=True,
)
# ─────────────────────────────────────────────
# TRECHO DE KM
# ─────────────────────────────────────────────


class TrechoKmForm(BootstrapMixin, forms.ModelForm):

    class Meta:
        model = TrechoKm
        fields = [
            "ordem",
            "data",
            "origem",
            "origem_endereco_completo",
            "origem_lat",
            "origem_lon",
            "destino",
            "destino_endereco_completo",
            "destino_lat",
            "destino_lon",
            "km",
            "km_calculado_api",
            "km_informado",
            "diferenca_km_percentual",
            "fonte_calculo_rota",
            "rota_geojson",
            "valor_km",
            "observacao",
        ]
        widgets = {
            "data": forms.DateInput(
                attrs={
                    "type": "date",
                    "class": "form-control form-control-sm",
                },
                format="%Y-%m-%d",
            ),
            "ordem": forms.HiddenInput(),
            "origem_endereco_completo": forms.HiddenInput(),
            "origem_lat": forms.HiddenInput(),
            "origem_lon": forms.HiddenInput(),
            "destino_endereco_completo": forms.HiddenInput(),
            "destino_lat": forms.HiddenInput(),
            "destino_lon": forms.HiddenInput(),
            "km_calculado_api": forms.HiddenInput(),
            "km_informado": forms.HiddenInput(),
            "diferenca_km_percentual": forms.HiddenInput(),
            "fonte_calculo_rota": forms.HiddenInput(),
            "rota_geojson": forms.HiddenInput(attrs={"class": "mapa-rota-geojson"}),
        }

    def __init__(self, *args, **kwargs):
        valor_km_padrao = kwargs.pop("valor_km_padrao", None)
        super().__init__(*args, **kwargs)
        self.fields["data"].input_formats = ["%Y-%m-%d", "%d/%m/%Y"]
        self.fields["data"].widget.attrs["max"] = timezone.localdate().isoformat()
        for name in [
            "data",
            "origem",
            "origem_endereco_completo",
            "origem_lat",
            "origem_lon",
            "destino",
            "destino_endereco_completo",
            "destino_lat",
            "destino_lon",
            "km",
            "km_calculado_api",
            "km_informado",
            "diferenca_km_percentual",
            "fonte_calculo_rota",
            "rota_geojson",
            "valor_km",
            "observacao",
        ]:
            self.fields[name].required = False

        self.fields["origem"].widget.attrs.update(
            {
                "class": "form-control form-control-sm",
                "placeholder": "Endereço origem",
            }
        )

        self.fields["destino"].widget.attrs.update(
            {
                "class": "form-control form-control-sm",
                "placeholder": "Endereço destino",
            }
        )

        self.fields["km"].widget.attrs.update(
            {
                "class": "form-control form-control-sm campo-km",
                "inputmode": "decimal",
                "placeholder": "0,00",
            }
        )
        self.fields["valor_km"].widget.attrs.update(
            {
                "class": "form-control form-control-sm campo-vkm",
                "inputmode": "decimal",
                "placeholder": "0,00",
            }
        )
        self.fields["observacao"].widget.attrs.update(
            {
                "class": "form-control form-control-sm",
                "placeholder": "Observação",
            }
        )

        if not self.is_bound and not self.instance.pk:
            valor_final = None

            if valor_km_padrao not in (None, ""):
                valor_final = valor_km_padrao
            else:
                relatorio = getattr(self.instance, "relatorio", None)
                if relatorio and getattr(relatorio, "cliente", None):
                    valor_final = getattr(relatorio.cliente, "valor_km", None)

            if valor_final not in (None, ""):
                self.fields["valor_km"].initial = valor_final


class BaseTrechoKmFormSet(BaseInlineFormSet):
    def clean(self):
        if any(self.errors):
            return

        relatorio = self.instance

        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue

            if form.cleaned_data.get("DELETE"):
                continue

            data = form.cleaned_data.get("data")
            origem = form.cleaned_data.get("origem")
            destino = form.cleaned_data.get("destino")
            km = form.cleaned_data.get("km")
            valor_km = form.cleaned_data.get("valor_km")
            observacao = form.cleaned_data.get("observacao")
            clientes_raw = ""
            if getattr(self, "data", None):
                clientes_raw = self.data.get(f"{form.prefix}-clientes", "")
            clientes_ids = [
                item.strip()
                for item in str(clientes_raw or "").split(",")
                if item.strip()
            ]
            clientes_ids_unicos = list(dict.fromkeys(clientes_ids))
            trecho_multi_cliente = len(clientes_ids_unicos) > 1
            if trecho_multi_cliente and valor_km is None:
                form.cleaned_data["valor_km"] = Decimal("0.00")
                valor_km = form.cleaned_data["valor_km"]
            elif len(clientes_ids_unicos) == 1 and valor_km is None:
                valor_cliente = (
                    Cliente.objects.filter(pk=clientes_ids_unicos[0])
                    .values_list("valor_km", flat=True)
                    .first()
                )
                if valor_cliente is not None:
                    form.cleaned_data["valor_km"] = valor_cliente
                    valor_km = valor_cliente

            # Linha totalmente vazia → ignora
            if not form.instance.pk and not any(
                [data, origem, destino, km, observacao]
            ):
                continue

            if not data:
                form.add_error("data", "Informe a data.")
            if not origem:
                form.add_error("origem", "Informe a origem.")
            if not destino:
                form.add_error("destino", "Informe o destino.")
            if km is None:
                form.add_error("km", "Informe o KM.")
            elif km <= 0:
                form.add_error("km", "KM deve ser maior que zero.")
            if valor_km is None and not trecho_multi_cliente:
                form.cleaned_data["valor_km"] = Decimal("0.00")
            if data and data > timezone.localdate():
                form.add_error("data", "Data não pode ser futura.")
            # Data fora do período
            if data and relatorio and relatorio.data_inicio and relatorio.data_fim:
                if data < relatorio.data_inicio or data > relatorio.data_fim:
                    form.add_error(
                        "data",
                        (
                            f"Data fora do período do relatório "
                            f"({relatorio.data_inicio:%d/%m/%Y} a "
                            f"{relatorio.data_fim:%d/%m/%Y})."
                        ),
                    )


"""
Removido para permitir edição manual do valor_km sem bloquear o salvamento.
        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue

            if form.cleaned_data.get("DELETE"):
                continue

            valor_km = form.cleaned_data.get("valor_km")
            relatorio = self.instance

            if not valor_km or not relatorio or not relatorio.cliente:
                continue

            valor_padrao = relatorio.cliente.valor_km_padrao

            if valor_km != valor_padrao:
                form.add_error(
                    "valor_km",
                    f"Valor diferente do padrão do cliente (R$ {valor_padrao}).",
                )
"""

TrechoKmFormSet = inlineformset_factory(
    RelatorioTecnico,
    TrechoKm,
    form=TrechoKmForm,
    formset=BaseTrechoKmFormSet,
    extra=0,
    min_num=0,
    can_delete=True,
)

# ─────────────────────────────────────────────
# FILTRO LISTAGEM
# ─────────────────────────────────────────────


class RelatorioFiltroForm(BootstrapMixin, forms.Form):
    busca = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Número, cliente, técnico...",
                "class": "form-control form-control-sm",
            }
        ),
    )
    tecnico = forms.ModelChoiceField(
        queryset=Tecnico.objects.filter(ativo=True),
        required=False,
        empty_label="Todos os técnicos",
    )
    cliente = forms.ModelChoiceField(
        queryset=Cliente.objects.filter(ativo=True),
        required=False,
        empty_label="Todos os clientes",
    )
    status = forms.ChoiceField(
        choices=[("", "Todos os status")]
        + list(RelatorioTecnico._meta.get_field("status").choices),
        required=False,
    )
    data_inicio = forms.DateField(
        required=False,
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "form-control form-control-sm",
            }
        ),
    )
    data_fim = forms.DateField(
        required=False,
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "form-control form-control-sm",
            }
        ),
    )


# ─────────────────────────────────────────────
# OUTROS
# ─────────────────────────────────────────────


class TecnicoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Tecnico
        fields = ["nome", "email", "telefone", "ativo"]


class ClienteForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Cliente
        fields = [
            "nome",
            "cnpj_cpf",
            "cidade",
            "uf",
            "contato",
            "telefone",
            "email",
            "valor_km",
            "ativo",
        ]

        widgets = {
            "valor_km": forms.NumberInput(
                attrs={
                    "step": "0.0001",
                    "class": "form-control form-control-sm",
                    "placeholder": "Ex: 1.2500",
                }
            ),
        }


class AdiantamentoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Adiantamento
        fields = ["tecnico", "relatorio", "tipo", "valor", "data", "descricao"]
        widgets = {
            "data": forms.DateInput(
                attrs={"type": "date", "class": "form-control form-control-sm"},
                format="%Y-%m-%d",
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tecnico"].queryset = Tecnico.objects.filter(ativo=True)
        self.fields["relatorio"].queryset = RelatorioTecnico.objects.order_by(
            "-data_inicio"
        )
        self.fields["relatorio"].required = False
        self.fields["relatorio"].empty_label = "— Nenhum —"
