import mimetypes
import os

from django import forms
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.db import transaction

from .models import Customer, Loan, Enquiry, LoanApplication, ApplicationDocument, PaymentClaim, UserProfile

User = get_user_model()


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ["name", "phone", "alt_phone", "address", "id_note", "notes"]
        widgets = {"address": forms.Textarea(attrs={"rows": 2}), "notes": forms.Textarea(attrs={"rows": 2})}


class LoanForm(forms.ModelForm):
    class Meta:
        model = Loan
        fields = ["principal", "interest_percent", "tenure", "frequency", "start_date"]
        widgets = {"start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}

    def clean_tenure(self):
        t = self.cleaned_data["tenure"]
        if not 1 <= t <= 600:
            raise forms.ValidationError("Enter between 1 and 600 installments.")
        return t


class EnquiryForm(forms.ModelForm):
    class Meta:
        model = Enquiry
        fields = ["name", "phone", "message"]
        widgets = {"message": forms.Textarea(attrs={"rows": 4})}


class CustomerRegistrationForm(UserCreationForm):
    """Registration form for customers creating a self-service portal account."""

    phone = forms.CharField(max_length=20, required=True, label="Mobile number")

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "email")

    field_order = ["username", "first_name", "last_name", "phone", "email", "password1", "password2"]

    def clean_phone(self):
        phone = self.cleaned_data.get("phone", "").strip()
        digits = "".join(c for c in phone if c.isdigit())
        if len(digits) == 12 and digits.startswith("91"):
            digits = digits[2:]
        elif len(digits) == 11 and digits.startswith("0"):
            digits = digits[1:]
        if len(digits) != 10 or any(c.isalpha() for c in phone):
            raise forms.ValidationError("Please enter a valid 10-digit mobile number.")
        return digits

    def clean(self):
        cleaned_data = super().clean()
        phone = cleaned_data.get("phone")
        if phone:
            digits = "".join(c for c in phone if c.isdigit())
            if len(digits) != 10:
                self.add_error("phone", "Phone number must be exactly 10 digits.")
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.first_name = self.cleaned_data.get("first_name", "")
        user.last_name = self.cleaned_data.get("last_name", "")
        user.email = self.cleaned_data.get("email", "")

        def _setup_profile(u):
            profile, created = UserProfile.objects.get_or_create(
                user=u,
                defaults={
                    "role": UserProfile.ROLE_CUSTOMER,
                    "status": UserProfile.STATUS_ACTIVE,
                    "phone": self.cleaned_data.get("phone", ""),
                },
            )
            if not created:
                profile.role = UserProfile.ROLE_CUSTOMER
                profile.status = UserProfile.STATUS_ACTIVE
                profile.phone = self.cleaned_data.get("phone", "")
                profile.save()
            if not profile.sfc_id:
                UserProfile.assign_sfc_id(profile)
            return profile

        if commit:
            with transaction.atomic():
                user.save()
                _setup_profile(user)
        else:
            orig_save_m2m = getattr(self, "save_m2m", None)

            def save_m2m():
                if callable(orig_save_m2m):
                    orig_save_m2m()
                _setup_profile(user)

            self.save_m2m = save_m2m

        return user


class StaffRegistrationRequestForm(CustomerRegistrationForm):
    """Registration request form for prospective staff members awaiting manager approval."""

    def save(self, commit=True):
        user = super(CustomerRegistrationForm, self).save(commit=False)
        user.first_name = self.cleaned_data.get("first_name", "")
        user.last_name = self.cleaned_data.get("last_name", "")
        user.email = self.cleaned_data.get("email", "")
        user.is_active = False

        def _setup_profile(u):
            profile, created = UserProfile.objects.get_or_create(
                user=u,
                defaults={
                    "role": UserProfile.ROLE_STAFF,
                    "status": UserProfile.STATUS_PENDING,
                    "phone": self.cleaned_data.get("phone", ""),
                },
            )
            if not created:
                profile.role = UserProfile.ROLE_STAFF
                profile.status = UserProfile.STATUS_PENDING
                profile.phone = self.cleaned_data.get("phone", "")
                profile.save()
            if not profile.sfc_id:
                UserProfile.assign_sfc_id(profile)
            return profile

        if commit:
            with transaction.atomic():
                user.save()
                _setup_profile(user)
        else:
            orig_save_m2m = getattr(self, "save_m2m", None)

            def save_m2m():
                if callable(orig_save_m2m):
                    orig_save_m2m()
                _setup_profile(user)

            self.save_m2m = save_m2m

        return user


