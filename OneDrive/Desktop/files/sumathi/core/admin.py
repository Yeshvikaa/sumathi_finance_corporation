from django.contrib import admin
from .models import *

class InstallmentInline(admin.TabularInline):
    model, extra, readonly_fields = Installment, 0, ("paid_amount", "paid_on")

@admin.register(SiteSettings)
class SiteAdmin(admin.ModelAdmin):
    def has_add_permission(self, r): return not SiteSettings.objects.exists()
    def has_delete_permission(self, r, obj=None): return False

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display, search_fields = ("name", "phone", "created"), ("name", "phone")

@admin.register(Loan)
class LoanAdmin(admin.ModelAdmin):
    list_display, inlines = ("__str__", "principal", "tenure", "frequency"), [InstallmentInline]

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("receipt_no", "installment", "amount", "method", "paid_on")
    def has_delete_permission(self, r, obj=None): return False   # ledger stays intact

admin.site.register([Policy, GalleryImage, Enquiry])


@admin.register(StaffLocation)
class StaffLocationAdmin(admin.ModelAdmin):
    list_display = ("user", "latitude", "longitude", "accuracy", "timestamp", "is_tracking")
    list_filter = ("is_tracking",)
    search_fields = ("user__username", "user__first_name", "user__last_name")
    readonly_fields = ("user", "latitude", "longitude", "accuracy", "timestamp", "is_tracking")
    ordering = ("-timestamp",)

    def has_add_permission(self, request):
        return False  # locations are created by the GPS endpoint only

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser  # only superusers may purge records


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("sfc_id", "user", "role", "status", "phone", "created")
    list_filter = ("role", "status")
    search_fields = ("sfc_id", "user__username", "user__first_name", "phone")
    readonly_fields = ("sfc_id", "created")


@admin.register(LoanApplication)
class LoanApplicationAdmin(admin.ModelAdmin):
    list_display = ("ref_no", "full_name", "mobile", "status", "assigned_to", "created")
    list_filter = ("status", "customer_type")
    search_fields = ("ref_no", "full_name", "mobile")
    readonly_fields = ("ref_no", "created", "updated", "submitted_at")


@admin.register(ApplicationDocument)
class ApplicationDocumentAdmin(admin.ModelAdmin):
    list_display = ("application", "doc_type", "original_name", "uploaded_at")
    list_filter = ("doc_type",)


@admin.register(ApplicationStatusHistory)
class ApplicationStatusHistoryAdmin(admin.ModelAdmin):
    list_display = ("application", "from_status", "to_status", "changed_by", "changed_at")
    readonly_fields = ("application", "from_status", "to_status", "changed_by", "note", "changed_at")

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]


@admin.register(PaymentClaim)
class PaymentClaimAdmin(admin.ModelAdmin):
    list_display = ("id", "claimed_by", "method", "amount_claimed", "status", "created")
    list_filter = ("status", "method")
    search_fields = ("txn_ref", "claimed_by__username")


@admin.register(FAQ)
class FAQAdmin(admin.ModelAdmin):
    list_display = ("question", "order")
    list_editable = ("order",)


