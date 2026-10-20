import json
import os
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import F, Q, Sum
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from .forms import (CustomerForm, EnquiryForm, LoanForm, 
                    CustomerRegistrationForm, StaffRegistrationRequestForm, 
                    LoanApplicationForm, DocumentUploadForm, PaymentClaimForm, PortalLoginForm)
from .models import (Customer, Enquiry, GalleryImage, Installment, Payment, 
                     Policy, SiteSettings, StaffLocation, 
                     LoanApplication, ApplicationStatusHistory, ApplicationDocument, 
                     PaymentClaim, UserProfile, FAQ)

def _is_manage_user(u):
    """True for Django is_staff users AND for active portal staff/manager accounts."""
    if not u.is_authenticated:
        return False
    if u.is_staff or u.is_superuser:
        return True
    try:
        p = u.profile
        return p.role in (UserProfile.ROLE_STAFF, UserProfile.ROLE_MANAGER) and p.status == UserProfile.STATUS_ACTIVE
    except Exception:
        return False

staff_required = user_passes_test(_is_manage_user, login_url="/manage/login/")
ZERO = Decimal("0")

# ---------- public site ----------
def home(request):
    return render(request, "public/home.html", {"policies": Policy.objects.all()[:3]})

def about(request):
    return render(request, "public/about.html", {"policies": Policy.objects.all()})

def gallery(request):
    return render(request, "public/gallery.html", {"images": GalleryImage.objects.all()})

def contact(request):
    form = EnquiryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Thank you! We have received your message and will call you back.")
        return redirect("contact")
    return render(request, "public/contact.html", {"form": form})

def robots(request):
    return HttpResponse(f"User-agent: *\nDisallow: /manage/\nDisallow: /admin/\nSitemap: {request.build_absolute_uri('/sitemap.xml')}\n",
                        content_type="text/plain")

# ---------- internal app ----------
def _wa_link(inst):
    s = SiteSettings.load()
    digits = "".join(c for c in inst.loan.customer.phone if c.isdigit())[-10:]
    when = "was due on" if inst.is_overdue else "is due on"
    text = (f"Dear {inst.loan.customer.name}, your installment of Rs {inst.balance} to {s.company_name} "
            f"{when} {inst.due_date:%d-%m-%Y}. Please pay at the earliest. Thank you.")
    return f"https://wa.me/91{digits}?text={quote(text)}"

@staff_required
@ensure_csrf_cookie
def dashboard(request):
    today = timezone.localdate()
    unpaid = Installment.objects.filter(paid_on__isnull=True)
    owed = F("amount") - F("paid_amount")
    pay = Payment.objects
    ctx = {
        "collected": pay.aggregate(t=Sum("amount"))["t"] or ZERO,
        "collected_month": pay.filter(paid_on__year=today.year, paid_on__month=today.month).aggregate(t=Sum("amount"))["t"] or ZERO,
        "pending": unpaid.aggregate(t=Sum(owed))["t"] or ZERO,
        "overdue": unpaid.filter(due_date__lt=today).aggregate(t=Sum(owed))["t"] or ZERO,
        "overdue_count": unpaid.filter(due_date__lt=today).count(),
        "today_count": unpaid.filter(due_date=today).count(),
        "customers_count": Customer.objects.count(),
        "new_enquiries": Enquiry.objects.filter(handled=False).count(),
        "due_soon": unpaid.filter(due_date__lte=today + timedelta(days=7)).select_related("loan__customer").order_by("due_date")[:12],
        "recent": pay.select_related("installment__loan__customer")[:8],
    }
    return render(request, "manage/dashboard.html", ctx)

@staff_required
def customers(request):
    q = request.GET.get("q", "").strip()
    qs = Customer.objects.prefetch_related("loans__installments")
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(phone__icontains=q))
    return render(request, "manage/customers.html", {"customers": qs, "q": q})

