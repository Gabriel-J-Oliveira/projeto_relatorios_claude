import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from relatorios.models import (
    ArtigoAjuda,
    CategoriaAjuda,
    FormatoArtigoAjuda,
    PublicoArtigoAjuda,
)


HELP_CONTENT_DIR = Path(__file__).resolve().parents[2] / "help_content"


class Command(BaseCommand):
    help = "Importa categorias e artigos Markdown da Central de Ajuda para o banco."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Simula sem gravar.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        index_path = HELP_CONTENT_DIR / "index.json"
        data = json.loads(index_path.read_text(encoding="utf-8"))

        categorias_criadas = 0
        categorias_atualizadas = 0
        artigos_criados = 0
        artigos_atualizados = 0

        with transaction.atomic():
            categorias = {}
            for ordem, item in enumerate(data.get("categories", []), start=1):
                defaults = {
                    "titulo": item["title"],
                    "descricao": item.get("description", ""),
                    "icone": item.get("icon", "bi-question-circle"),
                    "ordem": int(item.get("ordem") or item.get("order") or ordem),
                    "ativo": True,
                }
                categoria, created = CategoriaAjuda.objects.update_or_create(
                    slug=item["slug"],
                    defaults=defaults,
                )
                categorias[item["slug"]] = categoria
                categorias_criadas += int(created)
                categorias_atualizadas += int(not created)

            for item in data.get("articles", []):
                categoria = categorias.get(item["category"])
                if not categoria:
                    self.stderr.write(f"Categoria ausente para artigo {item['slug']}: {item['category']}")
                    continue
                path = HELP_CONTENT_DIR / item["path"]
                conteudo = path.read_text(encoding="utf-8") if path.exists() else "# Conteúdo indisponível"
                publico = item.get("profile") or [PublicoArtigoAjuda.TODOS]
                defaults = {
                    "categoria": categoria,
                    "titulo": item["title"],
                    "resumo": item.get("summary", ""),
                    "conteudo": conteudo,
                    "formato": FormatoArtigoAjuda.MARKDOWN,
                    "tags": item.get("tags", []),
                    "publico_para": publico,
                    "importante": bool(item.get("important", False)),
                    "link_rapido": bool(item.get("quick_link", False)),
                    "tour_url": item.get("tour_url", ""),
                    "ativo": True,
                }
                _artigo, created = ArtigoAjuda.objects.update_or_create(
                    slug=item["slug"],
                    defaults=defaults,
                )
                artigos_criados += int(created)
                artigos_atualizados += int(not created)

            if dry_run:
                transaction.set_rollback(True)

        sufixo = " (dry-run)" if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                "Importação de ajuda concluída%s: categorias criadas=%s, atualizadas=%s, "
                "artigos criados=%s, atualizados=%s"
                % (
                    sufixo,
                    categorias_criadas,
                    categorias_atualizadas,
                    artigos_criados,
                    artigos_atualizados,
                )
            )
        )
