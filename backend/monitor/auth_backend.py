from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q


class EmailOrUsernameModelBackend(ModelBackend):
    """
    Allows authentication with either username or email (case-insensitive) alongside password.
    Enables users who registered via Google OAuth (or standard form) to sign in using either
    their username or their Gmail/email address.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD)
        if not username or not password:
            return None

        clean_identifier = username.strip()
        user = UserModel.objects.filter(
            Q(username__iexact=clean_identifier) | Q(email__iexact=clean_identifier)
        ).order_by('id').first()

        if user and user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
