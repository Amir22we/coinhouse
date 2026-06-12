from django.core.management.base import BaseCommand
from core.models import CoinType, COIN_CODES


class Command(BaseCommand):
    help = "Создаёт типы коинов"

    def handle(self, *args, **options):
        for i, code in enumerate(COIN_CODES):
            CoinType.objects.update_or_create(code=code, defaults={"order": i, "name": f"{code}-коин"})
        self.stdout.write(self.style.SUCCESS(f"Готово: {len(COIN_CODES)} типов коинов."))
