from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import Installment, SiteSettings
from core.sms import send_sms

class Command(BaseCommand):
    help = "SMS customers whose installment is due soon or overdue. Run daily from cron."
    def add_arguments(self, p):
        p.add_argument("--days", type=int, default=1, help="Remind this many days before due date")
        p.add_argument("--dry-run", action="store_true")

    def handle(self, *a, **o):
        today, s = timezone.localdate(), SiteSettings.load()
        qs = (Installment.objects.filter(paid_on__isnull=True, due_date__lte=today + timedelta(days=o["days"]))
              .exclude(reminder_sent_on=today).select_related("loan__customer"))
        sent = 0
        for i in qs:
            c = i.loan.customer
            when = f"was due on {i.due_date:%d-%m-%Y}" if i.due_date < today else f"is due on {i.due_date:%d-%m-%Y}"
            msg = (f"Dear {c.name}, your installment of Rs {i.balance} to {s.company_name} {when}. "
                   f"Please pay at the earliest. Call {s.phone}.")
            if o["dry_run"]:
                self.stdout.write(f"[dry] {c.phone}: {msg}"); continue
            if send_sms(c.phone, msg):
                i.reminder_sent_on = today; i.save(update_fields=["reminder_sent_on"]); sent += 1
            else:
                self.stderr.write(f"FAILED {c.phone} (set FAST2SMS_API_KEY / check number)")
        self.stdout.write(f"Reminders sent: {sent}")
