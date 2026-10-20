import calendar
import os
import uuid
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def add_months(d, n):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y, m)[1]))


def _safe_upload_path(instance, filename):
    """Generate a safe, random filename for uploaded documents.
    
    Original filename is discarded to prevent directory traversal and
    to avoid leaking applicant details in URLs.
    """
    ext = os.path.splitext(filename)[1].lower()
    return f"applications/{uuid.uuid4().hex}{ext}"


# ─────────────────────────────────────────────────────────────────────────────
# Site configuration
# ─────────────────────────────────────────────────────────────────────────────

class SiteSettings(models.Model):
    """Single row: everything the public site shows. Edit in /admin/."""
    company_name   = models.CharField(max_length=120, default="Sumathi Finance Corporation")
    tagline        = models.CharField(max_length=200, default="Trusted finance for families and small businesses.")
    about          = models.TextField(default="Write the company story here from the Django admin.")
    phone          = models.CharField(max_length=20, blank=True)
    whatsapp       = models.CharField(max_length=20, blank=True, help_text="With country code, e.g. 919876543210")
    email          = models.EmailField(blank=True)
    address        = models.TextField(blank=True)
    office_hours   = models.TextField(
        default="Monday – Saturday: 11:00 AM – 8:00 PM\nSunday: Holiday",
        help_text="One line per row",
    )
    map_embed_url  = models.URLField(
        max_length=1000, blank=True,
        help_text="Google Maps > Share > Embed a map > copy the src=... URL only",
    )
    founded_year   = models.PositiveIntegerField(default=1994, help_text="Years of service = this year - founded year")
    customers_override = models.PositiveIntegerField(
        null=True, blank=True, help_text="Leave empty to show the live customer count",
    )
    upi_id         = models.CharField(max_length=100, blank=True, help_text="Official company UPI ID for payment display")
    upi_qr_image   = models.ImageField(upload_to="config/", blank=True, help_text="Official UPI QR code image")
    bank_name      = models.CharField(max_length=100, blank=True)
    bank_account_no = models.CharField(max_length=30, blank=True)
    bank_ifsc      = models.CharField(max_length=20, blank=True)
    bank_account_name = models.CharField(max_length=120, blank=True)

    class Meta:
        verbose_name_plural = "Site settings"

    def save(self, *a, **kw):
        self.pk = 1
        super().save(*a, **kw)

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=1)[0]

    def hours_list(self):
        return [l for l in self.office_hours.splitlines() if l.strip()]

    def __str__(self):
        return self.company_name


# ─────────────────────────────────────────────────────────────────────────────
# Public content
# ─────────────────────────────────────────────────────────────────────────────

class Policy(models.Model):
    title = models.CharField(max_length=150)
    body  = models.TextField()
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]
        verbose_name_plural = "Policies"

    def __str__(self):
        return self.title


class GalleryImage(models.Model):
    title = models.CharField(max_length=120, blank=True)
    image = models.ImageField(upload_to="gallery/")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "-id"]

    def __str__(self):
        return self.title or f"Image {self.pk}"


class FAQ(models.Model):
    question = models.CharField(max_length=300)
    answer   = models.TextField()
    order    = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "FAQ"
        verbose_name_plural = "FAQs"

    def __str__(self):
        return self.question[:80]


class Enquiry(models.Model):
    name    = models.CharField(max_length=100)
    phone   = models.CharField(max_length=20)
    message = models.TextField()
    created = models.DateTimeField(auto_now_add=True)
    handled = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created"]
        verbose_name_plural = "Enquiries"

    def __str__(self):
        return f"{self.name} ({self.created:%d %b})"


# ─────────────────────────────────────────────────────────────────────────────
# User profiles & roles
# ─────────────────────────────────────────────────────────────────────────────

