from django.contrib import admin
from .models import Balance, CoinRequest, CoinType, Duel, Message, Transaction

admin.site.register(CoinType)
admin.site.register(Balance)
admin.site.register(Transaction)
admin.site.register(CoinRequest)
admin.site.register(Message)
admin.site.register(Duel)
