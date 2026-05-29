import json

from relatorios.services.autorizacao_service import permissoes_usuario
from relatorios.services.clientes_valor_km_service import (
    clientes_pendentes_valor_km,
    usuario_pode_configurar_valor_km,
)
from relatorios.services.notificacoes_service import (
    obter_notificacoes_usuario,
    total_notificacoes,
)


def permissoes_erp(request):
    tours_guiados_vistos = {}
    user = getattr(request, "user", None)
    if getattr(user, "is_authenticated", False):
        try:
            tours_guiados_vistos = user.perfil_usuario.tours_guiados_vistos or {}
        except Exception:
            tours_guiados_vistos = {}
    permissoes = permissoes_usuario(user)
    clientes_sem_valor_km = []
    clientes_sem_valor_km_count = 0
    pode_configurar_valor_km = usuario_pode_configurar_valor_km(user)
    if pode_configurar_valor_km:
        try:
            qs_pendentes = clientes_pendentes_valor_km(user, apenas_api_novos=True)
            clientes_sem_valor_km_count = qs_pendentes.count()
            clientes_sem_valor_km = list(qs_pendentes[:20])
        except Exception:
            clientes_sem_valor_km = []
            clientes_sem_valor_km_count = 0
    try:
        notificacoes = obter_notificacoes_usuario(user)
    except Exception:
        notificacoes = []

    return {
        "permissoes_erp": permissoes,
        "tours_guiados_vistos": tours_guiados_vistos,
        "tours_guiados_vistos_json": json.dumps(tours_guiados_vistos),
        "clientes_sem_valor_km": clientes_sem_valor_km,
        "clientes_sem_valor_km_count": clientes_sem_valor_km_count,
        "pode_configurar_valor_km": pode_configurar_valor_km,
        "notificacoes_usuario": notificacoes,
        "notificacoes_usuario_total": total_notificacoes(notificacoes),
    }
