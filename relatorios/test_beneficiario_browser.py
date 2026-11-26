import os
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse

from .models import Cliente, Tecnico
from .tests import _marcar_cadastro_usuario_completo


@skipUnless(os.environ.get("RELATORIO_BROWSER"), "Requer Playwright e Chromium locais")
@override_settings(STATICFILES_DIRS=[Path(__file__).resolve().parents[1] / "static"], STATIC_ROOT=None)
class BeneficiarioBrowserTests(StaticLiveServerTestCase):
    def test_seletor_autosave_e_temas(self):
        user = get_user_model().objects.create_user("ana-piloto", email="ana@piloto.example")
        user.groups.add(Group.objects.get_or_create(name="Financeiro")[0])
        _marcar_cadastro_usuario_completo(user, first_name="Ana", last_name="Piloto")
        ana = Tecnico.objects.create(nome="Ana Piloto", email=user.email, ad_username=user.username)
        bruno = Tecnico.objects.create(nome="Bruno Piloto", email="bruno@piloto.example")
        carla = Tecnico.objects.create(nome="Carla Externa", email="carla@piloto.example")
        cliente = Cliente.objects.create(nome="Cliente Piloto", cidade="Curitiba", uf="PR")
        self.client.force_login(user)
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ["node", "tests_js/beneficiario_browser.cjs"],
            cwd=root,
            env={**os.environ, "RELATORIO_URL": self.live_server_url + reverse("relatorios:relatorio_create"),
                 "RELATORIO_SESSION": self.client.cookies["sessionid"].value,
                 "RELATORIO_IDS": ",".join(str(obj.pk) for obj in (ana, bruno, carla, cliente))},
            capture_output=True, text=True, encoding="utf-8", timeout=110,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
