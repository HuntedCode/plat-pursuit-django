"""
URL configuration for plat_pursuit project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path
from core.views import IndexView
from trophies.views import monitoring_dashboard, token_stats, token_stats_sse

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", IndexView.as_view(), name="home"),

    path('api/token-stats/', token_stats, name='token-stats'),
    path('api/token-stats/sse/', token_stats_sse, name='token-stats-sse'),
    path('monitoring/', monitoring_dashboard, name='monitoring'),
]
