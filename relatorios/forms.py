from django import forms
from django.forms import inlineformset_factory, BaseInlineFormSet
from .models import (
    RelatorioTecnico,
    ItemDespesa,
    TrechoKm,
    Tecnico,
    Cliente,
    Adiantamento,
    PoliticaValor,
)
import datetime

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
# CABEÇALHO
# ─────────────────────────────────────────────


class RelatorioTecnicoForm(BootstrapMixin, forms.ModelForm):
    """
    Inclui campo extra 'tecnicos_equipe' para seleção múltipla de técnicos
    que participaram do atendimento (além do responsável).
    """

    tecnicos_equipe = forms.ModelMultipleChoiceField(
        queryset=Tecnico.objects.filter(ativo=True).order_by("nome"),
        required=False,
        label="Equipe (técnicos adicionais)",
        widget=forms.SelectMultiple(
            attrs={
                "class": "form-select form-select-sm",
                "size": "5",
            }
        ),
        help_text="Segure Ctrl (ou Cmd) para selecionar múltiplos.",
    )

    class Meta:
        model = RelatorioTecnico
        fields = [
            "numero",
            # "status",
            "cliente",
            "tecnico_responsavel",
            "cidade_atendimento",
            "uf_atendimento",
            "tipo_localidade",
            "data_inicio",
            "data_fim",
            "motivo",
            "centro_custo",
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
            "motivo": forms.Textarea(attrs={"rows": 4}),
            "observacoes": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "centro_custo": "Centro de Custo / Classificação",
        }
        help_texts = {
            "centro_custo": ("Será aplicado a todas as despesas deste relatório."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["cliente"].queryset = Cliente.objects.filter(ativo=True).order_by(
            "nome"
        )
        self.fields["tecnico_responsavel"].queryset = Tecnico.objects.filter(
            ativo=True
        ).order_by("nome")
        self.fields["numero"].widget.attrs["placeholder"] = "Ex: RT-2024-001"
        self.fields["centro_custo"].widget.attrs[
            "placeholder"
        ] = "Ex: Manutenção, Instalação, Comercial..."

        # Pré-seleciona equipe existente ao editar
        if self.instance and self.instance.pk:
            self.fields["tecnicos_equipe"].initial = (
                self.instance.tecnicos_adicionais.values_list("pk", flat=True)
            )

    def clean_numero(self):
        numero = self.cleaned_data.get("numero", "").strip().upper()
        qs = RelatorioTecnico.objects.filter(numero=numero)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Número já cadastrado.")
        return numero

    def clean(self):
        cd = super().clean()
        ini = cd.get("data_inicio")
        fim = cd.get("data_fim")
        if ini and fim and fim < ini:
            self.add_error("data_fim", "Data fim não pode ser anterior à data início.")

        # Técnico responsável não pode estar na equipe adicional
        resp = cd.get("tecnico_responsavel")
        equipe = cd.get("tecnicos_equipe", [])
        if resp and resp in equipe:
            self.add_error(
                "tecnicos_equipe",
                "O técnico responsável não deve ser adicionado à equipe adicional.",
            )
        return cd

    def save(self, commit=True):
        instance = super().save(commit=commit)
        if commit:
            self._salvar_equipe(instance)
        return instance

    def _salvar_equipe(self, instance):
        """Sincroniza a M2M de equipe com o que foi selecionado."""
        from .models import RelatorioTecnicoEquipe

        equipe_selecionada = set(
            t.pk for t in self.cleaned_data.get("tecnicos_equipe", [])
        )
        # Remove quem saiu
        instance.equipe.exclude(tecnico_id__in=equipe_selecionada).delete()
        # Adiciona quem é novo
        existentes = set(instance.equipe.values_list("tecnico_id", flat=True))
        for pk in equipe_selecionada - existentes:
            RelatorioTecnicoEquipe.objects.create(
                relatorio=instance,
                tecnico_id=pk,
            )


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
        self.fields["valor"].widget.attrs["placeholder"] = "0,00"
        self.fields["comprovante"].widget.attrs.update(
            {
                "class": "form-control form-control-sm text-nowrap",
                "accept": "image/*,.pdf",
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
            valor = form.cleaned_data.get("valor")

            # linha vazia
            if not any([data, tipo, valor]):
                continue

            if tipo and not valor:
                form.add_error("valor", "Informe o valor.")

            if valor and not tipo:
                form.add_error("tipo", "Selecione o tipo.")

            if valor is not None and valor <= 0:
                form.add_error("valor", "Valor deve ser maior que zero.")

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
            "destino",
            "km",
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
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["origem"].widget.attrs.update(
            {
                "class": "form-control form-control-sm",
                "placeholder": "Cidade origem",
            }
        )

        self.fields["destino"].widget.attrs.update(
            {
                "class": "form-control form-control-sm",
                "placeholder": "Cidade destino",
            }
        )

        self.fields["km"].widget.attrs.update(
            {
                "class": "form-control form-control-sm campo-km",
                "placeholder": "0,0",
            }
        )

        self.fields["valor_km"].widget.attrs.update(
            {
                "class": "form-control form-control-sm campo-vkm",
                "placeholder": "0,0000",
            }
        )

        self.fields["observacao"].widget.attrs.update(
            {
                "class": "form-control form-control-sm",
                "placeholder": "Observação",
            }
        )

        # Preenche valor_km pela política vigente se o form estiver vazio
        if not self.instance.pk and not self.initial.get("valor_km"):
            self.initial["valor_km"] = PoliticaValor.valor_km_vigente(
                datetime.date.today()
            )


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

            # Linha totalmente vazia → ignora
            if not any([data, origem, destino]):
                continue

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
            "valor_km", f"Valor diferente do padrão do cliente (R$ {valor_padrao})."
        )

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
