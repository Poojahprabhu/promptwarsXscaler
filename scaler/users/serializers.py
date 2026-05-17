import logging

from django.contrib.auth import authenticate
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

logger = logging.getLogger(__name__)

User = get_user_model()


class UserRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True,
        min_length=8,
        style={"input_type": "password"},
    )

    class Meta:
        model = User
        fields = ("username", "email", "name", "job_title", "password")
        extra_kwargs = {
            "email": {"required": True},
            "name": {"required": False, "default": ""},
            "job_title": {"required": False, "default": ""},
        }

    def validate_email(self, value):
        normalized = value.lower()
        if User.objects.filter(email__iexact=normalized).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return normalized

    def validate_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        return value

    def create(self, validated_data):
        user = User.objects.create_user(
            username=validated_data["username"],
            email=validated_data["email"],
            name=validated_data.get("name", ""),
            job_title=validated_data.get("job_title", ""),
            password=validated_data["password"],
        )
        logger.info("New user registered: %s", user.username)
        return user


class UserLoginSerializer(serializers.Serializer):
    username = serializers.CharField(help_text="Username or email address")
    password = serializers.CharField(write_only=True, style={"input_type": "password"})

    def validate(self, attrs):
        identifier = attrs["username"]
        password = attrs["password"]

        # Allow login via email by resolving to the matching username
        if "@" in identifier:
            try:
                user_obj = User.objects.get(email__iexact=identifier)
                identifier = user_obj.username
            except User.DoesNotExist:
                pass

        user = authenticate(
            request=self.context.get("request"),
            username=identifier,
            password=password,
        )

        if user is None:
            raise serializers.ValidationError("Invalid credentials. Please try again.")

        if not user.is_active:
            raise serializers.ValidationError("This account has been disabled.")

        attrs["user"] = user
        return attrs


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "username", "email", "name", "job_title", "date_joined", "is_active")
        read_only_fields = ("id", "username", "date_joined", "is_active")
