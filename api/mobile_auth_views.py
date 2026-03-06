import logging

from django.contrib.auth import authenticate, get_user_model
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from allauth.account.models import EmailAddress

logger = logging.getLogger('psn_api')
CustomUser = get_user_model()


def _user_payload(user):
    """Build the standardised user object returned on login/signup."""
    profile = getattr(user, 'profile', None)
    return {
        'id': user.id,
        'email': user.email,
        'is_premium': user.is_premium(),
        'has_psn_linked': bool(profile and profile.is_linked),
        'psn_username': profile.display_psn_username if profile else None,
        'avatar_url': profile.avatar_url if profile else None,
    }


class MobileLoginView(APIView):
    """
    POST /api/v1/auth/login/
    Body: { email, password }
    Returns: { token, user }

    Rate-limited to 5 attempts/min per IP to prevent brute-force.
    """
    permission_classes = [AllowAny]
    authentication_classes = []  # No auth required — this IS the auth endpoint

    @method_decorator(ratelimit(key='ip', rate='5/m', method='POST', block=True))
    def post(self, request):
        email = request.data.get('email', '').strip().lower()
        password = request.data.get('password', '')

        if not email or not password:
            return Response(
                {'error': 'email and password are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = authenticate(request, username=email, password=password)
        if user is None:
            return Response(
                {'error': 'Invalid email or password.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.is_active:
            return Response(
                {'error': 'Account is disabled.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Require email verification (allauth stores this in EmailAddress)
        try:
            email_address = EmailAddress.objects.get(user=user, email=email)
            if not email_address.verified:
                return Response(
                    {'error': 'Please verify your email address before logging in.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
        except EmailAddress.DoesNotExist:
            # Superusers created via manage.py may not have an EmailAddress row
            if not user.is_superuser:
                return Response(
                    {'error': 'Please verify your email address before logging in.'},
                    status=status.HTTP_403_FORBIDDEN,
                )

        token, _ = Token.objects.get_or_create(user=user)
        return Response({'token': token.key, 'user': _user_payload(user)})


class MobileSignupView(APIView):
    """
    POST /api/v1/auth/signup/
    Body: { email, password }
    Returns: { message } — user must verify email before they can log in.

    Delegates to allauth's internal signup machinery to ensure consistent
    validation (min length, common passwords, etc.) and email verification flow.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    @method_decorator(ratelimit(key='ip', rate='3/m', method='POST', block=True))
    def post(self, request):
        from allauth.account.adapter import get_adapter
        from allauth.account.forms import SignupForm
        from allauth.account import app_settings as allauth_settings

        email = request.data.get('email', '').strip().lower()
        password = request.data.get('password', '')

        if not email or not password:
            return Response(
                {'error': 'email and password are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Use allauth's form for consistent validation
        form_data = {
            'email': email,
            'email2': email,
            'password1': password,
            'password2': password,
        }
        form = SignupForm(data=form_data)
        if not form.is_valid():
            # Flatten allauth form errors into a single readable string
            errors = {}
            for field, messages in form.errors.items():
                errors[field] = [str(m) for m in messages]
            return Response({'errors': errors}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = form.save(request)
        except Exception:
            logger.exception("Mobile signup error")
            return Response(
                {'error': 'Signup failed. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {'message': 'Account created. Please check your email to verify your address before logging in.'},
            status=status.HTTP_201_CREATED,
        )


class MobileLogoutView(APIView):
    """
    POST /api/v1/auth/logout/
    Deletes the user's auth token server-side.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            # Also unregister push token if provided
            push_token = request.data.get('push_token')
            if push_token:
                from notifications.models import DeviceToken
                DeviceToken.objects.filter(user=request.user, token=push_token).delete()

            request.user.auth_token.delete()
        except Exception:
            pass  # Token already gone — treat as success
        return Response({'success': True})


class MobilePasswordResetView(APIView):
    """
    POST /api/v1/auth/password-reset/
    Body: { email }
    Triggers allauth's password reset email flow.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    @method_decorator(ratelimit(key='ip', rate='3/m', method='POST', block=True))
    def post(self, request):
        from allauth.account.forms import ResetPasswordForm

        email = request.data.get('email', '').strip().lower()
        if not email:
            return Response(
                {'error': 'email is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        form = ResetPasswordForm(data={'email': email})
        if form.is_valid():
            form.save(request)

        # Always return success to avoid email enumeration
        return Response({
            'message': 'If an account with that email exists, a password reset link has been sent.'
        })
