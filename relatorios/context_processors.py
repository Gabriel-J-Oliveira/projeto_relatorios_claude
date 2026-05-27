import json

from relatorios.services.autorizacao_service import permissoes_usuario


def permissoes_erp(request):
    tours_guiados_vistos = {}
    user = getattr(request, "user", None)
    if getattr(user, "is_authenticated", False):
        try:
            tours_guiados_vistos = user.perfil_usuario.tours_guiados_vistos or {}
        except Exception:
            tours_guiados_vistos = {}

    return {
        "permissoes_erp": permissoes_usuario(user),
        "tours_guiados_vistos": tours_guiados_vistos,
        "tours_guiados_vistos_json": json.dumps(tours_guiados_vistos),
    }
