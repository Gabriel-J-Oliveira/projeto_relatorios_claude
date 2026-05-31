import html
import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from django.db import OperationalError, ProgrammingError
from django.utils.safestring import mark_safe

from relatorios.models import (
    ArtigoAjuda,
    CategoriaAjuda,
    FormatoArtigoAjuda,
    PublicoArtigoAjuda,
)
from relatorios.services.autorizacao_service import (
    usuario_eh_admin_erp,
    usuario_eh_administrativo,
    usuario_tem_acesso_total,
)


logger = logging.getLogger(__name__)

HELP_CONTENT_DIR = Path(__file__).resolve().parent.parent / "help_content"


try:
    import markdown as markdown_lib
except ImportError:  # pragma: no cover - fallback para ambientes sem dependencia instalada.
    markdown_lib = None

try:
    import bleach
except ImportError:  # pragma: no cover - fallback quando a dependencia ainda nao foi instalada.
    bleach = None


@dataclass(frozen=True)
class HelpCategory:
    slug: str
    title: str
    description: str
    icon: str
    count: int = 0
    ordem: int = 0
    db_id: int | None = None


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
    db_id: int | None = None
    formato: str = "markdown"
    content: str = ""
    related_slugs: tuple[str, ...] = ()


def usuario_pode_editar_ajuda(user):
    return bool(
        usuario_tem_acesso_total(user)
        or usuario_eh_admin_erp(user)
        or getattr(user, "is_staff", False)
    )


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
            ordem=int(item.get("order") or item.get("ordem") or 0),
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
        raise ValueError("Caminho de artigo invalido.")
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Artigo da central de ajuda ausente. path=%s", relative_path)
        return "# Conteudo indisponivel\n\nEste artigo ainda nao foi publicado."


def _db_seguro():
    try:
        CategoriaAjuda.objects.exists()
        return True
    except (OperationalError, ProgrammingError):
        return False


def _category_from_model(category):
    return HelpCategory(
        slug=category.slug,
        title=category.titulo,
        description=category.descricao,
        icon=category.icone or "bi-question-circle",
        ordem=category.ordem,
        db_id=category.pk,
    )


def _article_from_model(article):
    return HelpArticle(
        slug=article.slug,
        title=article.titulo,
        category=article.categoria.slug,
        profile=tuple(article.publico_lista),
        tags=tuple(article.tags_lista),
        path="",
        summary=article.resumo,
        important=article.importante,
        quick_link=article.link_rapido,
        tour_url=article.tour_url,
        db_id=article.pk,
        formato=article.formato,
        content=article.conteudo,
        related_slugs=tuple(article.artigos_relacionados.filter(ativo=True).values_list("slug", flat=True)),
    )


def _perfis_visiveis(user):
    if usuario_tem_acesso_total(user) or usuario_eh_administrativo(user):
        return {"admin", "financeiro", "tecnico", "geral", "todos"}
    return {"tecnico", "geral", "todos"}


def artigo_visivel_para_usuario(article, user):
    perfis = set(article.profile or ("geral",))
    return bool(perfis & _perfis_visiveis(user))


def listar_categorias():
    file_categories, _articles, _category_map = _load_index()
    categories = {category.slug: category for category in file_categories}
    if _db_seguro():
        for category in CategoriaAjuda.objects.filter(ativo=True).order_by("ordem", "titulo"):
            categories[category.slug] = _category_from_model(category)
    return sorted(categories.values(), key=lambda item: (item.ordem, item.title))


def listar_artigos(user):
    _categories, file_articles, _category_map = _load_index()
    articles = []
    db_slugs = set()

    if _db_seguro():
        db_slugs = set(ArtigoAjuda.objects.values_list("slug", flat=True))
        db_articles = (
            ArtigoAjuda.objects.filter(ativo=True, categoria__ativo=True)
            .select_related("categoria")
            .order_by("categoria__ordem", "titulo")
        )
        for article in db_articles:
            item = _article_from_model(article)
            if artigo_visivel_para_usuario(item, user):
                articles.append(item)

    for article in file_articles:
        if article.slug in db_slugs:
            continue
        if artigo_visivel_para_usuario(article, user):
            articles.append(article)

    return articles


def listar_categorias_com_contagem(user):
    counts = {}
    for article in listar_artigos(user):
        counts[article.category] = counts.get(article.category, 0) + 1
    return [
        {
            "slug": category.slug,
            "title": category.title,
            "description": category.description,
            "icon": category.icon,
            "count": counts.get(category.slug, 0),
        }
        for category in listar_categorias()
        if counts.get(category.slug, 0)
    ]


def buscar_artigos(user, termo, categoria_slug=""):
    termo_normalizado = _normalizar(termo)
    articles = listar_artigos(user)
    if categoria_slug:
        articles = [article for article in articles if article.category == categoria_slug]
    if not termo_normalizado:
        return articles

    category_map = {category.slug: category for category in listar_categorias()}
    results = []
    for article in articles:
        content = article.content or _read_markdown(article.path)
        category = category_map.get(article.category)
        haystack = _normalizar(
            " ".join(
                [
                    article.title,
                    article.summary,
                    " ".join(article.tags),
                    category.title if category else article.category,
                    content,
                ]
            )
        )
        if termo_normalizado in haystack:
            results.append(article)
    return results


