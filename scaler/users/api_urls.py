from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .api_views import CurrentUserView
from .api_views import LoginView
from .api_views import RegisterView

urlpatterns = [
    path("auth/register/", RegisterView.as_view(), name="auth-register"),
    path("auth/login/", LoginView.as_view(), name="auth-login"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="auth-token-refresh"),
    path("auth/me/", CurrentUserView.as_view(), name="auth-me"),
]
