"""
Management command to seed the database with a demo organization and admin user.

Usage:
    python manage.py seed_demo

Creates:
    - Organization: "Sirena Health ABA"
    - Admin user: admin@sirenahealth.com / admin123
"""
from django.core.management.base import BaseCommand
from apps.accounts.models import Organization, User


class Command(BaseCommand):
    help = 'Create a demo organization and admin user for local development'

    def handle(self, *args, **options):
        # 1. Create or get organization
        org, org_created = Organization.objects.get_or_create(
            name='Sirena Health ABA',
            defaults={
                'contact_email': 'admin@sirenahealth.com',
                'contact_phone': '(555) 000-0000',
                'address': '123 Therapy Lane, Los Angeles, CA 90001',
            }
        )
        if org_created:
            self.stdout.write(self.style.SUCCESS('[OK] Created organization: %s' % org.name))
        else:
            self.stdout.write('[..] Organization already exists: %s' % org.name)

        # 2. Create or update admin user
        email = 'admin@sirenahealth.com'
        password = 'admin123'

        user, user_created = User.objects.get_or_create(
            email=email,
            defaults={
                'first_name': 'Admin',
                'last_name': 'User',
                'role': 'admin',
                'organization': org,
                'is_staff': True,
                'is_superuser': True,
                'is_active': True,
            }
        )

        if user_created:
            user.set_password(password)
            user.save()
            self.stdout.write(self.style.SUCCESS('[OK] Created admin user: %s' % email))
        else:
            # Make sure existing user has the org
            if user.organization != org:
                user.organization = org
                user.save()
                self.stdout.write(self.style.SUCCESS('[OK] Linked existing user to organization'))
            else:
                self.stdout.write('[..] Admin user already exists: %s' % email)

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=' * 45))
        self.stdout.write(self.style.SUCCESS('  Login credentials:'))
        self.stdout.write(self.style.SUCCESS('  Email:    %s' % email))
        self.stdout.write(self.style.SUCCESS('  Password: %s' % password))
        self.stdout.write(self.style.SUCCESS('=' * 45))
        self.stdout.write('')
