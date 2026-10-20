from django.core.management.base import BaseCommand
from core.models import SiteSettings, Policy

class Command(BaseCommand):
    help = "Create the site settings row and starter policies (edit them in /admin/)."
    def handle(self, *a, **o):
        SiteSettings.load()
        if not Policy.objects.exists():
            Policy.objects.bulk_create([
                Policy(title="Repayment policy", body="Installments are due on the dates in your repayment schedule. Please pay on or before the due date.", order=1),
                Policy(title="Late payment", body="Delayed payments may attract charges as agreed in your loan terms. Contact us if you expect a delay.", order=2),
                Policy(title="Privacy of your data", body="Your personal and payment details are used only for managing your account and are never shared without consent.", order=3),
            ])
        self.stdout.write("Done. Now edit everything in /admin/ with the client's real details.")