class UserProfile(models.Model):
    """One-to-one extension of Django's User model adding SFC role and auto-ID."""

    ROLE_CUSTOMER = "customer"
    ROLE_STAFF    = "staff"
    ROLE_MANAGER  = "manager"
    ROLE_CHOICES  = [
        (ROLE_CUSTOMER, "Customer"),
        (ROLE_STAFF,    "Staff"),
        (ROLE_MANAGER,  "Manager"),
    ]

    STATUS_ACTIVE  = "active"
    STATUS_PENDING = "pending"   # staff awaiting manager approval
    STATUS_INACTIVE = "inactive"
    STATUS_CHOICES = [
        (STATUS_ACTIVE,   "Active"),
        (STATUS_PENDING,  "Pending approval"),
        (STATUS_INACTIVE, "Inactive"),
    ]

    user        = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    role        = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ROLE_CUSTOMER)
    sfc_id      = models.CharField(max_length=20, unique=True, blank=True, db_index=True)
    status      = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    phone       = models.CharField(max_length=20, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_profiles",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created     = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]

    # ── SFC ID generation ────────────────────────────────────────────────────

    @classmethod
    def _next_seq(cls, prefix):
        """Return next collision-safe sequence number for the given prefix."""
        last = (
            cls.objects
            .filter(sfc_id__startswith=prefix)
            .order_by("-sfc_id")
            .values_list("sfc_id", flat=True)
            .first()
        )
        if last:
            try:
                return int(last.split("-")[-1]) + 1
            except (ValueError, IndexError):
                pass
        return 1

    @classmethod
    @transaction.atomic
    def assign_sfc_id(cls, profile):
        """Assign a unique SFC ID to a profile. Call inside a transaction."""
        prefixes = {
            cls.ROLE_CUSTOMER: "SFC-CUS",
            cls.ROLE_STAFF:    "SFC-STF",
            cls.ROLE_MANAGER:  "SFC-MGR",
        }
        prefix = prefixes.get(profile.role, "SFC-USR")
        n = cls._next_seq(prefix)
        profile.sfc_id = f"{prefix}-{n:05d}"
        profile.save(update_fields=["sfc_id"])

    def __str__(self):
        return f"{self.sfc_id} — {self.user.get_full_name() or self.user.username} ({self.get_role_display()})"

    @property
    def is_customer(self):
        return self.role == self.ROLE_CUSTOMER

    @property
    def is_staff_member(self):
        return self.role == self.ROLE_STAFF

    @property
    def is_manager(self):
        return self.role == self.ROLE_MANAGER

    @property
    def is_active_staff_or_manager(self):
        return self.role in (self.ROLE_STAFF, self.ROLE_MANAGER) and self.status == self.STATUS_ACTIVE


# ─────────────────────────────────────────────────────────────────────────────
# Finance — Customers, Loans, Payments
# ─────────────────────────────────────────────────────────────────────────────

class Customer(models.Model):
    name      = models.CharField(max_length=120)
    phone     = models.CharField(max_length=20)
    alt_phone = models.CharField("Alternate phone", max_length=20, blank=True)
    address   = models.TextField(blank=True)
    id_note   = models.CharField("ID / reference note", max_length=120, blank=True)
    notes     = models.TextField(blank=True)
    created   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.phone})"

    @property
    def balance(self):
        return sum((l.balance for l in self.loans.all()), Decimal("0"))


class Loan(models.Model):
    FREQ = [("daily", "Daily"), ("weekly", "Weekly"), ("monthly", "Monthly")]
    customer         = models.ForeignKey(Customer, related_name="loans", on_delete=models.CASCADE)
    principal        = models.DecimalField("Amount lent (₹)", max_digits=12, decimal_places=2)
    interest_percent = models.DecimalField(
        "Total interest %", max_digits=5, decimal_places=2, default=0,
        help_text="Flat interest on principal for the whole term",
    )
    tenure    = models.PositiveIntegerField("Number of installments")
    frequency = models.CharField(max_length=10, choices=FREQ, default="monthly")
    start_date = models.DateField("First due date", default=timezone.localdate)
    created   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return f"Loan #{self.pk} – {self.customer.name}"

    @property
    def total_payable(self):
        return (self.principal * (1 + self.interest_percent / 100)).quantize(Decimal("0.01"))

    @property
    def paid(self):
        return sum((i.paid_amount for i in self.installments.all()), Decimal("0"))

    @property
    def balance(self):
        return self.total_payable - self.paid

    def due_date_for(self, n):
        if self.frequency == "daily":   return self.start_date + timedelta(days=n)
        if self.frequency == "weekly":  return self.start_date + timedelta(weeks=n)
        return add_months(self.start_date, n)

    def generate_schedule(self):
        total = self.total_payable
        base  = (total / self.tenure).quantize(Decimal("0.01"))
        for i in range(self.tenure):
            amt = base if i < self.tenure - 1 else total - base * (self.tenure - 1)
            Installment.objects.create(loan=self, number=i + 1, due_date=self.due_date_for(i), amount=amt)


