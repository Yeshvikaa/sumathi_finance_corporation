# Sumathi Finance Corporation – website + management app

## Run (first time)
    python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
    pip install -r requirements.txt
    python manage.py makemigrations core
    python manage.py migrate
    python manage.py seed_site
    python manage.py createsuperuser                       # this user logs in at /manage/login/ and /admin/
    python manage.py runserver

## URLs
Public: /  /about/  /gallery/  /contact/  /sitemap.xml  /robots.txt
Staff app: /manage/  (dashboard, customers, payments, reminders, enquiries)
Site content editor (hours, phone, map, policies, gallery photos): /admin/

## Daily SMS reminders (cron, 9 AM)
    export FAST2SMS_API_KEY=xxxx
    0 9 * * * cd /path/sumathi && venv/bin/python manage.py send_reminders --days 1
Test first with: python manage.py send_reminders --dry-run