class LoanApplicationForm(forms.ModelForm):
    """Customer-submitted loan application form for digital onboarding."""

    class Meta:
        model = LoanApplication
        fields = [
            "full_name",
            "mobile",
            "email",
            "residential_address",
            "area",
            "pincode",
            "customer_type",
            "designation",
            "company_name",
            "office_address",
            "id_type",
            "ref_name",
            "ref_relationship",
            "ref_mobile",
            "cheque_amount",
            "cheque_details",
            "decl_info_accurate",
            "decl_docs_genuine",
            "decl_privacy_read",
            "decl_consent_data",
        ]
        widgets = {
            "residential_address": forms.Textarea(attrs={"rows": 3}),
            "office_address": forms.Textarea(attrs={"rows": 3}),
            "cheque_details": forms.Textarea(attrs={"rows": 3}),
            "decl_info_accurate": forms.CheckboxInput(),
            "decl_docs_genuine": forms.CheckboxInput(),
            "decl_privacy_read": forms.CheckboxInput(),
            "decl_consent_data": forms.CheckboxInput(),
        }

    def clean_mobile(self):
        mobile = self.cleaned_data.get("mobile", "").strip()
        digits = "".join(c for c in mobile if c.isdigit())
        if len(digits) == 12 and digits.startswith("91"):
            digits = digits[2:]
        elif len(digits) == 11 and digits.startswith("0"):
            digits = digits[1:]
        if len(digits) != 10 or any(c.isalpha() for c in mobile):
            raise forms.ValidationError("Please enter a valid 10-digit mobile number.")
        return digits

    def clean_ref_mobile(self):
        ref_mobile = self.cleaned_data.get("ref_mobile", "").strip()
        if not ref_mobile:
            return ""
        digits = "".join(c for c in ref_mobile if c.isdigit())
        if len(digits) == 12 and digits.startswith("91"):
            digits = digits[2:]
        elif len(digits) == 11 and digits.startswith("0"):
            digits = digits[1:]
        if len(digits) != 10 or any(c.isalpha() for c in ref_mobile):
            raise forms.ValidationError("Please enter a valid 10-digit mobile number.")
        return digits

    def clean(self):
        cleaned_data = super().clean()
        decl_checks = [
            ("decl_info_accurate", "You must confirm that all provided information is accurate."),
            ("decl_docs_genuine", "You must confirm that all uploaded documents are genuine."),
            ("decl_privacy_read", "You must confirm that you have read and agreed to the privacy policy."),
            ("decl_consent_data", "You must consent to data processing to submit an application."),
        ]
        for field, error_msg in decl_checks:
            if not cleaned_data.get(field):
                self.add_error(field, error_msg)
        return cleaned_data


class DocumentUploadForm(forms.ModelForm):
    """Upload form for loan application supporting documents."""

    MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
    ALLOWED_TYPES = ["image/jpeg", "image/png", "application/pdf"]

    class Meta:
        model = ApplicationDocument
        fields = ["doc_type", "file"]

    def clean_file(self):
        file = self.cleaned_data.get("file")
        if not file:
            return file

        if file.size > self.MAX_FILE_SIZE:
            raise forms.ValidationError("File size must not exceed 5MB.")

        mime_type = getattr(file, "content_type", "")
        if not mime_type or mime_type == "application/octet-stream":
            guessed, _ = mimetypes.guess_type(file.name)
            if guessed:
                mime_type = guessed

        if mime_type not in self.ALLOWED_TYPES:
            raise forms.ValidationError(
                "Unsupported file type. Only JPG, PNG, and PDF files are allowed."
            )

        return file

    def save(self, commit=True):
        doc = super().save(commit=False)
        file = self.cleaned_data.get("file")
        if file:
            doc.original_name = os.path.basename(file.name)
            mime_type = getattr(file, "content_type", "")
            if not mime_type or mime_type == "application/octet-stream":
                guessed, _ = mimetypes.guess_type(file.name)
                mime_type = guessed or "application/octet-stream"
            doc.mime_type = mime_type
        if commit:
            doc.save()
        return doc


class PaymentClaimForm(forms.ModelForm):
    """Payment claim submission form for UPI and Bank transfer installments."""

    class Meta:
        model = PaymentClaim
        fields = ["method", "amount_claimed", "txn_ref", "txn_date", "note"]
        widgets = {
            "txn_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "note": forms.Textarea(attrs={"rows": 2}),
        }

    def clean_amount_claimed(self):
        amount = self.cleaned_data.get("amount_claimed")
        if amount is not None and amount <= 0:
            raise forms.ValidationError("Claimed amount must be greater than zero.")
        return amount


class PortalLoginForm(forms.Form):
    """Login form for customer portal and staff authentication using SFC ID or username."""

    login_id = forms.CharField(label="SFC ID or Username", max_length=150)
    password = forms.CharField(label="Password", widget=forms.PasswordInput)

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request", None)
        if args and hasattr(args[0], "META"):
            self.request = args[0]
            args = args[1:]
        self.user_cache = None
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        login_id = cleaned_data.get("login_id", "").strip()
        password = cleaned_data.get("password")

        if login_id and password:
            user = None
            # Check if login_id matches an SFC ID (e.g. SFC-CUS-00001, SFC-STF-00001)
            profile = UserProfile.objects.filter(sfc_id__iexact=login_id).select_related("user").first()
            if profile:
                user = authenticate(self.request, username=profile.user.get_username(), password=password)

            # If not matched or authenticated by SFC ID, try direct username authentication
            if not user:
                user = authenticate(self.request, username=login_id, password=password)

            if user is None:
                raise forms.ValidationError("Invalid SFC ID / username or password.")
            elif not user.is_active:
                raise forms.ValidationError("This account is inactive or pending approval.")

            self.user_cache = user

        return cleaned_data

    def get_user(self):
        return self.user_cache