class Installment(models.Model):
    loan             = models.ForeignKey(Loan, related_name="installments", on_delete=models.CASCADE)
    number           = models.PositiveIntegerField()
    due_date         = models.DateField()
    amount           = models.DecimalField(max_digits=12, decimal_places=2)
    paid_amount      = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    paid_on          = models.DateField(null=True, blank=True, help_text="Set only when fully paid")
    reminder_sent_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["loan_id", "number"]

    def __str__(self):
        return f"{self.loan} #{self.number}"

    @property
    def balance(self):
        return self.amount - self.paid_amount

    @property
    def is_overdue(self):
        return self.paid_on is None and self.due_date < timezone.localdate()

    @property
    def status(self):
        if self.paid_on:    return "paid"
        if self.is_overdue: return "overdue"
        return "partial" if self.paid_amount > 0 else "upcoming"

    def record_payment(self, amount, method="cash", note="", confirmed_by=None):
        amount = min(Decimal(amount), self.balance)
        if amount <= 0:
            return None
        with transaction.atomic():
            p = Payment.objects.create(
                installment=self, amount=amount,
                method=method, note=note, confirmed_by=confirmed_by,
            )
            self.paid_amount += amount
            if self.paid_amount >= self.amount:
                self.paid_on = timezone.localdate()
            self.save()
        return p


class Payment(models.Model):
    """Immutable ledger of every confirmed payment."""
    METHODS = [
        ("cash",   "Cash"),
        ("upi",    "UPI"),
        ("bank",   "Bank transfer"),
        ("cheque", "Cheque"),
    ]
    installment  = models.ForeignKey(Installment, related_name="payments", on_delete=models.PROTECT)
    amount       = models.DecimalField(max_digits=12, decimal_places=2)
    method       = models.CharField(max_length=10, choices=METHODS, default="cash")
    paid_on      = models.DateField(default=timezone.localdate)
    note         = models.CharField(max_length=200, blank=True)
    receipt_no   = models.CharField(max_length=20, blank=True, editable=False)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="confirmed_payments",
    )
    created      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]

    def save(self, *a, **kw):
        super().save(*a, **kw)
        if not self.receipt_no:
            self.receipt_no = f"SFC-{self.paid_on:%y%m}-{self.pk:05d}"
            super().save(update_fields=["receipt_no"])

    def __str__(self):
        return self.receipt_no


# ─────────────────────────────────────────────────────────────────────────────
# Loan Application Portal
# ─────────────────────────────────────────────────────────────────────────────