@staff_required
def customer_new(request):
    form = CustomerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        c = form.save()
        messages.success(request, f"{c.name} added. Now create their loan.")
        return redirect("loan_new", pk=c.pk)
    return render(request, "manage/form.html", {"form": form, "title": "New customer"})

@staff_required
def customer_detail(request, pk):
    c = get_object_or_404(Customer.objects.prefetch_related("loans__installments"), pk=pk)
    return render(request, "manage/customer_detail.html", {"c": c})

@staff_required
def loan_new(request, pk):
    c = get_object_or_404(Customer, pk=pk)
    form = LoanForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        loan = form.save(commit=False)
        loan.customer = c
        loan.save()
        loan.generate_schedule()
        messages.success(request, f"Loan created with {loan.tenure} installments.")
        return redirect("customer_detail", pk=c.pk)
    return render(request, "manage/form.html", {"form": form, "title": f"New loan for {c.name}"})

@staff_required
def installments(request):
    f, today = request.GET.get("f", "overdue"), timezone.localdate()
    qs = Installment.objects.select_related("loan__customer")
    if f == "paid":
        qs = qs.filter(paid_on__isnull=False).order_by("-paid_on")
    else:
        qs = qs.filter(paid_on__isnull=True).order_by("due_date")
        if f == "overdue": qs = qs.filter(due_date__lt=today)
        elif f == "today": qs = qs.filter(due_date=today)
        elif f == "week": qs = qs.filter(due_date__range=(today, today + timedelta(days=7)))
    return render(request, "manage/installments.html", {"items": qs[:300], "f": f})

@staff_required
@require_POST
def pay(request, pk):
    inst = get_object_or_404(Installment.objects.select_related("loan__customer"), pk=pk)
    try:
        amt = Decimal(request.POST.get("amount") or inst.balance)
    except InvalidOperation:
        amt = inst.balance
    p = inst.record_payment(amt, request.POST.get("method", "cash"))
    if p:
        messages.success(request, f"Receipt {p.receipt_no}: ₹{p.amount} received from {inst.loan.customer.name}.")
    else:
        messages.error(request, "Nothing to record – installment already paid or amount invalid.")
    nxt = request.POST.get("next", "")
    ok = url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()})
    return redirect(nxt if ok else "dashboard")

@staff_required
def receipt(request, pk):
    p = get_object_or_404(Payment.objects.select_related("installment__loan__customer"), pk=pk)
    return render(request, "manage/receipt.html", {"p": p})

@staff_required
def reminders(request):
    today = timezone.localdate()
    items = list(Installment.objects.filter(paid_on__isnull=True, due_date__lte=today + timedelta(days=2))
                 .select_related("loan__customer").order_by("due_date"))
    for i in items:
        i.wa_link = _wa_link(i)
    return render(request, "manage/reminders.html", {"items": items})

@staff_required
def enquiries(request):
    if request.method == "POST":
        Enquiry.objects.filter(pk=request.POST.get("id")).update(handled=True)
        return redirect("enquiries")
    return render(request, "manage/enquiries.html", {"items": Enquiry.objects.all()[:100]})


# ---------- staff location tracking ----------

