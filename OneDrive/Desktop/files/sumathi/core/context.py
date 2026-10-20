from django.utils import timezone
from .models import SiteSettings, Customer, Loan, UserProfile

def site(request):
    s = SiteSettings.load()
    ctx = {
        "site": s,
        "years_of_service": max(timezone.localdate().year - s.founded_year, 1),
        "total_customers": s.customers_override or Customer.objects.count(),
        "total_loans": Loan.objects.count(),
    }
    # Add user profile context if authenticated
    if request.user.is_authenticated:
        try:
            profile = request.user.profile
            ctx["user_profile"] = profile
            ctx["user_role"] = profile.role
            ctx["user_sfc_id"] = profile.sfc_id
        except UserProfile.DoesNotExist:
            pass
    return ctx