def obter_artigo(user, slug):
    categories = {category.slug: category for category in listar_categorias()}
    articles = listar_artigos(user)
    articles_by_slug = {item.slug: item for item in articles}
    for article in articles:
        if article.slug != slug:
            continue
        content = article.content or _read_markdown(article.path)
        related = [
            articles_by_slug[related_slug]
            for related_slug in article.related_slugs
            if related_slug in articles_by_slug and related_slug != article.slug
        ]
        if not related:
            related = [
                item
                for item in articles
                if item.category == article.category and item.slug != article.slug
            ][:8]
        return {
            "article": article,
            "category": categories.get(article.category),
            "content": content,
            "html": render_article_content(content, article.formato),
            "related": related,
            "categories": listar_categorias_com_contagem(user),
            "can_edit_help": usuario_pode_editar_ajuda(user),
        }
    return None


def obter_categoria(user, slug, termo=""):
    categories = {category.slug: category for category in listar_categorias()}
    category = categories.get(slug)
    if not category:
        return None
    articles = buscar_artigos(user, termo, categoria_slug=slug)
    return {
        "category": category,
        "query": termo,
        "articles": articles,
        "categories": listar_categorias_com_contagem(user),
        "can_edit_help": usuario_pode_editar_ajuda(user),
    }


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
        "can_edit_help": usuario_pode_editar_ajuda(user),
    }


def materializar_artigo_arquivo(slug, usuario=None):
    if not _db_seguro():
        return None
    artigo_db = ArtigoAjuda.objects.filter(slug=slug).select_related("categoria").first()
    if artigo_db:
        return artigo_db

    categories, articles, category_map = _load_index()
    file_article = next((article for article in articles if article.slug == slug), None)
    if not file_article:
        return None
    file_category = category_map.get(file_article.category)
    if not file_category:
        return None

    categoria, _created = CategoriaAjuda.objects.update_or_create(
        slug=file_category.slug,
        defaults={
            "titulo": file_category.title,
            "descricao": file_category.description,
            "icone": file_category.icon,
            "ordem": file_category.ordem,
            "ativo": True,
        },
    )
    artigo, _created = ArtigoAjuda.objects.update_or_create(
        slug=file_article.slug,
        defaults={
            "categoria": categoria,
            "titulo": file_article.title,
            "resumo": file_article.summary,
            "conteudo": _read_markdown(file_article.path),
            "formato": FormatoArtigoAjuda.MARKDOWN,
            "tags": list(file_article.tags),
            "publico_para": list(file_article.profile or [PublicoArtigoAjuda.TODOS]),
            "importante": file_article.important,
            "link_rapido": file_article.quick_link,
            "tour_url": file_article.tour_url,
            "ativo": True,
            "criado_por": usuario if getattr(usuario, "is_authenticated", False) else None,
            "atualizado_por": usuario if getattr(usuario, "is_authenticated", False) else None,
        },
    )
    return artigo


def sanitizar_html(conteudo):
    conteudo = conteudo or ""
    if bleach:
        allowed_classes = {
            "text-primary", "text-secondary", "text-success", "text-danger",
            "text-warning", "text-info", "alert", "alert-primary",
            "alert-secondary", "alert-success", "alert-danger", "alert-warning",
            "alert-info", "table", "table-sm", "table-bordered", "img-fluid",
            "help-placeholder",
        }

        def _atributo_seguro(tag, name, value):
            if name == "class":
                classes = str(value or "").split()
                return bool(classes) and all(classe in allowed_classes for classe in classes)
            if name in {"title", "alt", "scope", "colspan", "rowspan", "href", "src", "target", "rel"}:
                return True
            return False

        allowed_tags = [
            "p", "br", "strong", "b", "em", "i", "u", "ul", "ol", "li",
            "h1", "h2", "h3", "h4", "table", "thead", "tbody", "tr", "th", "td",
            "a", "img", "blockquote", "div", "span", "hr", "pre", "code",
        ]
        return bleach.clean(
            conteudo,
            tags=allowed_tags,
            attributes=_atributo_seguro,
            protocols=["http", "https", "mailto"],
            strip=True,
        )

    conteudo = re.sub(r"<\s*(script|iframe|object|embed|style).*?>.*?<\s*/\s*\1\s*>", "", conteudo, flags=re.I | re.S)
    conteudo = re.sub(r"\s+on[a-z]+\s*=\s*(['\"]).*?\1", "", conteudo, flags=re.I | re.S)
    conteudo = re.sub(r"javascript\s*:", "", conteudo, flags=re.I)
    return conteudo


def render_article_content(content, formato="markdown"):
    if formato == FormatoArtigoAjuda.HTML:
        return mark_safe(_formatar_placeholders(sanitizar_html(content)))
    return render_markdown(content)


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
    return mark_safe(_formatar_placeholders(sanitizar_html(rendered)))


def _formatar_placeholders(rendered):
    return re.sub(
        r"\[INSERIR IMAGEM:\s*(.*?)\]",
        r'<span class="help-placeholder"><i class="bi bi-image me-1"></i>INSERIR IMAGEM: \1</span>',
        rendered or "",
    )


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
