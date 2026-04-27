from django import forms
from django.forms import inlineformset_factory
from .models import (
    RelatorioTecnico,
    ItemDespesa,
    TrechoKm,
    Tecnico,
    Cliente,
    Adiantamento,
    RelatorioTecnicoEquipe,
    PoliticaValor,
)


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


# ─────────────────────────────────────────────
# CABEÇALHO DO RELATÓRIO
# ─────────────────────────────────────────────


class RelatorioTecnicoForm(forms.ModelForm):
    class Meta:
        model = RelatorioTecnico
        # Campos que REALMENTE existem no seu Model
        fields = [
            "numero",
            "status",
            "cliente",
            "tecnico_responsavel",
            "cidade_atendimento",
            "uf_atendimento",
            "tipo_localidade",
            "data_inicio",
            "data_fim",
            "motivo",
            "area_gasto",
            "valor_adiantamento",
            "observacoes",
        ]
        widgets = {
            "data_inicio": forms.DateInput(
                attrs={"type": "date", "class": "form-control form-control-sm"},
                format="%Y-%m-%d",
            ),
            "data_fim": forms.DateInput(
                attrs={"type": "date", "class": "form-control form-control-sm"},
                format="%Y-%m-%d",
            ),
            "motivo": forms.Textarea(attrs={"rows": 4, "class": "form-control"}),
            "observacoes": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
            "numero": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Ex: RT-2026-001"}
            ),
            "cliente": forms.Select(attrs={"class": "form-select"}),
            "tecnico_responsavel": forms.Select(attrs={"class": "form-select"}),
            "cidade_atendimento": forms.TextInput(attrs={"class": "form-control"}),
            "uf_atendimento": forms.Select(attrs={"class": "form-select"}),
            "tipo_localidade": forms.Select(attrs={"class": "form-select"}),
            "area_gasto": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Ex: Manutenção, Instalação...",
                }
            ),
            "valor_adiantamento": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01"}
            ),
            "status": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Filtros para garantir que apenas registros ativos apareçam
        self.fields["cliente"].queryset = Cliente.objects.filter(ativo=True).order_by(
            "nome"
        )
        self.fields["tecnico_responsavel"].queryset = Tecnico.objects.filter(
            ativo=True
        ).order_by("nome")

        # Adicionando atributos para cálculos em tempo real (Alpine.js ou JS puro)
        self.fields["valor_adiantamento"].widget.attrs.update(
            {"x-model.number": "adiantamento", "@input": "calcularTotais()"}
        )

    def clean_numero(self):
        numero = self.cleaned_data.get("numero", "").strip().upper()
        qs = RelatorioTecnico.objects.filter(numero=numero)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Já existe um relatório com este número.")
        return numero

    def clean(self):
        cleaned = super().clean()
        ini = cleaned.get("data_inicio")
        fim = cleaned.get("data_fim")
        if ini and fim and fim < ini:
            self.add_error(
                "data_fim", "A data fim não pode ser anterior à data início."
            )
        return cleaned


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
            "reembolsavel",
            "centro_custo",
            "comprovante",
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
        self.fields["descricao"].widget.attrs["placeholder"] = "Fornecedor / Detalhe"
        self.fields["centro_custo"].widget.attrs["placeholder"] = "Centro de custo"
        self.fields["valor"].widget.attrs.update(
            {
                "placeholder": "0,00",
                "@input": "calcular()",
                "x-model": "item_valor",
            }
        )
        self.fields["comprovante"].widget.attrs.update(
            {
                "class": "form-control form-control-sm",
                "accept": "image/*,.pdf",
            }
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
            "destino",
            "km",
            "valor_km",
            "observacao",
        ]
        widgets = {
            "data": forms.DateInput(
                attrs={"type": "date", "class": "form-control form-control-sm"},
                format="%Y-%m-%d",
            ),
            "ordem": forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["origem"].widget.attrs["placeholder"] = "Cidade origem"
        self.fields["destino"].widget.attrs["placeholder"] = "Cidade destino"
        self.fields["km"].widget.attrs["placeholder"] = "0,0"
        self.fields["valor_km"].widget.attrs["placeholder"] = "0,0000"
        self.fields["observacao"].widget.attrs["placeholder"] = "Observação"
        self.fields["km"].widget.attrs["@input"] = "calcularKm()"
        self.fields["valor_km"].widget.attrs["@input"] = "calcularKm()"


# ─────────────────────────────────────────────
# FORMSETS
# ─────────────────────────────────────────────

ItemDespesaFormSet = inlineformset_factory(
    RelatorioTecnico,
    ItemDespesa,
    form=ItemDespesaForm,
    extra=0,
    min_num=0,
    can_delete=True,
)

TrechoKmFormSet = inlineformset_factory(
    RelatorioTecnico,
    TrechoKm,
    form=TrechoKmForm,
    extra=0,
    min_num=0,
    can_delete=True,
)


# ─────────────────────────────────────────────
# OUTROS FORMS (Técnico, Cliente, Adiantamento)
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
            "ativo",
        ]


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
            attrs={"type": "date", "class": "form-control form-control-sm"}
        ),
    )
    data_fim = forms.DateField(
        required=False,
        widget=forms.DateInput(
            attrs={"type": "date", "class": "form-control form-control-sm"}
        ),
    )
