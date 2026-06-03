from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db.models import Q
from django.utils import timezone

from relatorios.models import RelatorioTecnico
from relatorios.services.email_service import (
    _contexto_email_relatorio,
    send_templated_email,
)


class Command(BaseCommand):
    help = "Envia os templates HTML de e-mail para validacao visual."

    def add_arguments(self, parser):
        parser.add_argument("destinatario", nargs="?", help="E-mail que recebera os testes.")
        parser.add_argument("--relatorio", help="ID ou numero do relatorio usado no contexto.")
        parser.add_argument("--somente", choices=["financeiro", "tecnico"], help="Filtra os modelos enviados.")
        parser.add_argument("--listar", action="store_true", help="Lista os modelos disponiveis sem enviar.")

    def _buscar_relatorio(self, ref):
        if not ref:
            return None
        filtro = Q(numero=ref)
        if str(ref).isdigit():
            filtro |= Q(pk=int(ref))
        return RelatorioTecnico.objects.filter(filtro).first()

    def _contexto_fake(self, *, titulo, mensagem, status_label, status_cor, status_bg, action_label, motivo="", anexos_linhas=None):
        site_url = (
            getattr(settings, "SITE_URL", "")
            or getattr(settings, "APP_BASE_URL", "")
            or "https://relatorios.controlsul.com.br"
        ).rstrip("/")
        return {
            "email_title": titulo,
            "preheader": mensagem,
            "titulo": titulo,
            "mensagem": mensagem,
            "status_label": status_label,
            "status_cor": status_cor,
            "status_bg": status_bg,
            "action_url": f"{site_url}/relatorios/123/consulta/",
            "action_label": action_label,
            "motivo": motivo,
            "anexos_linhas": list(anexos_linhas or []),
            "ano_atual": timezone.localtime(timezone.now()).year,
            "resumo_linhas": [
                {"label": "Nº do relatório", "value": "#RT-TESTE-001"},
                {"label": "Técnico responsável", "value": "Gabriel Oliveira"},
                {"label": "Período", "value": "01/06/2026 a 03/06/2026"},
                {"label": "Cliente(s)", "value": "Cliente Exemplo Ltda"},
                {"label": "Tipo de reembolso", "value": "Reembolsável"},
                {"label": "Status", "value": status_label},
                {"label": "Valor solicitado", "value": "R$ 1.250,00"},
                {"label": "Valor aprovado", "value": "R$ 1.100,00"},
                {"label": "Data/hora", "value": timezone.localtime(timezone.now()).strftime("%d/%m/%Y %H:%M")},
            ],
        }

    def _modelos(self, relatorio=None):
        if relatorio:
            financeiro = _contexto_email_relatorio(
                relatorio,
                titulo="Novo relatório enviado para conferência",
                mensagem="Há um relatório aguardando análise financeira.",
                status_label="Conferência pendente",
                status_cor="#1f5fbf",
                status_bg="#e8f0ff",
                action_label="Abrir conferência",
            )
            ajuste = _contexto_email_relatorio(
                relatorio,
                titulo="Relatório devolvido para ajuste",
                mensagem="O financeiro solicitou correções antes de concluir a análise.",
                status_label="Ajuste pendente",
                status_cor="#9a5b00",
                status_bg="#fff4db",
                action_label="Corrigir relatório",
                motivo="Exemplo de motivo para validar a exibicao do bloco de justificativa.",
            )
            aprovado = _contexto_email_relatorio(
                relatorio,
                titulo="Relatório aprovado",
                mensagem="O relatório foi aprovado e está disponível para consulta.",
                status_label="Aprovado",
                status_cor="#17663a",
                status_bg="#e7f7ee",
                action_label="Consultar relatório",
                incluir_aprovado=True,
            )
            rejeitado = _contexto_email_relatorio(
                relatorio,
                titulo="Relatório rejeitado",
                mensagem="O relatório foi rejeitado definitivamente após a conferência.",
                status_label="Rejeitado",
                status_cor="#a61b1b",
                status_bg="#ffe8e8",
                action_label="Consultar relatório",
                motivo="Exemplo de motivo de rejeição para validação visual.",
                incluir_aprovado=True,
            )
            finalizado = _contexto_email_relatorio(
                relatorio,
                titulo="Relatório aprovado",
                mensagem="O relatório foi aprovado e os documentos oficiais foram gerados.",
                status_label="Aprovado",
                status_cor="#17663a",
                status_bg="#e7f7ee",
                action_label="Consultar relatório",
                incluir_aprovado=True,
                anexos_linhas=["Relatório financeiro interno", "Pacote ZIP com PDFs individuais dos clientes"],
            )
        else:
            financeiro = self._contexto_fake(
                titulo="Novo relatório enviado para conferência",
                mensagem="Há um relatório aguardando análise financeira.",
                status_label="Conferência pendente",
                status_cor="#1f5fbf",
                status_bg="#e8f0ff",
                action_label="Abrir conferência",
            )
            ajuste = self._contexto_fake(
                titulo="Relatório devolvido para ajuste",
                mensagem="O financeiro solicitou correções antes de concluir a análise.",
                status_label="Ajuste pendente",
                status_cor="#9a5b00",
                status_bg="#fff4db",
                action_label="Corrigir relatório",
                motivo="Exemplo de motivo para validar a exibição do bloco de justificativa.",
            )
            aprovado = self._contexto_fake(
                titulo="Relatório aprovado",
                mensagem="O relatório foi aprovado e está disponível para consulta.",
                status_label="Aprovado",
                status_cor="#17663a",
                status_bg="#e7f7ee",
                action_label="Consultar relatório",
            )
            rejeitado = self._contexto_fake(
                titulo="Relatório rejeitado",
                mensagem="O relatório foi rejeitado definitivamente após a conferência.",
                status_label="Rejeitado",
                status_cor="#a61b1b",
                status_bg="#ffe8e8",
                action_label="Consultar relatório",
                motivo="Exemplo de motivo de rejeição para validação visual.",
            )
            finalizado = self._contexto_fake(
                titulo="Relatório aprovado",
                mensagem="O relatório foi aprovado e os documentos oficiais foram gerados.",
                status_label="Aprovado",
                status_cor="#17663a",
                status_bg="#e7f7ee",
                action_label="Consultar relatório",
                anexos_linhas=["Relatório financeiro interno", "Pacote ZIP com PDFs individuais dos clientes"],
            )

        return [
            ("financeiro", "[TESTE] Novo relatório enviado para conferência", "emails/relatorio_enviado_financeiro.html", "emails/text/relatorio_enviado_financeiro.txt", financeiro, "teste_template_financeiro"),
            ("tecnico", "[TESTE] Relatório devolvido para ajuste", "emails/relatorio_ajuste_tecnico.html", "emails/text/relatorio_ajuste_tecnico.txt", ajuste, "teste_template_ajuste"),
            ("tecnico", "[TESTE] Relatório aprovado", "emails/relatorio_aprovado_tecnico.html", "emails/text/relatorio_aprovado_tecnico.txt", aprovado, "teste_template_aprovado"),
            ("tecnico", "[TESTE] Relatório rejeitado", "emails/relatorio_rejeitado_tecnico.html", "emails/text/relatorio_rejeitado_tecnico.txt", rejeitado, "teste_template_rejeitado"),
            ("financeiro", "[TESTE] Relatório finalizado - documentos gerados", "emails/relatorio_finalizado_financeiro.html", "emails/text/relatorio_aprovado_tecnico.txt", finalizado, "teste_template_finalizado"),
        ]

    def handle(self, *args, **options):
        relatorio = self._buscar_relatorio(options.get("relatorio"))
        if options.get("relatorio") and relatorio is None:
            raise CommandError("Relatorio informado nao foi encontrado.")
        modelos = self._modelos(relatorio)
        if options.get("somente"):
            modelos = [modelo for modelo in modelos if modelo[0] == options["somente"]]

        if options["listar"]:
            for grupo, assunto, template_html, _template_text, _contexto, tipo in modelos:
                self.stdout.write(f"{grupo}: {tipo} - {assunto} ({template_html})")
            return

        destinatario = (options.get("destinatario") or "").strip()
        try:
            validate_email(destinatario)
        except ValidationError as exc:
            raise CommandError("Informe um destinatario de e-mail valido.") from exc

        enviados = 0
        falhas = 0
        for grupo, assunto, template_html, template_text, contexto, tipo in modelos:
            try:
                send_templated_email(
                    assunto,
                    [destinatario],
                    template_html,
                    template_text,
                    contexto,
                    relatorio=relatorio,
                    tipo_email=tipo,
                )
            except Exception as exc:
                falhas += 1
                self.stderr.write(self.style.ERROR(f"Falha em {tipo}: {exc}"))
                continue
            enviados += 1
            self.stdout.write(self.style.SUCCESS(f"Enviado: {tipo} para {destinatario}"))

        self.stdout.write(f"Resumo: enviados={enviados} falhas={falhas}")
        if falhas:
            raise CommandError("Um ou mais templates falharam no envio.")
