from django.urls import path
from .mobile_auth_views import (
    MobileLoginView,
    MobileSignupView,
    MobileLogoutView,
    MobilePasswordResetView,
)

urlpatterns = [
    path('login/', MobileLoginView.as_view(), name='mobile-login'),
    path('signup/', MobileSignupView.as_view(), name='mobile-signup'),
    path('logout/', MobileLogoutView.as_view(), name='mobile-logout'),
    path('password-reset/', MobilePasswordResetView.as_view(), name='mobile-password-reset'),
]