@login_required(login_url="/portal/login/")
@require_POST
def submit_location(request):
    """Receive a GPS fix from an authenticated staff member's browser.
    Only active staff/manager accounts or Django is_staff users may call this.
    Accepts application/json (regular AJAX) or multipart/form-data (sendBeacon on unload).
    """
    # Only allow active staff/manager or is_staff users
    if not _is_manage_user(request.user):
        return JsonResponse({"error": "Access denied. Staff only."}, status=403)

    # sendBeacon sends FormData with a _beacon=1 flag
    is_beacon = request.POST.get("_beacon") == "1"

    if is_beacon:
        # Parse from form POST data
        try:
            lat = float(request.POST.get("latitude"))
            lon = float(request.POST.get("longitude"))
            acc = float(request.POST.get("accuracy", 0))
            is_tracking = request.POST.get("is_tracking", "true").lower() == "true"
        except (TypeError, ValueError):
            return JsonResponse({"error": "Invalid form data."}, status=400)
    else:
        # Parse from JSON body
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        try:
            lat = float(data.get("latitude"))
            lon = float(data.get("longitude"))
            acc = float(data.get("accuracy", 0))
            is_tracking = bool(data.get("is_tracking", True))
        except (TypeError, ValueError):
            return JsonResponse(
                {"error": "latitude, longitude and accuracy must be numbers."},
                status=400,
            )

    if not (-90 <= lat <= 90):
        return JsonResponse({"error": "latitude must be between -90 and 90."}, status=400)
    if not (-180 <= lon <= 180):
        return JsonResponse({"error": "longitude must be between -180 and 180."}, status=400)
    if acc < 0:
        return JsonResponse({"error": "accuracy must be non-negative."}, status=400)

    StaffLocation.objects.update_or_create(
        user=request.user,
        defaults={
            "latitude": round(lat, 7),
            "longitude": round(lon, 7),
            "accuracy": acc,
            "timestamp": timezone.now(),
            "is_tracking": is_tracking,
        },
    )

    # sendBeacon ignores the response body; 204 is the correct status.
    if is_beacon:
        from django.http import HttpResponse as _HR
        return _HR(status=204)
    return JsonResponse({"status": "ok"})


@staff_required
def location_map(request):
    """Show all staff members' latest recorded locations on a map.

    Only accessible to is_staff users (same guard as dashboard).
    Locations are serialised to JSON and passed to the Leaflet template.
    We show both actively-tracking and recently-updated (last 24 h) entries
    so managers can see who was last online.
    """
    cutoff = timezone.now() - timedelta(hours=24)
    locations = (
        StaffLocation.objects
        .select_related("user")
        .filter(timestamp__gte=cutoff)
        .order_by("-timestamp")
    )

    # Build a JSON-safe list for the template
    loc_data = []
    for loc in locations:
        display_name = loc.user.get_full_name() or loc.user.username
        loc_data.append({
            "name": display_name,
            "lat": float(loc.latitude),
            "lon": float(loc.longitude),
            "accuracy": loc.accuracy,
            "timestamp": loc.timestamp.astimezone(
                timezone.get_current_timezone()
            ).strftime("%d %b %Y, %I:%M %p"),
            "is_tracking": loc.is_tracking,
            "maps_url": loc.google_maps_url,
        })

    return render(request, "manage/location_map.html", {
        "locations": locations,
        "loc_json": json.dumps(loc_data),
    })


@staff_required
def location_data(request):
    """JSON API: return latest staff locations for AJAX map refresh."""
    cutoff = timezone.now() - timedelta(hours=24)
    locations = (
        StaffLocation.objects
        .select_related("user")
        .filter(timestamp__gte=cutoff)
        .order_by("-timestamp")
    )
    loc_data = []
    for loc in locations:
        try:
            profile = loc.user.profile
            sfc_id = profile.sfc_id
        except Exception:
            sfc_id = ""
        display_name = loc.user.get_full_name() or loc.user.username
        loc_data.append({
            "name": display_name,
            "sfc_id": sfc_id,
            "lat": float(loc.latitude),
            "lon": float(loc.longitude),
            "accuracy": loc.accuracy,
            "timestamp": loc.timestamp.astimezone(
                timezone.get_current_timezone()
            ).strftime("%d %b %Y, %I:%M %p"),
            "timestamp_iso": loc.timestamp.isoformat(),
            "is_tracking": loc.is_tracking,
            "maps_url": loc.google_maps_url,
        })
    return JsonResponse({"locations": loc_data})


# =============================================================================
# Role-based Access Control Decorators
# =============================================================================

