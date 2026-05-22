from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views
from django.views.generic import RedirectView

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="registration/login.html",
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", RedirectView.as_view(url="/dashboard/", permanent=False)),
    path("", include("relatorios.urls", namespace="relatorios")), 
]

# Em produção, arquivos de mídia devem ser servidos pelo Nginx.
# O Django só expõe MEDIA_URL diretamente em desenvolvimento/homologação.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.ANEXOS_URL, document_root=settings.ANEXOS_ROOT)
