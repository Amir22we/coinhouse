from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path
from core import views

urlpatterns = [
    path("django-admin/", admin.site.urls),

    path("", views.home, name="home"),
    path("info/", views.info, name="info"),
    path("help/", views.help_page, name="help"),

    path("register/", views.register, name="register"),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),

    path("dashboard/", views.dashboard, name="dashboard"),
    path("profile/", views.profile, name="profile"),
    path("inbox/", views.inbox, name="inbox"),

    path("deposit/", views.deposit, name="deposit"),
    path("withdraw/", views.withdraw, name="withdraw"),
    path("requests/", views.my_requests, name="requests"),

    path("games/", views.games, name="games"),
    path("games/duel/", views.duel_view, name="duel"),
    path("games/blackjack/", views.blackjack_view, name="blackjack"),
    path("games/blackjack/bet/", views.blackjack_bet, name="blackjack_bet"),
    path("games/blackjack/<str:action>/", views.blackjack_action, name="blackjack_action"),
    path("games/slots/", views.slots_view, name="slots"),
    path("games/slots/spin/", views.slots_spin, name="slots_spin"),

    path("panel/", views.panel_home, name="panel_home"),
    path("panel/requests/", views.panel_requests, name="panel_requests"),
    path("panel/requests/<int:pk>/", views.panel_request_detail, name="panel_request_detail"),
    path("panel/users/", views.panel_users, name="panel_users"),
    path("panel/economy/", views.panel_economy, name="panel_economy"),
    path("panel/users/<int:pk>/", views.panel_user_detail, name="panel_user_detail"),
]