def customer_required(view_func):
    """Only allow authenticated users with customer role."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('portal_login')
        try:
            profile = request.user.profile
        except UserProfile.DoesNotExist:
            return redirect('portal_login')
        if profile.role != UserProfile.ROLE_CUSTOMER or profile.status != UserProfile.STATUS_ACTIVE:
            messages.error(request, 'Access denied.')
            return redirect('portal_login')
        return view_func(request, *args, **kwargs)
    return wrapper


def manager_required(view_func):
    """Only allow authenticated managers or is_staff users. Staff can view, manager has full access."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('portal_login')
        if _is_manage_user(request.user):
            return view_func(request, *args, **kwargs)
        messages.error(request, 'Access denied.')
        return redirect('portal_login')
    return wrapper


def only_manager_required(view_func):
    """Only allow authenticated managers (not staff) or is_superuser."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('portal_login')
        if request.user.is_superuser:
            return view_func(request, *args, **kwargs)
        try:
            profile = request.user.profile
            if profile.role == UserProfile.ROLE_MANAGER and profile.status == UserProfile.STATUS_ACTIVE:
                return view_func(request, *args, **kwargs)
        except UserProfile.DoesNotExist:
            pass
        messages.error(request, 'Access denied. Manager only.')
        return redirect('portal_login')
    return wrapper


# =============================================================================
# Portal Auth Views
# =============================================================================

def register_customer(request):
    """Customer self-registration. Creates User + UserProfile with SFC-CUS-XXXXX."""
    if request.user.is_authenticated:
        return redirect('portal_dashboard')

    if request.method == 'POST':
        form = CustomerRegistrationForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                user = form.save()
                profile, created = UserProfile.objects.get_or_create(
                    user=user,
                    defaults={
                        'role': UserProfile.ROLE_CUSTOMER,
                        'status': UserProfile.STATUS_ACTIVE,
                    },
                )
                if not profile.sfc_id:
                    profile.role = UserProfile.ROLE_CUSTOMER
                    profile.status = UserProfile.STATUS_ACTIVE
                    profile.save()
                    UserProfile.assign_sfc_id(profile)
            login(request, user)
            messages.success(request, f'Welcome, {user.first_name or user.username}! Your account has been created.')
            return redirect('portal_dashboard')
    else:
        form = CustomerRegistrationForm()
    return render(request, 'portal/register.html', {'form': form})


def register_staff_request(request):
    """Staff registration request (pending manager approval)."""
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        form = StaffRegistrationRequestForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                user = form.save()
                profile, created = UserProfile.objects.get_or_create(
                    user=user,
                    defaults={
                        'role': UserProfile.ROLE_STAFF,
                        'status': UserProfile.STATUS_PENDING,
                    },
                )
                profile.role = UserProfile.ROLE_STAFF
                profile.status = UserProfile.STATUS_PENDING
                profile.save()
            messages.info(request, 'Request submitted, await approval')
            return redirect('portal_login')
    else:
        form = StaffRegistrationRequestForm()
    return render(request, 'portal/register_staff.html', {'form': form})


def portal_login(request):
    """Login by SFC ID or username."""
    if request.user.is_authenticated:
        try:
            profile = request.user.profile
            if profile.role == UserProfile.ROLE_CUSTOMER:
                return redirect('portal_dashboard')
            return redirect('dashboard')
        except UserProfile.DoesNotExist:
            return redirect('dashboard' if request.user.is_staff else 'portal_dashboard')

    if request.method == 'POST':
        form = PortalLoginForm(request.POST)
        if form.is_valid():
            login_id = form.cleaned_data.get('login_id') or form.cleaned_data.get('username') or ''
            login_id = str(login_id).strip()
            password = form.cleaned_data.get('password', '')

            # Authenticate: if login_id starts with 'SFC-', look up UserProfile.sfc_id -> get username
            username = login_id
            if login_id.upper().startswith('SFC-'):
                profile_obj = UserProfile.objects.filter(sfc_id__iexact=login_id).select_related('user').first()
                if profile_obj:
                    username = profile_obj.user.username
                else:
                    username = None

            user = authenticate(request, username=username, password=password) if username else None
            if user is not None:
                # Check UserProfile.status == 'active' (reject pending/inactive)
                try:
                    profile = user.profile
                except UserProfile.DoesNotExist:
                    profile = None

                if profile and profile.status != UserProfile.STATUS_ACTIVE:
                    if profile.status == UserProfile.STATUS_PENDING:
                        messages.warning(request, 'Your account is pending manager approval.')
                    else:
                        messages.error(request, 'Your account is inactive. Please contact support.')
                    return render(request, 'portal/login.html', {'form': form})

                login(request, user)
                messages.success(request, f'Welcome back, {user.get_full_name() or user.username}!')

                nxt = request.GET.get('next') or request.POST.get('next')
                if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
                    return redirect(nxt)

                # Redirect based on role:
                #   - customer -> portal_dashboard
                #   - staff/manager -> dashboard (existing manage page)
                if profile and profile.role == UserProfile.ROLE_CUSTOMER:
                    return redirect('portal_dashboard')
                return redirect('dashboard')
            else:
                messages.error(request, 'Invalid SFC ID / username or password.')
    else:
        form = PortalLoginForm()
    return render(request, 'portal/login.html', {'form': form})


@require_POST
def portal_logout(request):
    """POST-only logout."""
    logout(request)
    messages.info(request, 'You have been logged out.')
    return redirect('home')


# =============================================================================
# Customer Portal Views
# =============================================================================

@customer_required
def portal_dashboard(request):
    """Customer home page showing their applications and payment summary."""
    apps = LoanApplication.objects.filter(applicant=request.user).order_by('-created')[:10]
    claims = PaymentClaim.objects.filter(claimed_by=request.user).order_by('-created')[:10]

    customer = None
    try:
        if request.user.profile.phone:
            customer = Customer.objects.filter(phone=request.user.profile.phone).first()
    except Exception:
        pass
    if not customer:
        linked_app = LoanApplication.objects.filter(applicant=request.user, linked_customer__isnull=False).first()
        if linked_app:
            customer = linked_app.linked_customer

    loans = customer.loans.prefetch_related('installments').all() if customer else []
    total_borrowed = sum((l.principal for l in loans), Decimal('0'))
    total_payable = sum((l.total_payable for l in loans), Decimal('0'))
    total_paid = sum((l.paid for l in loans), Decimal('0'))
    total_balance = sum((l.balance for l in loans), Decimal('0'))

    ctx = {
        'apps': apps,
        'applications': apps,
        'claims': claims,
        'customer': customer,
        'loans': loans,
        'total_borrowed': total_borrowed,
        'total_payable': total_payable,
        'total_paid': total_paid,
        'total_balance': total_balance,
    }
    return render(request, 'portal/dashboard.html', ctx)


@customer_required
def apply_loan(request):
    """Multi-part loan application form."""
    if request.method == 'POST':
        form = LoanApplicationForm(request.POST, request.FILES)
        if form.is_valid():
            with transaction.atomic():
                app = form.save(commit=False)
                app.applicant = request.user
                app.status = LoanApplication.STATUS_SUBMITTED
                app.submitted_at = timezone.now()
                app.save()
                if hasattr(form, 'save_m2m'):
                    form.save_m2m()
                ApplicationStatusHistory.objects.create(
                    application=app,
                    from_status='',
                    to_status=LoanApplication.STATUS_SUBMITTED,
                    changed_by=request.user,
                    note='Application submitted by applicant.',
                )
            messages.success(request, f'Application {app.ref_no} submitted successfully!')
            return redirect('application_detail', pk=app.pk)
    else:
        initial = {}
        if request.user.get_full_name():
            initial['full_name'] = request.user.get_full_name()
        if request.user.email:
            initial['email'] = request.user.email
        try:
            if request.user.profile.phone:
                initial['mobile'] = request.user.profile.phone
        except Exception:
            pass
        form = LoanApplicationForm(initial=initial) if initial else LoanApplicationForm()

    return render(request, 'portal/apply.html', {'form': form})


@customer_required
def my_applications(request):
    """List all applications for current customer."""
    apps = LoanApplication.objects.filter(applicant=request.user).order_by('-created')
    return render(request, 'portal/my_applications.html', {'apps': apps, 'applications': apps})


@customer_required
def application_detail(request, pk):
    """View single application status and history."""
    app = get_object_or_404(LoanApplication, pk=pk, applicant=request.user)
    history = app.history.select_related('changed_by').all()
    documents = app.documents.all()

    allowed_statuses = (
        LoanApplication.STATUS_DRAFT,
        LoanApplication.STATUS_SUBMITTED,
        LoanApplication.STATUS_MORE_INFO,
    )
    can_upload = app.status in allowed_statuses

    doc_form = DocumentUploadForm()
    if request.method == 'POST' and can_upload:
        doc_form = DocumentUploadForm(request.POST, request.FILES)
        if doc_form.is_valid():
            with transaction.atomic():
                doc = doc_form.save(commit=False)
                doc.application = app
                uploaded_file = request.FILES.get('file')
                if uploaded_file:
                    if not doc.original_name:
                        doc.original_name = uploaded_file.name
                    if hasattr(uploaded_file, 'content_type') and not doc.mime_type:
                        doc.mime_type = uploaded_file.content_type
                doc.save()
            messages.success(request, 'Document uploaded successfully.')
            return redirect('application_detail', pk=app.pk)
        else:
            messages.error(request, 'Failed to upload document. Please check the file requirements.')

    return render(request, 'portal/application_detail.html', {
        'app': app,
        'application': app,
        'history': history,
        'documents': documents,
        'doc_form': doc_form,
        'can_upload': can_upload,
    })


@login_required
def serve_document(request, pk):
    """Serve a private uploaded document. Only owner or staff can access."""
    doc = get_object_or_404(ApplicationDocument.objects.select_related('application__applicant'), pk=pk)

    # Check: request.user == doc.application.applicant OR request.user.is_staff
    is_owner = (request.user == doc.application.applicant)
    is_staff = request.user.is_staff or request.user.is_superuser
    if not (is_owner or is_staff):
        try:
            profile = request.user.profile
            if profile.role in (UserProfile.ROLE_STAFF, UserProfile.ROLE_MANAGER) and profile.status == UserProfile.STATUS_ACTIVE:
                is_staff = True
        except UserProfile.DoesNotExist:
            pass

    if not (is_owner or is_staff):
        raise PermissionDenied('You do not have permission to view this document.')

    if not doc.file:
        raise Http404('Document file not found.')

    try:
        content_type = doc.mime_type or 'application/octet-stream'
        filename = doc.original_name or os.path.basename(doc.file.name)
        response = FileResponse(doc.file.open('rb'), content_type=content_type)
        response['Content-Disposition'] = f'inline; filename="{filename}"'
        return response
    except (FileNotFoundError, OSError):
        raise Http404('Document file missing on disk.')


# =============================================================================
# Manager Application Review Views
# =============================================================================

@manager_required
def manage_applications(request):
    """List loan applications. Managers see all; staff see assigned + unassigned."""
    status_filter = request.GET.get('status', '').strip()
    q = request.GET.get('q', '').strip()

    # Determine if current user is a manager or just staff
    is_mgr = False
    if request.user.is_superuser:
        is_mgr = True
    else:
        try:
            is_mgr = request.user.profile.role == UserProfile.ROLE_MANAGER
        except Exception:
            is_mgr = request.user.is_staff  # fallback for Django admin users without profile

    apps = LoanApplication.objects.select_related('applicant', 'assigned_to').all()

    # Staff see only applications assigned to them OR unassigned (inbox model)
    if not is_mgr:
        apps = apps.filter(
            Q(assigned_to=request.user) | Q(assigned_to__isnull=True)
        )

    if status_filter:
        apps = apps.filter(status=status_filter)
    if q:
        apps = apps.filter(
            Q(ref_no__icontains=q) |
            Q(full_name__icontains=q) |
            Q(mobile__icontains=q)
        )

    unassigned_count = LoanApplication.objects.filter(assigned_to__isnull=True).exclude(
        status__in=[LoanApplication.STATUS_APPROVED, LoanApplication.STATUS_REJECTED, LoanApplication.STATUS_WITHDRAWN]
    ).count()

    return render(request, 'manage/applications.html', {
        'apps': apps,
        'applications': apps,
        'status_filter': status_filter,
        'q': q,
        'status_choices': LoanApplication.STATUS_CHOICES,
        'is_manager': is_mgr,
        'unassigned_count': unassigned_count,
    })


@manager_required
def review_application(request, pk):
    """View application detail with approve/reject/request-info actions."""
    app = get_object_or_404(LoanApplication.objects.select_related('applicant'), pk=pk)
    documents = app.documents.all()
    history = app.history.select_related('changed_by').all()

    # Determine role of current user
    is_mgr = False
    if request.user.is_superuser:
        is_mgr = True
    else:
        try:
            is_mgr = request.user.profile.role == UserProfile.ROLE_MANAGER
        except Exception:
            is_mgr = request.user.is_staff

    # Staff can only access apps assigned to them or unassigned
    if not is_mgr and app.assigned_to and app.assigned_to != request.user:
        messages.error(request, 'This application is not assigned to you.')
        return redirect('manage_applications')

    if request.method == 'POST':
        action = request.POST.get('action')
        note = request.POST.get('note', '').strip()
        old_status = app.status

        # Staff actions: claim (assign to self) or update status to under_review / request_info
        staff_actions = ('claim', 'under_review', 'request_info')
        # Manager-only actions
        manager_actions = ('approve', 'reject', 'assign')

        if action in manager_actions and not is_mgr:
            messages.error(request, 'Only managers can perform this action.')
            return redirect('review_application', pk=pk)

        if action in (*staff_actions, *manager_actions):
            with transaction.atomic():
                if action == 'claim':
                    app.assigned_to = request.user
                    app.status = LoanApplication.STATUS_UNDER_REVIEW
                elif action == 'approve':
                    app.status = LoanApplication.STATUS_APPROVED
                    app.approved_by = request.user
                    app.approved_at = timezone.now()
                elif action == 'reject':
                    app.status = LoanApplication.STATUS_REJECTED
                    app.rejected_by = request.user
                    app.rejected_at = timezone.now()
                    app.rejection_reason = note
                elif action == 'request_info':
                    app.status = LoanApplication.STATUS_MORE_INFO
                    app.review_notes = note
                elif action == 'under_review':
                    app.status = LoanApplication.STATUS_UNDER_REVIEW
                    if not app.assigned_to:
                        app.assigned_to = request.user
                elif action == 'assign':
                    # Manager assigns to a specific user
                    assign_to_id = request.POST.get('assign_to_id')
                    if assign_to_id:
                        from django.contrib.auth import get_user_model
                        User = get_user_model()
                        try:
                            assignee = User.objects.get(pk=assign_to_id)
                            app.assigned_to = assignee
                            app.status = LoanApplication.STATUS_UNDER_REVIEW
                        except User.DoesNotExist:
                            messages.error(request, 'Assignee not found.')
                            return redirect('review_application', pk=pk)

                app.save()
                ApplicationStatusHistory.objects.create(
                    application=app,
                    from_status=old_status,
                    to_status=app.status,
                    changed_by=request.user,
                    note=note,
                )
            messages.success(request, f'Application {app.ref_no} updated to {app.get_status_display()}')
            return redirect('manage_applications')
        else:
            messages.error(request, 'Unknown action requested.')

    # Build list of active staff for manager to assign to
    staff_list = []
    if is_mgr:
        from django.contrib.auth import get_user_model as _gum
        User = _gum()
        staff_profiles = UserProfile.objects.filter(
            role__in=[UserProfile.ROLE_STAFF, UserProfile.ROLE_MANAGER],
            status=UserProfile.STATUS_ACTIVE,
        ).select_related('user')
        staff_list = [(p.user.pk, p.user.get_full_name() or p.user.username, p.sfc_id) for p in staff_profiles]

    return render(request, 'manage/review_application.html', {
        'app': app,
        'application': app,
        'documents': documents,
        'history': history,
        'is_manager': is_mgr,
        'staff_list': staff_list,
    })


@manager_required
def manage_staff_requests(request):
    """View pending staff registration requests and approve/reject."""
    pending = UserProfile.objects.filter(role=UserProfile.ROLE_STAFF, status=UserProfile.STATUS_PENDING).select_related('user')

    if request.method == 'POST':
        profile_id = request.POST.get('profile_id')
        action = request.POST.get('action')
        profile = get_object_or_404(UserProfile.objects.select_related('user'), pk=profile_id)

        if action == 'approve':
            with transaction.atomic():
                profile.status = UserProfile.STATUS_ACTIVE
                profile.approved_by = request.user
                profile.approved_at = timezone.now()
                profile.save()
                profile.user.is_active = True
                # Set is_staff so the user can access the /manage/ panel
                profile.user.is_staff = True
                profile.user.save()
                if not profile.sfc_id:
                    UserProfile.assign_sfc_id(profile)
            messages.success(request, f'{profile.user.get_full_name() or profile.user.username} approved as {profile.sfc_id}')
        elif action == 'reject':
            profile.status = UserProfile.STATUS_INACTIVE
            profile.save()
            messages.info(request, f'{profile.user.get_full_name() or profile.user.username} rejected.')
        return redirect('manage_staff_requests')

    return render(request, 'manage/staff_requests.html', {
        'pending': pending,
        'pending_requests': pending,
    })


@manager_required
def manage_payment_claims(request):
    """View and verify/reject payment claims."""
    status_filter = request.GET.get('status', 'pending').strip()
    claims = PaymentClaim.objects.select_related('installment__loan__customer', 'claimed_by').all()
    if status_filter and status_filter != 'all':
        claims = claims.filter(status=status_filter)

    if request.method == 'POST':
        claim_id = request.POST.get('claim_id')
        action = request.POST.get('action')
        claim = get_object_or_404(PaymentClaim.objects.select_related('installment__loan__customer'), pk=claim_id)

        if action == 'verify' and claim.status == PaymentClaim.STATUS_PENDING:
            with transaction.atomic():
                # Create actual Payment record
                payment = claim.installment.record_payment(
                    claim.amount_claimed,
                    method=claim.method,
                    note=f'Claim #{claim.pk} verified',
                    confirmed_by=request.user,
                )
                claim.status = PaymentClaim.STATUS_VERIFIED
                claim.verified_by = request.user
                claim.verified_at = timezone.now()
                claim.linked_payment = payment
                claim.save()
            messages.success(request, f'Claim #{claim.pk} verified and payment recorded.')
        elif action == 'reject':
            with transaction.atomic():
                claim.status = PaymentClaim.STATUS_REJECTED
                claim.rejection_reason = request.POST.get('reason', '').strip()
                claim.verified_by = request.user
                claim.verified_at = timezone.now()
                claim.save()
            messages.info(request, f'Claim #{claim.pk} rejected.')
        return redirect('manage_payment_claims')

    return render(request, 'manage/payment_claims.html', {
        'claims': claims,
        'status_filter': status_filter,
        'status_choices': PaymentClaim.STATUS_CHOICES,
    })


# =============================================================================
# Public Page Views
# =============================================================================

def services(request):
    return render(request, 'public/services.html')


def privacy(request):
    return render(request, 'public/privacy.html')


def terms(request):
    return render(request, 'public/terms.html')


def disclaimer(request):
    return render(request, 'public/disclaimer.html')


def faq(request):
    return render(request, 'public/faq.html', {'faqs': FAQ.objects.all()})