class LoanApplication(models.Model):
    """Pre-verification loan application submitted through the portal."""

    STATUS_DRAFT       = "draft"
    STATUS_SUBMITTED   = "submitted"
    STATUS_UNDER_REVIEW = "under_review"
    STATUS_MORE_INFO   = "more_info"
    STATUS_APPROVED    = "approved"
    STATUS_REJECTED    = "rejected"
    STATUS_WITHDRAWN   = "withdrawn"

    STATUS_CHOICES = [
        (STATUS_DRAFT,        "Draft"),
        (STATUS_SUBMITTED,    "Submitted"),
        (STATUS_UNDER_REVIEW, "Under Review"),
        (STATUS_MORE_INFO,    "Additional Information Required"),
        (STATUS_APPROVED,     "Approved"),
        (STATUS_REJECTED,     "Rejected"),
        (STATUS_WITHDRAWN,    "Withdrawn"),
    ]

    ID_AADHAAR  = "aadhaar"
    ID_DL       = "driving_licence"
    ID_PASSPORT = "passport"
    ID_CHOICES  = [
        (ID_AADHAAR,  "Aadhaar Card"),
        (ID_DL,       "Driving Licence"),
        (ID_PASSPORT, "Passport"),
    ]

    CUSTOMER_TYPE_EXISTING = "existing"
    CUSTOMER_TYPE_NEW      = "new"
    CUSTOMER_TYPE_CHOICES  = [
        (CUSTOMER_TYPE_EXISTING, "Existing SFC customer"),
        (CUSTOMER_TYPE_NEW,      "New applicant"),
    ]

    # ── Identity ─────────────────────────────────────────────────────────────
    applicant       = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="applications",
    )
    ref_no          = models.CharField(max_length=20, unique=True, blank=True, db_index=True)
    status          = models.CharField(max_length=15, choices=STATUS_CHOICES, default=STATUS_DRAFT)

    # ── Personal details ──────────────────────────────────────────────────────
    full_name       = models.CharField(max_length=150)
    mobile          = models.CharField(max_length=15)
    email           = models.EmailField(blank=True)
    residential_address = models.TextField()
    area            = models.CharField(max_length=100, blank=True)
    pincode         = models.CharField(max_length=10)
    customer_type   = models.CharField(max_length=10, choices=CUSTOMER_TYPE_CHOICES, default=CUSTOMER_TYPE_NEW)
    designation     = models.CharField("Designation / Occupation", max_length=100, blank=True)
    company_name    = models.CharField("Office / Company name", max_length=150, blank=True)
    office_address  = models.TextField("Office address", blank=True)

    # ── Identity document ─────────────────────────────────────────────────────
    id_type         = models.CharField(max_length=20, choices=ID_CHOICES, blank=True)
    # NOTE: id_number is not stored — only used for reference in physical verification

    # ── Reference / Family contact ────────────────────────────────────────────
    ref_name        = models.CharField("Reference contact name", max_length=100, blank=True)
    ref_relationship = models.CharField("Relationship", max_length=60, blank=True)
    ref_mobile      = models.CharField("Reference contact mobile", max_length=15, blank=True)

    # ── Cheque / transaction details ──────────────────────────────────────────
    cheque_amount   = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    cheque_details  = models.TextField("Cheque / transaction details", blank=True)

    # ── Declarations (stored as booleans) ─────────────────────────────────────
    decl_info_accurate  = models.BooleanField("Information is accurate", default=False)
    decl_docs_genuine   = models.BooleanField("Documents are genuine", default=False)
    decl_privacy_read   = models.BooleanField("Privacy notice read", default=False)
    decl_consent_data   = models.BooleanField("Consent to data processing", default=False)

    # ── Workflow ──────────────────────────────────────────────────────────────
    assigned_to     = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="assigned_applications",
    )
    review_notes    = models.TextField(blank=True)
    rejection_reason = models.TextField(blank=True)
    approved_by     = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="approved_applications",
    )
    approved_at     = models.DateTimeField(null=True, blank=True)
    rejected_by     = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="rejected_applications",
    )
    rejected_at     = models.DateTimeField(null=True, blank=True)

    # ── Linked customer record (after approval) ───────────────────────────────
    linked_customer = models.ForeignKey(
        Customer, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="applications",
    )

    submitted_at    = models.DateTimeField(null=True, blank=True)
    created         = models.DateTimeField(auto_now_add=True)
    updated         = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return f"{self.ref_no} — {self.full_name}"

    def save(self, *a, **kw):
        if not self.ref_no:
            super().save(*a, **kw)   # get PK first
            self.ref_no = f"SFC-APP-{self.pk:06d}"
            super().save(update_fields=["ref_no"])
        else:
            super().save(*a, **kw)

    def get_status_badge_class(self):
        mapping = {
            self.STATUS_DRAFT:        "upcoming",
            self.STATUS_SUBMITTED:    "partial",
            self.STATUS_UNDER_REVIEW: "partial",
            self.STATUS_MORE_INFO:    "warn",
            self.STATUS_APPROVED:     "paid",
            self.STATUS_REJECTED:     "overdue",
            self.STATUS_WITHDRAWN:    "upcoming",
        }
        return mapping.get(self.status, "upcoming")


class ApplicationStatusHistory(models.Model):
    """Immutable audit log of every status change on a LoanApplication."""
    application = models.ForeignKey(LoanApplication, on_delete=models.CASCADE, related_name="history")
    from_status = models.CharField(max_length=15, blank=True)
    to_status   = models.CharField(max_length=15)
    changed_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
    )
    note        = models.TextField(blank=True)
    changed_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["changed_at"]

    def __str__(self):
        return f"{self.application.ref_no}: {self.from_status} → {self.to_status}"


