from relatorios.services.autorizacao_service import permissoes_usuario


def permissoes_erp(request):
    return {
        "permissoes_erp": permissoes_usuario(getattr(request, "user", None)),
    }
