import html
import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from django.utils.safestring import mark_safe

from relatorios.services.autorizacao_service import (
    usuario_eh_administrativo,
    usuario_tem_acesso_total,
)


logger = logging.getLogger(__name__)

HELP_CONTENT_DIR = Path(__file__).resolve().parent.parent / "help_content"


try:
    import markdown as markdown_lib
except ImportError:  # pragma: no cover - fallback para ambientes sem dependência instalada.
    markdown_lib = None


@dataclass(frozen=True)
class HelpCategory:
    slug: str
    title: str
    description: str
    icon: str


@dataclass(frozen=True)
class HelpArticle:
    slug: str
    title: str
    category: str
    profile: tuple[str, ...]
    tags: tuple[str, ...]
    path: str
    summary: str
    important: bool = False
    quick_link: bool = False
    tour_url: str = ""


def _normalizar(texto):
    return " ".join((texto or "").strip().lower().split())


@lru_cache(maxsize=1)
def _load_index():
    index_path = HELP_CONTENT_DIR / "index.json"
    with index_path.open("r", encoding="utf-8") as fp:
        data = json.load(fp)

    categories = [
        HelpCategory(
            slug=item["slug"],
            title=item["title"],
            description=item.get("description", ""),
            icon=item.get("icon", "bi-question-circle"),
        )
        for item in data.get("categories", [])
    ]
    articles = [
        HelpArticle(
            slug=item["slug"],
            title=item["title"],
            category=item["category"],
            profile=tuple(item.get("profile", [])),
            tags=tuple(item.get("tags", [])),
            path=item["path"],
            summary=item.get("summary", ""),
            important=bool(item.get("important", False)),
            quick_link=bool(item.get("quick_link", False)),
            tour_url=item.get("tour_url", ""),
        )
        for item in data.get("articles", [])
    ]
    category_map = {category.slug: category for category in categories}
    return categories, articles, category_map


@lru_cache(maxsize=128)
def _read_markdown(relative_path):
    path = (HELP_CONTENT_DIR / relative_path).resolve()
    base = HELP_CONTENT_DIR.resolve()
    if base not in path.parents:
        raise ValueError("Caminho de artigo inválido.")
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Artigo da central de ajuda ausente. path=%s", relative_path)
        return "# Conteúdo indisponível\n\nEste artigo ainda não foi publicado."


def _perfis_visiveis(user):
    if usuario_tem_acesso_total(user) or usuario_eh_administrativo(user):
        return {"admin", "financeiro", "tecnico", "geral"}
    return {"tecnico", "geral"}


def artigo_visivel_para_usuario(article, user):
    perfis = set(article.profile or ("geral",))
    return bool(perfis & _perfis_visiveis(user))


def listar_artigos(user):
    _categories, articles, _category_map = _load_index()
    return [article for article in articles if artigo_visivel_para_usuario(article, user)]


def listar_categorias_com_contagem(user):
    categories, _articles, _category_map = _load_index()
    visible_articles = listar_artigos(user)
    counts = {}
    for article in visible_articles:
        counts[article.category] = counts.get(article.category, 0) + 1
    return [
        {
            "slug": category.slug,
            "title": category.title,
            "description": category.description,
            "icon": category.icon,
            "count": counts.get(category.slug, 0),
        }
        for category in categories
        if counts.get(category.slug, 0)
    ]


def buscar_artigos(user, termo):
    termo_normalizado = _normalizar(termo)
    articles = listar_artigos(user)
    if not termo_normalizado:
        return articles

    results = []
    for article in articles:
        content = _read_markdown(article.path)
        haystack = _normalizar(
            " ".join(
                [
                    article.title,
                    article.summary,
                    " ".join(article.tags),
                    content,
                ]
            )
        )
        if termo_normalizado in haystack:
            results.append(article)
    return results


def obter_artigo(user, slug):
    _categories, articles, category_map = _load_index()
    for article in articles:
        if article.slug == slug and artigo_visivel_para_usuario(article, user):
            content = _read_markdown(article.path)
            return {
                "article": article,
                "category": category_map.get(article.category),
                "content": content,
                "html": render_markdown(content),
                "related": [
                    item
                    for item in listar_artigos(user)
                    if item.category == article.category and item.slug != article.slug
                ][:8],
            }
    return None


def artigos_por_categoria(user):
    grouped = {}
    for article in listar_artigos(user):
        grouped.setdefault(article.category, []).append(article)
    return grouped


def contexto_central_ajuda(user, termo=""):
    categories = listar_categorias_com_contagem(user)
    category_map = {item["slug"]: item for item in categories}
    results = buscar_artigos(user, termo)
    grouped = artigos_por_categoria(user)
    return {
        "query": termo,
        "categories": categories,
        "category_map": category_map,
        "articles_by_category": grouped,
        "results": results,
        "important_articles": [item for item in listar_artigos(user) if item.important][:8],
        "quick_links": [item for item in listar_artigos(user) if item.quick_link][:6],
    }


def render_markdown(content):
    safe_content = html.escape(content or "")
    if markdown_lib:
        rendered = markdown_lib.markdown(
            safe_content,
            extensions=["extra", "sane_lists"],
            output_format="html5",
        )
    else:
        rendered = _render_markdown_basico(safe_content)
    rendered = re.sub(
        r"\[INSERIR IMAGEM:\s*(.*?)\]",
        r'<span class="help-placeholder"><i class="bi bi-image me-1"></i>INSERIR IMAGEM: \1</span>',
        rendered,
    )
    return mark_safe(rendered)


def _render_markdown_basico(content):
    lines = content.splitlines()
    html_lines = []
    in_list = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            continue
        if stripped.startswith("# "):
            html_lines.append(f"<h1>{stripped[2:]}</h1>")
        elif stripped.startswith("## "):
            html_lines.append(f"<h2>{stripped[3:]}</h2>")
        elif stripped.startswith("!["):
            match = re.match(r"!\[(.*?)\]\((.*?)\)", stripped)
            if match:
                alt, src = match.groups()
                html_lines.append(f'<p><img src="{src}" alt="{alt}"></p>')
            else:
                html_lines.append(f"<p>{stripped}</p>")
        elif stripped.startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{stripped[2:]}</li>")
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<p>{stripped}</p>")
    if in_list:
        html_lines.append("</ul>")
    return "\n".join(html_lines)
