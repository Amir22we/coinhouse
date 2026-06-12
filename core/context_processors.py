def unread_messages(request):
    if request.user.is_authenticated:
        return {"unread_count": request.user.site_messages.filter(is_read=False).count()}
    return {"unread_count": 0}


def panel_pending(request):
    """Счётчик открытых заявок для бейджа в админ-панели."""
    if request.user.is_authenticated and request.user.is_staff:
        from .models import CoinRequest
        return {"panel_pending": CoinRequest.objects.filter(status__in=["pending", "awaiting"]).count()}
    return {"panel_pending": 0}