class ApplicationDocument(models.Model):
    """Private uploaded document for a loan application."""

    DOC_ID_PROOF   = "id_proof"
    DOC_PHOTO      = "photo"
    DOC_CHEQUE     = "cheque"
    DOC_SALARY     = "salary_slip"
    DOC_HOUSE      = "house_exterior"
    DOC_STREET     = "street_photo"
    DOC_OTHER      = "other"

    DOC_TYPE_CHOICES = [
        (DOC_ID_PROOF, "Identity Proof"),
        (DOC_PHOTO,    "Profile Photograph"),
        (DOC_CHEQUE,   "Cheque Image"),
        (DOC_SALARY,   "Salary Slip"),
        (DOC_HOUSE,    "House Exterior Photo"),
        (DOC_STREET,   "Street / Neighbourhood Photo"),
        (DOC_OTHER,    "Other"),
    ]

    application = models.ForeignKey(LoanApplication, on_delete=models.CASCADE, related_name="documents")
    doc_type    = models.CharField(max_length=20, choices=DOC_TYPE_CHOICES)
    file        = models.FileField(upload_to=_safe_upload_path)
    original_name = models.CharField(max_length=255, blank=True)  # stored for display only
    mime_type   = models.CharField(max_length=100, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["doc_type"]

    def __str__(self):
        return f"{self.application.ref_no} — {self.get_doc_type_display()}"

    @property
    def is_image(self):
        return self.mime_type.startswith("image/") if self.mime_type else False


# ─────────────────────────────────────────────────────────────────────────────
# Payment claims (UPI / bank transfer — pending verification)
# ─────────────────────────────────────────────────────────────────────────────

class PaymentClaim(models.Model):
    """Customer-submitted payment claim awaiting staff verification."""

    STATUS_PENDING  = "pending"
    STATUS_VERIFIED = "verified"
    STATUS_REJECTED = "rejected"

    STATUS_CHOICES = [
        (STATUS_PENDING,  "Pending Verification"),
        (STATUS_VERIFIED, "Verified"),
        (STATUS_REJECTED, "Rejected"),
    ]

    METHOD_UPI  = "upi"
    METHOD_BANK = "bank"
    METHOD_CHOICES = [
        (METHOD_UPI,  "UPI"),
        (METHOD_BANK, "Bank Transfer"),
    ]

    installment    = models.ForeignKey(Installment, on_delete=models.CASCADE, related_name="claims")
    claimed_by     = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payment_claims")
    method         = models.CharField(max_length=10, choices=METHOD_CHOICES)
    amount_claimed = models.DecimalField(max_digits=12, decimal_places=2)
    txn_ref        = models.CharField("Transaction / UTR reference", max_length=100, blank=True)
    txn_date       = models.DateField("Transaction date", null=True, blank=True)
    note           = models.CharField(max_length=300, blank=True)
    status         = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    verified_by    = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="verified_claims",
    )
    verified_at    = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    linked_payment = models.OneToOneField(
        Payment, null=True, blank=True, on_delete=models.SET_NULL, related_name="claim",
    )
    created        = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return f"Claim #{self.pk} — {self.get_status_display()}"


# ─────────────────────────────────────────────────────────────────────────────
# Staff location tracking
# ─────────────────────────────────────────────────────────────────────────────

class StaffLocation(models.Model):
    """Latest GPS location for each authenticated staff member.

    One row per user — views use update_or_create so the table never
    accumulates historical rows.  is_tracking is False once the staff
    member clicks 'Stop Tracking'.
    """
    user      = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="staff_location",
    )
    latitude  = models.DecimalField(max_digits=10, decimal_places=7, help_text="Decimal degrees, WGS-84")
    longitude = models.DecimalField(max_digits=10, decimal_places=7, help_text="Decimal degrees, WGS-84")
    accuracy  = models.FloatField(help_text="Accuracy radius in metres reported by the browser", default=0)
    timestamp = models.DateTimeField(help_text="UTC time of the last accepted GPS fix")
    is_tracking = models.BooleanField(default=True, help_text="False once the staff member clicks Stop Tracking")

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Staff location"
        verbose_name_plural = "Staff locations"

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} @ {self.timestamp:%d %b %H:%M}"

    @property
    def google_maps_url(self):
        return f"https://www.google.com/maps?q={self.latitude},{self.longitude}"
