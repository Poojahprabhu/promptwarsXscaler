import logging

from rest_framework import status
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import AllowAny
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from .serializers import UserLoginSerializer
from .serializers import UserProfileSerializer
from .serializers import UserRegistrationSerializer

logger = logging.getLogger(__name__)

__all__ = [
    "RegisterView",
    "LoginView",
    "TokenRefreshView",
    "CurrentUserView",
]


def _issue_tokens(user) -> dict:
    """Return a fresh access/refresh token pair for the given user."""
    refresh = RefreshToken.for_user(user)
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
    }


class RegisterView(APIView):
    """Create a new user account and return JWT tokens."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = UserRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(
            {
                "user": UserProfileSerializer(user).data,
                "tokens": _issue_tokens(user),
            },
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    """Authenticate with username/email + password and return JWT tokens."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = UserLoginSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        logger.info("User logged in: %s", user.username)
        return Response(
            {
                "user": UserProfileSerializer(user).data,
                "tokens": _issue_tokens(user),
            },
            status=status.HTTP_200_OK,
        )


class CurrentUserView(RetrieveAPIView):
    """Return the profile of the currently authenticated user."""

    permission_classes = [IsAuthenticated]
    serializer_class = UserProfileSerializer

    def get_object(self):
        return self.request.user
