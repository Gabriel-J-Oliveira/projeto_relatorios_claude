import logging
from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.forms import inlineformset_factory, BaseInlineFormSet
from django.utils import timezone
from django.utils.text import slugify
from .models import (
    RelatorioTecnico,
    ItemDespesa,
    TrechoKm,
    Tecnico,
    Cliente,
    Adiantamento,
    EmpresaGrupo,
    TipoDocumentoComprovante,
    TipoReembolso,
    Municipio,
    Setor,
    ArtigoAjuda,
    CategoriaAjuda,
    PublicoArtigoAjuda,
    FormatoArtigoAjuda,
)
from .validators import validar_anexo_upload


logger = logging.getLogger(__name__)


def _normalizar_numero_documento(valor):
    return " ".join(str(valor or "").strip().split()).upper()

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
    def __init__(self, *args, user=None, perfil=None, **kwargs):
        self.user = user
        self.perfil = perfil
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


class ArtigoAjudaForm(BootstrapMixin, forms.ModelForm):
    tags_texto = forms.CharField(
        label="Tags",
        required=False,
        help_text="Separe as tags por vírgula.",
    )
    publico_para = forms.MultipleChoiceField(
        label="Público-alvo",
        required=False,
        choices=PublicoArtigoAjuda.choices,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = ArtigoAjuda
        fields = [
            "titulo",
            "categoria",
            "resumo",
            "conteudo",
            "formato",
            "ativo",
            "importante",
            "link_rapido",
            "tour_url",
            "artigos_relacionados",
        ]
        widgets = {
            "resumo": forms.Textarea(attrs={"rows": 3}),
            "conteudo": forms.Textarea(attrs={"rows": 18, "class": "form-control help-editor"}),
            "formato": forms.HiddenInput(),
            "artigos_relacionados": forms.SelectMultiple(
                attrs={
                    "class": "form-select",
                    "size": 8,
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["categoria"].queryset = CategoriaAjuda.objects.filter(ativo=True).order_by("ordem", "titulo")
        relacionados_qs = ArtigoAjuda.objects.filter(ativo=True).select_related("categoria").order_by(
            "categoria__ordem",
            "categoria__titulo",
            "titulo",
        )
        if self.instance.pk:
            relacionados_qs = relacionados_qs.exclude(pk=self.instance.pk)
        self.fields["artigos_relacionados"].queryset = relacionados_qs
        self.fields["artigos_relacionados"].label_from_instance = (
            lambda artigo: f"{artigo.categoria.titulo} - {artigo.titulo}"
        )
        self.fields["formato"].initial = FormatoArtigoAjuda.HTML
        self.fields["tags_texto"].initial = ", ".join(self.instance.tags_lista) if self.instance.pk else ""
        self.fields["publico_para"].initial = self.instance.publico_lista if self.instance.pk else [PublicoArtigoAjuda.TODOS]
        self.fields["publico_para"].widget.attrs["class"] = "form-check-input"
        for name in ["ativo", "importante", "link_rapido"]:
            self.fields[name].widget.attrs["class"] = "form-check-input"

    def clean_tags_texto(self):
        texto = self.cleaned_data.get("tags_texto") or ""
        return [tag.strip() for tag in texto.split(",") if tag.strip()]

    def clean_publico_para(self):
        valores = self.cleaned_data.get("publico_para") or []
        return valores or [PublicoArtigoAjuda.TODOS]

    def clean_titulo(self):
        titulo = (self.cleaned_data.get("titulo") or "").strip()
        slug = slugify(titulo)
        if slug:
            qs = ArtigoAjuda.objects.filter(slug=slug)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError("Já existe um artigo com este título. Ajuste o título para criar um slug único.")
        return titulo

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.formato = FormatoArtigoAjuda.HTML
        instance.tags = self.cleaned_data.get("tags_texto") or []
        instance.publico_para = self.cleaned_data.get("publico_para") or [PublicoArtigoAjuda.TODOS]
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class TecnicoChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        partes = [obj.nome]
        identificador = obj.ad_username or obj.email
        if identificador:
            partes.append(identificador)
        if obj.email and obj.email != identificador:
            partes.append(obj.email)
        return " — ".join(partes)


class TecnicoMultipleChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        partes = [obj.nome]
        identificador = obj.ad_username or obj.email
        if identificador:
            partes.append(identificador)
        if obj.email and obj.email != identificador:
            partes.append(obj.email)
        return " — ".join(partes)


# ─────────────────────────────────────────────
# CABEÇALHO
# ─────────────────────────────────────────────

# forms.py


class RelatorioTecnicoForm(BootstrapMixin, forms.ModelForm):

    tecnico_responsavel = TecnicoChoiceField(
        queryset=Tecnico.objects.filter(ativo=True).order_by("nome"),
        required=True,
        label="Técnico responsável",
    )
    tecnicos_equipe = TecnicoMultipleChoiceField(
        queryset=Tecnico.objects.filter(ativo=True).order_by("nome"),
        required=False,
        label="Equipe (técnicos adicionais)",
        widget=forms.SelectMultiple(
            attrs={"class": "form-select form-select-sm", "size": "5"}
        ),
        help_text="Segure Ctrl (ou Cmd) para selecionar múltiplos.",
    )
    tecnico_reembolso = TecnicoChoiceField(
        queryset=Tecnico.objects.filter(ativo=True).order_by("nome"),
        required=False,
        label="Técnico reembolsado",
        widget=forms.HiddenInput(),
    )
    municipio_atendimento = forms.ModelChoiceField(
        queryset=Municipio.objects.filter(ativo=True).order_by("nome", "uf"),
        required=False,
        widget=forms.HiddenInput(),
    )
    empresa_grupo = forms.ChoiceField(
        choices=[("", "Selecione")] + list(EmpresaGrupo.choices),
        required=False,
        label="Empresa responsável pelo custo",
    )

    class Meta:
        model = RelatorioTecnico
        fields = [
            "numero",
            "tecnico_responsavel",
            "tecnico_reembolso",
            "municipio_atendimento",
            "cidade_atendimento",
            "uf_atendimento",
            "tipo_localidade",
            "data_inicio",
            "data_fim",
            "motivo",
            "tipo_relatorio",
            "tipo_reembolso",
            "empresa_grupo",
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
            "tipo_relatorio": "Area de gasto",
            "tipo_reembolso": "Tipo de reembolso",
        }


    def __init__(self, *args, **kwargs):
        kwargs.pop("numero_sugerido", None)
        super().__init__(*args, **kwargs)

        self.fields["tecnico_responsavel"].queryset = Tecnico.objects.filter(
            ativo=True
        ).order_by("nome")
        self.fields["tecnico_reembolso"].queryset = Tecnico.objects.filter(
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
        self.fields["cidade_atendimento"].required = False
        self.fields["uf_atendimento"].required = False
        self.fields["tipo_localidade"].required = False
        self.fields["cidade_atendimento"].widget.attrs.update(
            {
                "autocomplete": "off",
                "placeholder": "Digite e selecione uma cidade oficial",
                "data-municipio-label": (
                    self.instance.municipio_atendimento.label
                    if self.instance
                    and self.instance.pk
                    and self.instance.municipio_atendimento_id
                    else ""
                ),
            }
        )
        self.fields["uf_atendimento"].widget.attrs.update({"readonly": "readonly"})
        self.fields["tipo_localidade"].widget.attrs.update(
            {
                "data-auto-localidade": "true",
                "tabindex": "-1",
                "style": "pointer-events: none;",
            }
        )
        self.fields["valor_adiantamento"].required = False
        self.fields["km_excedente_interno"].required = False
        self.fields["observacao_km_excedente"].required = False
        if self.instance and self.instance.pk and not self.instance.numero:
            self.fields["numero"].initial = "Rascunho"
        self.fields["tipo_relatorio"].widget.attrs.update(
            {"class": "form-select form-select-sm"}
        )
        self.fields["tipo_reembolso"].widget.attrs.update(
            {"class": "form-select form-select-sm"}
        )
        self.fields["empresa_grupo"].widget.attrs.update(
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
            if self.instance.tecnico_reembolso_id:
                self.fields["tecnico_reembolso"].initial = self.instance.tecnico_reembolso_id

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
        municipio = cd.get("municipio_atendimento")
        cidade = (cd.get("cidade_atendimento") or "").strip()
        if municipio:
            cd["cidade_atendimento"] = municipio.nome
            cd["uf_atendimento"] = municipio.uf
            cd["tipo_localidade"] = municipio.tipo_localidade_padrao
        elif cidade:
            from .models import normalizar_texto_busca

            cidade_base = cidade.split("/")[0].strip()
            uf_base = cidade.split("/", 1)[1].strip().upper() if "/" in cidade else ""
            if not uf_base:
                partes = cidade_base.split()
                if len(partes) > 1 and len(partes[-1]) == 2:
                    uf_base = partes[-1].upper()
                    cidade_base = " ".join(partes[:-1]).strip()
            qs = Municipio.objects.filter(
                ativo=True,
                nome_normalizado=normalizar_texto_busca(cidade_base),
            )
            if uf_base:
                qs = qs.filter(uf=uf_base)
            if qs.count() == 1:
                municipio = qs.first()
                cd["municipio_atendimento"] = municipio
                cd["cidade_atendimento"] = municipio.nome
                cd["uf_atendimento"] = municipio.uf
                cd["tipo_localidade"] = municipio.tipo_localidade_padrao
        resp = cd.get("tecnico_responsavel")
        equipe = cd.get("tecnicos_equipe", [])
        tecnico_reembolso = cd.get("tecnico_reembolso")
        tecnicos_envolvidos = [tecnico for tecnico in [resp] + list(equipe or []) if tecnico]
        if not tecnico_reembolso and len(tecnicos_envolvidos) == 1:
            tecnico_reembolso = tecnicos_envolvidos[0]
            cd["tecnico_reembolso"] = tecnico_reembolso
        if resp and resp in equipe:
            self.add_error(
                "tecnicos_equipe",
                "O técnico responsável não deve ser adicionado à equipe adicional.",
            )
        if tecnico_reembolso and tecnico_reembolso not in tecnicos_envolvidos:
            self.add_error(
                "tecnico_reembolso",
                "O técnico definido para reembolso deve estar entre os técnicos envolvidos no relatório.",
            )
        if cd.get("tipo_reembolso") != TipoReembolso.NAO_REEMBOLSAVEL:
            cd["empresa_grupo"] = ""
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
                "accept": ".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png",
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

    def clean_comprovante(self):
        comprovante = self.cleaned_data.get("comprovante")
        validar_anexo_upload(comprovante)
        return comprovante


class BaseItemDespesaFormSet(BaseInlineFormSet):
    def clean(self):
        if any(self.errors):
            return

        relatorio = self.instance
        documentos_vistos = {}

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

            numero_normalizado = _normalizar_numero_documento(numero_documento)
            if numero_normalizado:
                form.cleaned_data["numero_documento_comprovante"] = numero_normalizado
                form.instance.numero_documento_comprovante = numero_normalizado
                if numero_normalizado in documentos_vistos:
                    form.add_error(
                        "numero_documento_comprovante",
                        "Ja existe uma despesa cadastrada com este numero de nota/documento.",
                    )
                    documentos_vistos[numero_normalizado].add_error(
                        "numero_documento_comprovante",
                        "Ja existe uma despesa cadastrada com este numero de nota/documento.",
                    )
                    logger.warning(
                        "numero_documento_duplicado_formset relatorio_id=%s numero=%s",
                        getattr(relatorio, "pk", None),
                        numero_normalizado,
                    )
                else:
                    documentos_vistos[numero_normalizado] = form

                duplicados = ItemDespesa.objects.filter(
                    numero_documento_comprovante__iexact=numero_normalizado
                )
                if form.instance.pk:
                    duplicados = duplicados.exclude(pk=form.instance.pk)
                if duplicados.exists():
                    form.add_error(
                        "numero_documento_comprovante",
                        "Ja existe uma despesa cadastrada com este numero de nota/documento.",
                    )
                    logger.warning(
                        "numero_documento_duplicado_banco relatorio_id=%s despesa_id=%s numero=%s",
                        getattr(relatorio, "pk", None),
                        getattr(form.instance, "pk", None),
                        numero_normalizado,
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
        queryset=Cliente.objects.filter(ativo=True).order_by(
            "nome_fantasia",
            "razao_social",
            "nome",
        ),
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
        fields = ["nome", "email", "telefone", "setor", "funcao_setor", "ativo"]


class ClienteForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Cliente
        fields = [
            "nome",
            "razao_social",
            "nome_fantasia",
            "cnpj_cpf",
            "cep",
            "cidade",
            "uf",
            "logradouro",
            "numero",
            "bairro",
            "complemento",
            "contato",
            "telefone",
            "email",
            "valor_km",
            "valor_km_observacao",
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
