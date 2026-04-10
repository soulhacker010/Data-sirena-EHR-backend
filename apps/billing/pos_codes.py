"""
Place of Service (POS) codes for CMS billing.
BUILD 6.3: POS codes dropdown for appointment scheduling.
"""

# Most common POS codes for healthcare services
POS_CODES = [
    ('11', 'Office - Location where patient receives face-to-face services'),
    ('12', 'Home - Patient\'s home or residence'),
    ('02', 'Telehealth - Services via telecommunication systems'),
    ('03', 'School - Services provided in educational institution'),
    ('04', 'Homeless Shelter'),
    ('15', 'Mobile Unit - Equipped vehicle for medical services'),
    ('20', 'Urgent Care Facility'),
    ('21', 'Inpatient Hospital'),
    ('22', 'On Campus-Outpatient Hospital'),
    ('23', 'Emergency Room - Hospital'),
    ('31', 'Skilled Nursing Facility'),
    ('32', 'Nursing Facility'),
    ('33', 'Custodial Care Facility'),
    ('49', 'Independent Clinic'),
    ('50', 'Federally Qualified Health Center'),
    ('51', 'Inpatient Psychiatric Facility'),
    ('52', 'Psychiatric Facility-Partial Hospitalization'),
    ('53', 'Community Mental Health Center'),
    ('71', 'State or Local Public Health Clinic'),
    ('72', 'Rural Health Clinic'),
    ('99', 'Other Place of Service'),
]

# Common defaults based on service type
DEFAULT_POS_BY_SERVICE = {
    'office_visit': '11',
    'telehealth': '02',
    'home_visit': '12',
    'school_based': '03',
}

def get_pos_choices():
    """Return POS codes formatted for dropdown choices."""
    return [(code, f"{code} - {desc}") for code, desc in POS_CODES]

def get_default_pos(is_telehealth=False, location_type=None):
    """Get default POS code based on service delivery method."""
    if is_telehealth:
        return '02'
    elif location_type == 'home':
        return '12'
    elif location_type == 'school':
        return '03'
    else:
        return '11'  # Office default
