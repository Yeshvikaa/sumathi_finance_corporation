from django.contrib.auth import views as auth
from django.urls import path
from . import views as v

urlpatterns = [
    path("", v.home, name="home"),
    path("about/", v.about, name="about"),
    path("gallery/", v.gallery, name="gallery"),
    path("contact/", v.contact, name="contact"),
    path("robots.txt", v.robots),
    path("manage/login/", auth.LoginView.as_view(template_name="manage/login.html"), name="login"),
    path("manage/logout/", auth.LogoutView.as_view(), name="logout"),
    path("manage/", v.dashboard, name="dashboard"),
    path("manage/customers/", v.customers, name="customers"),
    path("manage/customers/new/", v.customer_new, name="customer_new"),
    path("manage/customers/<int:pk>/", v.customer_detail, name="customer_detail"),
    path("manage/customers/<int:pk>/loan/new/", v.loan_new, name="loan_new"),
    path("manage/installments/", v.installments, name="installments"),
    path("manage/installments/<int:pk>/pay/", v.pay, name="pay"),
    path("manage/receipt/<int:pk>/", v.receipt, name="receipt"),
    path("manage/reminders/", v.reminders, name="reminders"),
    path("manage/enquiries/", v.enquiries, name="enquiries"),
    # location tracking
    path("manage/location/submit/", v.submit_location, name="submit_location"),
    path("manage/location/map/", v.location_map, name="location_map"),
    path("manage/location/data.json", v.location_data, name="location_data"),

    # Portal auth
    path("portal/register/", v.register_customer, name="register_customer"),
    path("portal/register/staff/", v.register_staff_request, name="register_staff_request"),
    path("portal/login/", v.portal_login, name="portal_login"),
    path("portal/logout/", v.portal_logout, name="portal_logout"),

    # Customer portal
    path("portal/", v.portal_dashboard, name="portal_dashboard"),
    path("portal/apply/", v.apply_loan, name="apply_loan"),
    path("portal/applications/", v.my_applications, name="my_applications"),
    path("portal/applications/<int:pk>/", v.application_detail, name="application_detail"),
    path("portal/documents/<int:pk>/", v.serve_document, name="serve_document"),

    # Manager/staff extensions
    path("manage/applications/", v.manage_applications, name="manage_applications"),
    path("manage/applications/<int:pk>/", v.review_application, name="review_application"),
    path("manage/staff-requests/", v.manage_staff_requests, name="manage_staff_requests"),
    path("manage/payment-claims/", v.manage_payment_claims, name="manage_payment_claims"),

    # Public pages
    path("services/", v.services, name="services"),
    path("privacy/", v.privacy, name="privacy"),
    path("terms/", v.terms, name="terms"),
    path("disclaimer/", v.disclaimer, name="disclaimer"),
    path("faq/", v.faq, name="faq"),
]
