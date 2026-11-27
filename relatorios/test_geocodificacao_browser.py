import os
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse

from .tests import _marcar_cadastro_usuario_completo


ROOT = Path(__file__).resolve().parents[1]


@skipUnless(os.environ.get("RELATORIO_BROWSER"), "Requer Playwright e Chromium locais")
@override_settings(STATICFILES_DIRS=[ROOT / "static"], STATIC_ROOT=None)
class GeocodificacaoBrowserTests(StaticLiveServerTestCase):
    def test_busca_confirmacao_mapa_e_invalida_rota(self):
        user = get_user_model().objects.create_user("financeiro-teste", email="financeiro@example.com")
        user.groups.add(Group.objects.get_or_create(name="Financeiro")[0])
        _marcar_cadastro_usuario_completo(user)
        self.client.force_login(user)
        result = subprocess.run(
            ["node", "tests_js/geocodificacao_browser.cjs"], cwd=ROOT,
            env={**os.environ, "RELATORIO_URL": self.live_server_url + reverse("relatorios:relatorio_create"),
                 "RELATORIO_SESSION": self.client.cookies["sessionid"].value},
            capture_output=True, text=True, encoding="utf-8", timeout=100,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
