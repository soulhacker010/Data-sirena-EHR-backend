# Sirena Health EHR Platform - Complete Backend Build Plan

## 📋 Table of Contents
1. [Project Overview](#1-project-overview)
2. [Tech Stack](#2-tech-stack)
3. [Database Schema](#3-database-schema)
4. [API Documentation (Coordinated with Frontend)](#4-api-documentation)
5. [Security Implementation](#5-security-implementation)
6. [Third-Party Integrations](#6-third-party-integrations)
7. [Environment Configuration](#7-environment-configuration)
8. [Frontend-Backend Coordination Map](#8-frontend-backend-coordination-map)

---

## 1. Project Overview

### What We're Building
A full-featured Electronic Health Record (EHR) platform for behavioral health practices (ABA therapy clinics). The system allows:
- Multi-tenant organization management with NPI numbers
- Client management with insurance and diagnosis tracking
- Appointment scheduling with recurring appointments and authorization tracking
- Clinical documentation (session notes) with signature and co-sign workflow
- Billing: invoices, payments (Stripe), insurance claims
- Reports: session summaries, billing summaries, authorization usage, missing notes
- Admin: user management, role-based access, audit logging
- Real-time notifications

### User Roles
| Role | Can Do | Frontend Pages |
|------|--------|----------------|
| **Admin** | Everything + user management + audit log | All 19 pages |
| **Supervisor** | Clinical + co-sign notes + view reports | Dashboard, Calendar, Clients, Notes, Reports |
| **Clinician** | Own clients, own notes, own calendar | Dashboard, Calendar, Clients, Notes |
| **Biller** | Billing, invoices, claims, payments | Dashboard, Billing, Reports |
| **Front Desk** | Scheduling, client intake | Dashboard, Calendar, Clients |

### Core Features (MVP)
- ✅ JWT authentication with role-based access
- ✅ Client management (CRUD + insurance + diagnosis)
- ✅ Appointment scheduling (single + recurring + authorization linking)
- ✅ Session notes (SOAP format, sign, co-sign, lock)
- ✅ Billing (invoices, payments, claims, batch generation)
- ✅ Reports (session summary, billing summary, authorization report, missing notes)
- ✅ Admin (user CRUD, audit log)
- ✅ Notifications
- ✅ Multi-tenancy (organization-scoped data isolation)
- ✅ HIPAA-compliant audit logging

---

## 2. Tech Stack

### Backend
| Technology | Purpose |
|------------|---------|
| Python 3.11+ | Language |
| **Django 5.0+** | Web Framework |
| **Django REST Framework** | REST API |
| PostgreSQL 15+ | Database |
| **Django ORM** | Database Layer (built-in) |
| **Django Migrations** | Schema Migrations (built-in) |
| **Django Admin** | Admin Panel (built-in) |
| SimpleJWT | JWT Token Authentication |
| Pydantic / DRF Serializers | Request/Response Validation |
| Stripe SDK | Payment Processing |
| SendGrid / Resend | Email Notifications |
| Celery + Redis | Background Tasks (claim processing, reminders) |
| gunicorn | WSGI Production Server |

### Frontend (Already Built)
| Technology | Purpose |
|------------|---------|
| React 18 + TypeScript | UI Framework |
| Vite | Build Tool |
| React Router v6 | Routing (22 routes) |
| Phosphor Icons | Icon Library |
| react-hot-toast | Toast Notifications |
| Zod | Form Validation |

### Deployment
| Component | Platform |
|-----------|----------|
| Frontend | Vercel |
| Backend + DB | Render.com or Railway |
| File Storage | AWS S3 / Cloudinary |
| Email | SendGrid |
| Payments | Stripe |

---

## 3. Database Schema

### Organizations & Multi-Tenancy

```sql
CREATE TABLE organizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    tax_id VARCHAR(50),
    contact_email VARCHAR(255),
    contact_phone VARCHAR(50),
    address TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE npis (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    npi_number VARCHAR(10) NOT NULL UNIQUE,
    business_name VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE locations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    address TEXT NOT NULL,
    city VARCHAR(100),
    state VARCHAR(2),
    zip_code VARCHAR(10),
    is_telehealth BOOLEAN DEFAULT false,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW()
);
```

### Users & Authentication

```sql
CREATE TYPE user_role AS ENUM ('admin', 'clinician', 'supervisor', 'biller', 'front_desk');

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    role user_role NOT NULL,
    licenses TEXT[],
    credentials VARCHAR(255),
    is_active BOOLEAN DEFAULT true,
    last_login TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_users_org ON users(organization_id);
CREATE INDEX idx_users_email ON users(email);
```

### Clients

```sql
CREATE TABLE clients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    date_of_birth DATE NOT NULL,
    gender VARCHAR(50),
    address TEXT,
    city VARCHAR(100),
    state VARCHAR(2),
    zip_code VARCHAR(10),
    phone VARCHAR(50),
    email VARCHAR(255),
    emergency_contact_name VARCHAR(255),
    emergency_contact_phone VARCHAR(50),
    insurance_primary_name VARCHAR(255),
    insurance_primary_id VARCHAR(100),
    insurance_primary_group VARCHAR(100),
    insurance_secondary_name VARCHAR(255),
    insurance_secondary_id VARCHAR(100),
    diagnosis_codes TEXT[],
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_clients_org ON clients(organization_id);
CREATE INDEX idx_clients_name ON clients(last_name, first_name);
```

### Authorizations

```sql
CREATE TABLE authorizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    insurance_name VARCHAR(255) NOT NULL,
    authorization_number VARCHAR(100),
    service_code VARCHAR(50),
    units_approved INTEGER NOT NULL,
    units_used INTEGER DEFAULT 0,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_auth_client ON authorizations(client_id);
CREATE INDEX idx_auth_dates ON authorizations(start_date, end_date);
```

### Scheduling

```sql
CREATE TYPE appointment_status AS ENUM ('scheduled', 'attended', 'cancelled', 'no_show');

CREATE TABLE appointments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    provider_id UUID NOT NULL REFERENCES users(id),
    location_id UUID REFERENCES locations(id),
    authorization_id UUID REFERENCES authorizations(id),
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP NOT NULL,
    service_code VARCHAR(50),
    units DECIMAL(5,2),
    status appointment_status DEFAULT 'scheduled',
    notes TEXT,
    is_recurring BOOLEAN DEFAULT false,
    recurrence_pattern JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_appt_org ON appointments(organization_id);
CREATE INDEX idx_appt_client ON appointments(client_id);
CREATE INDEX idx_appt_provider ON appointments(provider_id);
CREATE INDEX idx_appt_time ON appointments(start_time, end_time);
```

### Clinical Records

```sql
CREATE TABLE note_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    template_type VARCHAR(100),
    fields JSONB NOT NULL,
    required_fields TEXT[],
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TYPE note_status AS ENUM ('draft', 'completed', 'signed', 'co_signed');

CREATE TABLE session_notes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    appointment_id UUID REFERENCES appointments(id),
    client_id UUID NOT NULL REFERENCES clients(id),
    provider_id UUID NOT NULL REFERENCES users(id),
    template_id UUID REFERENCES note_templates(id),
    note_data JSONB NOT NULL,
    status note_status DEFAULT 'draft',
    signature_data TEXT,
    signed_at TIMESTAMP,
    supervisor_signature TEXT,
    co_signed_at TIMESTAMP,
    co_signed_by UUID REFERENCES users(id),
    is_locked BOOLEAN DEFAULT false,
    version INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_notes_client ON session_notes(client_id);
CREATE INDEX idx_notes_provider ON session_notes(provider_id);
CREATE INDEX idx_notes_status ON session_notes(status);

CREATE TABLE treatment_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    provider_id UUID NOT NULL REFERENCES users(id),
    goals JSONB NOT NULL,
    start_date DATE NOT NULL,
    review_date DATE,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    uploaded_by UUID NOT NULL REFERENCES users(id),
    file_name VARCHAR(255) NOT NULL,
    file_type VARCHAR(50) NOT NULL,
    file_size INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    document_type VARCHAR(100),
    is_signed BOOLEAN DEFAULT false,
    signature_data TEXT,
    signed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_docs_client ON documents(client_id);
```

### Billing

```sql
CREATE TABLE invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    client_id UUID NOT NULL REFERENCES clients(id),
    invoice_number VARCHAR(100) UNIQUE NOT NULL,
    invoice_date DATE NOT NULL,
    total_amount DECIMAL(10,2) NOT NULL,
    paid_amount DECIMAL(10,2) DEFAULT 0,
    balance DECIMAL(10,2) NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',
    due_date DATE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE invoice_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    appointment_id UUID REFERENCES appointments(id),
    service_code VARCHAR(50) NOT NULL,
    description TEXT,
    units DECIMAL(5,2) NOT NULL,
    rate DECIMAL(10,2) NOT NULL,
    amount DECIMAL(10,2) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id UUID NOT NULL REFERENCES invoices(id),
    claim_id UUID REFERENCES claims(id),           -- NEW: link payment to specific claim
    client_id UUID NOT NULL REFERENCES clients(id), -- NEW: direct client link for client profile view
    amount DECIMAL(10,2) NOT NULL,
    payment_type VARCHAR(50) DEFAULT 'payment',     -- 'payment', 'write_off', 'adjustment'
    payer_type VARCHAR(50),                          -- 'insurance', 'patient'
    payment_method VARCHAR(50),
    stripe_payment_id VARCHAR(255),
    payment_date TIMESTAMP DEFAULT NOW(),
    reference_number VARCHAR(100),                   -- EOB number, check number, etc.
    notes TEXT
);

CREATE TYPE claim_status AS ENUM ('created', 'submitted', 'accepted', 'paid', 'denied', 'resubmitted');

CREATE TABLE claims (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id UUID NOT NULL REFERENCES invoices(id),
    client_id UUID NOT NULL REFERENCES clients(id), -- NEW: direct client link for client profile view
    claim_number VARCHAR(100),
    payer_name VARCHAR(255) NOT NULL,
    payer_id VARCHAR(100),
    status claim_status DEFAULT 'created',
    billed_amount DECIMAL(10,2) NOT NULL,            -- NEW: total amount billed on claim
    allowed_amount DECIMAL(10,2),                    -- NEW: insurer allowed amount
    insurance_paid DECIMAL(10,2) DEFAULT 0,          -- NEW: amount insurance paid
    patient_responsibility DECIMAL(10,2) DEFAULT 0,  -- NEW: patient copay/coinsurance
    write_off_amount DECIMAL(10,2) DEFAULT 0,        -- NEW: contractual write-off
    submitted_at TIMESTAMP,
    response_data JSONB,
    denial_reason TEXT,
    resubmission_count INTEGER DEFAULT 0,
    paid_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_claims_client ON claims(client_id);

CREATE INDEX idx_invoices_client ON invoices(client_id);
CREATE INDEX idx_invoices_status ON invoices(status);
CREATE INDEX idx_claims_status ON claims(status);
```

### Notifications

```sql
CREATE TABLE notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    type VARCHAR(50) NOT NULL,          -- 'alert', 'reminder', 'system', 'billing'
    priority VARCHAR(20) DEFAULT 'low', -- 'low', 'medium', 'high', 'urgent'
    is_read BOOLEAN DEFAULT false,
    action_url TEXT,                     -- deep link to related page
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_notif_user ON notifications(user_id);
CREATE INDEX idx_notif_read ON notifications(is_read);
```

### Audit Log

```sql
CREATE TABLE audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID REFERENCES organizations(id),
    user_id UUID REFERENCES users(id),
    action VARCHAR(100) NOT NULL,
    table_name VARCHAR(100),
    record_id UUID,
    ip_address INET,
    user_agent TEXT,
    changes JSONB,
    timestamp TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_audit_user ON audit_logs(user_id);
CREATE INDEX idx_audit_time ON audit_logs(timestamp);
CREATE INDEX idx_audit_table ON audit_logs(table_name, record_id);
```

### Database Relationships
```
organizations (1) ──→ (many) users
organizations (1) ──→ (many) npis
organizations (1) ──→ (many) locations
organizations (1) ──→ (many) clients
organizations (1) ──→ (many) appointments
organizations (1) ──→ (many) invoices

users (1) ──→ (many) appointments (as provider)
users (1) ──→ (many) session_notes (as provider)
users (1) ──→ (many) notifications

clients (1) ──→ (many) authorizations
clients (1) ──→ (many) appointments
clients (1) ──→ (many) session_notes
clients (1) ──→ (many) invoices
clients (1) ──→ (many) treatment_plans
clients (1) ──→ (many) documents

appointments (1) ──→ (1) session_note
invoices (1) ──→ (many) invoice_items
invoices (1) ──→ (many) payments
invoices (1) ──→ (many) claims
```

---

## 4. API Documentation (Coordinated with Frontend)

### Base URL
```
Development: http://localhost:8000/api/v1
Production:  https://api.sirenahealthehr.com/api/v1
```

All endpoints require `Authorization: Bearer <token>` header unless marked as public.

---

### 4.1 Authentication — LoginPage (`/login`)

#### POST /api/v1/auth/login/
**Frontend trigger:** LoginPage → "Sign In" button click

**Request Body:**
```json
{
  "email": "sarah@sirenahealthehr.com",
  "password": "SecurePass123!"
}
```

**Response (200 OK):**
```json
{
  "access": "eyJhbGciOiJIUzI1NiIs...",
  "refresh": "eyJhbGciOiJIUzI1NiIs...",
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "email": "sarah@sirenahealthehr.com",
    "first_name": "Sarah",
    "last_name": "Johnson",
    "role": "admin",
    "organization": {
      "id": "org-uuid",
      "name": "Sirena Health"
    }
  }
}
```

**Error (401):**
```json
{ "detail": "Invalid email or password" }
```

**Frontend action on success:** Store tokens → redirect to `/dashboard`

---

#### POST /api/v1/auth/token/refresh/
**Frontend trigger:** Axios interceptor (automatic when access token expires)

**Request Body:**
```json
{ "refresh": "eyJhbGciOiJIUzI1NiIs..." }
```

**Response (200 OK):**
```json
{ "access": "new-access-token..." }
```

---

#### GET /api/v1/auth/me/
**Frontend trigger:** App load → AuthContext checks if user is logged in

**Response (200 OK):**
```json
{
  "id": "user-uuid",
  "email": "sarah@sirenahealthehr.com",
  "first_name": "Sarah",
  "last_name": "Johnson",
  "role": "admin",
  "organization_id": "org-uuid",
  "organization_name": "Sirena Health"
}
```

---

#### PUT /api/v1/auth/password/
**Frontend trigger:** SettingsPage → "Change Password" form

**Request Body:**
```json
{
  "current_password": "OldPass123!",
  "new_password": "NewPass456!",
  "confirm_password": "NewPass456!"
}
```

**Response (200 OK):**
```json
{ "message": "Password updated successfully" }
```

---

### 4.2 Dashboard — DashboardPage (`/dashboard`)

#### GET /api/v1/dashboard/stats/
**Frontend trigger:** DashboardPage loads

**Response (200 OK):**
```json
{
  "total_clients": 128,
  "sessions_this_month": 342,
  "pending_notes": 15,
  "revenue_mtd": 45280.00,
  "upcoming_appointments": [
    {
      "id": "appt-uuid",
      "client_name": "Emma Watson",
      "provider_name": "Dr. Sarah Johnson",
      "start_time": "2026-02-19T10:00:00Z",
      "end_time": "2026-02-19T11:00:00Z",
      "service_code": "97153",
      "status": "scheduled"
    }
  ],
  "recent_activity": [
    {
      "type": "note_signed",
      "description": "Session note signed for Michael B.",
      "user": "Dr. Sarah Johnson",
      "timestamp": "2026-02-18T16:30:00Z"
    }
  ],
  "billing_overview": {
    "invoices_pending": 12,
    "claims_submitted": 8,
    "claims_denied": 2,
    "collections_rate": 94.5
  }
}
```

---

### 4.3 Clients — ClientsPage (`/clients`) & ClientDetailPage (`/clients/:id`)

#### GET /api/v1/clients/
**Frontend trigger:** ClientsPage loads, search input changes, status filter changes

**Query Parameters:**
- `search` (optional): Search by name/email
- `status` (optional): `active` | `inactive` | `all`
- `page` (optional): Page number (default: 1)
- `page_size` (optional): Items per page (default: 25)

**Response (200 OK):**
```json
{
  "count": 128,
  "next": "/api/v1/clients/?page=2",
  "results": [
    {
      "id": "client-uuid",
      "first_name": "Emma",
      "last_name": "Watson",
      "date_of_birth": "2015-03-12",
      "age": 10,
      "phone": "(555) 123-4567",
      "email": "parent@email.com",
      "insurance_primary_name": "Blue Cross",
      "diagnosis_codes": ["F84.0"],
      "is_active": true,
      "next_appointment": "2026-02-19T10:00:00Z",
      "sessions_this_month": 12,
      "created_at": "2025-06-15T10:00:00Z"
    }
  ]
}
```

---

#### POST /api/v1/clients/
**Frontend trigger:** ClientsPage → "Add Client" modal → Submit

**Request Body:**
```json
{
  "first_name": "Emma",
  "last_name": "Watson",
  "date_of_birth": "2015-03-12",
  "gender": "Female",
  "phone": "(555) 123-4567",
  "email": "parent@email.com",
  "address": "123 Oak Street",
  "city": "Springfield",
  "state": "IL",
  "zip_code": "62701",
  "emergency_contact_name": "John Watson",
  "emergency_contact_phone": "(555) 987-6543",
  "insurance_primary_name": "Blue Cross",
  "insurance_primary_id": "BCB123456",
  "insurance_primary_group": "GRP-789",
  "diagnosis_codes": ["F84.0"]
}
```

**Response (201 Created):**
```json
{
  "id": "new-client-uuid",
  "first_name": "Emma",
  "last_name": "Watson",
  ...
}
```

**Validation:**
- `first_name`: Required, 1-100 characters
- `last_name`: Required, 1-100 characters
- `date_of_birth`: Required, valid date, not in future
- `email`: Valid email format if provided

---

#### GET /api/v1/clients/{id}/
**Frontend trigger:** ClientDetailPage loads

**Response (200 OK):**
```json
{
  "id": "client-uuid",
  "first_name": "Emma",
  "last_name": "Watson",
  "date_of_birth": "2015-03-12",
  "age": 10,
  "gender": "Female",
  "phone": "(555) 123-4567",
  "email": "parent@email.com",
  "address": "123 Oak Street",
  "city": "Springfield",
  "state": "IL",
  "zip_code": "62701",
  "emergency_contact_name": "John Watson",
  "emergency_contact_phone": "(555) 987-6543",
  "insurance_primary_name": "Blue Cross",
  "insurance_primary_id": "BCB123456",
  "insurance_primary_group": "GRP-789",
  "insurance_secondary_name": null,
  "diagnosis_codes": ["F84.0"],
  "is_active": true,
  "authorizations": [
    {
      "id": "auth-uuid",
      "insurance_name": "Blue Cross",
      "authorization_number": "AUTH-2026-001",
      "service_code": "97153",
      "units_approved": 120,
      "units_used": 45,
      "start_date": "2026-01-01",
      "end_date": "2026-06-30"
    }
  ],
  "recent_sessions": [
    {
      "id": "note-uuid",
      "date": "2026-02-18",
      "provider_name": "Dr. Sarah Johnson",
      "service_code": "97153",
      "status": "signed"
    }
  ],
  "documents": [
    {
      "id": "doc-uuid",
      "file_name": "intake_form.pdf",
      "document_type": "Intake Form",
      "created_at": "2025-06-15T10:00:00Z"
    }
  ],
  "treatment_plan": {
    "id": "tp-uuid",
    "goals": [...],
    "start_date": "2026-01-01",
    "review_date": "2026-07-01"
  }
}
```

---

#### PUT /api/v1/clients/{id}/
**Frontend trigger:** ClientDetailPage → "Edit" button → EditClientModal → Save

**Request Body:** Same as POST (partial updates allowed)

**Response (200 OK):** Updated client object

---

#### DELETE /api/v1/clients/{id}/
**Frontend trigger:** ClientDetailPage → "Discharge" / "Deactivate" button (ConfirmDialog)

**Response (204 No Content)** (soft delete — sets `is_active = false`)

---

### 4.4 Calendar — CalendarPage (`/calendar`)

#### GET /api/v1/appointments/
**Frontend trigger:** CalendarPage loads, view/date changes

**Query Parameters:**
- `start_date` (required): Start of visible date range
- `end_date` (required): End of visible date range
- `provider_id` (optional): Filter by specific provider
- `client_id` (optional): Filter by specific client
- `status` (optional): `scheduled` | `attended` | `cancelled` | `no_show`

**Response (200 OK):**
```json
[
  {
    "id": "appt-uuid",
    "client": { "id": "client-uuid", "first_name": "Emma", "last_name": "Watson" },
    "provider": { "id": "user-uuid", "first_name": "Sarah", "last_name": "Johnson" },
    "location": { "id": "loc-uuid", "name": "Main Office" },
    "start_time": "2026-02-19T10:00:00Z",
    "end_time": "2026-02-19T11:00:00Z",
    "service_code": "97153",
    "units": 4.00,
    "status": "scheduled",
    "notes": "",
    "is_recurring": false,
    "authorization": {
      "id": "auth-uuid",
      "authorization_number": "AUTH-2026-001",
      "units_remaining": 75
    }
  }
]
```

---

#### POST /api/v1/appointments/
**Frontend trigger:** CalendarPage → "New Appointment" modal → Save

**Request Body:**
```json
{
  "client_id": "client-uuid",
  "provider_id": "user-uuid",
  "location_id": "loc-uuid",
  "authorization_id": "auth-uuid",
  "start_time": "2026-02-20T10:00:00Z",
  "end_time": "2026-02-20T11:00:00Z",
  "service_code": "97153",
  "units": 4.00,
  "notes": "Initial assessment",
  "is_recurring": true,
  "recurrence_pattern": {
    "frequency": "weekly",
    "end_date": "2026-06-30",
    "days_of_week": [1, 3, 5]
  }
}
```

**Response (201 Created):**
```json
{
  "id": "new-appt-uuid",
  "created_count": 1,
  "recurring_count": 56,
  "message": "Appointment created. 56 recurring appointments generated through 2026-06-30."
}
```

---

#### PUT /api/v1/appointments/{id}/
**Frontend trigger:** CalendarPage → click appointment → Edit modal → Save

**Request Body:** Partial update fields

---

#### POST /api/v1/appointments/{id}/status/
**Frontend trigger:** CalendarPage → appointment dropdown → Mark Attended / No-Show / Cancel

**Request Body:**
```json
{
  "status": "attended",
  "notes": "Session completed successfully"
}
```

**Response (200 OK):**
```json
{
  "id": "appt-uuid",
  "status": "attended",
  "authorization_units_remaining": 71
}
```

---

#### DELETE /api/v1/appointments/{id}/
**Frontend trigger:** CalendarPage → appointment → Cancel button

---

### 4.5 Session Notes — SessionNotesPage (`/notes`) & NoteEditorPage (`/notes/:id/edit`)

#### GET /api/v1/notes/
**Frontend trigger:** SessionNotesPage loads, filters change

**Query Parameters:**
- `search` (optional): Search by client name
- `status` (optional): `draft` | `completed` | `signed` | `co_signed`
- `provider_id` (optional): Filter by provider
- `date_from` (optional): Start date
- `date_to` (optional): End date
- `page`, `page_size`

**Response (200 OK):**
```json
{
  "count": 342,
  "results": [
    {
      "id": "note-uuid",
      "client": { "id": "client-uuid", "first_name": "Emma", "last_name": "Watson" },
      "provider": { "id": "user-uuid", "first_name": "Sarah", "last_name": "Johnson" },
      "appointment_id": "appt-uuid",
      "session_date": "2026-02-18",
      "service_code": "97153",
      "status": "signed",
      "is_locked": true,
      "signed_at": "2026-02-18T17:30:00Z",
      "co_signed_by": null,
      "created_at": "2026-02-18T16:00:00Z"
    }
  ]
}
```

---

#### POST /api/v1/notes/
**Frontend trigger:** SessionNotesPage → "New Note" button OR NoteEditorPage → new mode

**Request Body:**
```json
{
  "appointment_id": "appt-uuid",
  "client_id": "client-uuid",
  "template_id": "template-uuid",
  "note_data": {
    "subjective": "Client presented with good mood...",
    "objective": "Participated in 20 trials with 85% accuracy...",
    "assessment": "Progressing well toward goal 1...",
    "plan": "Continue current intervention protocol...",
    "session_type": "Direct 1:1",
    "service_code": "97153",
    "units": 4,
    "duration_minutes": 60,
    "start_time": "10:00",
    "end_time": "11:00",
    "goals_addressed": ["Goal 1", "Goal 2"],
    "interventions_used": ["DTT", "NET", "Prompting"]
  }
}
```

**Response (201 Created):** Full note object with status `draft`

---

#### PUT /api/v1/notes/{id}/
**Frontend trigger:** NoteEditorPage → "Save Draft" button

**Request Body:** Updated `note_data` fields

---

#### POST /api/v1/notes/{id}/sign/
**Frontend trigger:** NoteEditorPage → "Sign & Complete" button

**Request Body:**
```json
{
  "signature_data": "data:image/png;base64,..." 
}
```

**Response (200 OK):**
```json
{
  "id": "note-uuid",
  "status": "signed",
  "signed_at": "2026-02-18T17:30:00Z",
  "is_locked": true
}
```

---

#### POST /api/v1/notes/{id}/cosign/
**Frontend trigger:** NoteEditorPage → "Request Co-Sign" button (supervisor selection)

**Request Body:**
```json
{
  "supervisor_id": "supervisor-uuid",
  "action": "request"
}
```

**Supervisor approves:**
```json
{
  "supervisor_id": "supervisor-uuid",
  "action": "approve",
  "supervisor_signature": "data:image/png;base64,..."
}
```

**Response (200 OK):**
```json
{
  "id": "note-uuid",
  "status": "co_signed",
  "co_signed_by": "supervisor-uuid",
  "co_signed_at": "2026-02-19T09:00:00Z"
}
```

---

### 4.6 Billing — BillingPage (`/billing`) & InvoiceDetailPage (`/billing/invoices/:id`)

#### GET /api/v1/invoices/
**Frontend trigger:** BillingPage → Invoices tab loads, filters change

**Query Parameters:**
- `search`, `status`, `client_id`, `date_from`, `date_to`, `page`, `page_size`

**Response (200 OK):**
```json
{
  "count": 156,
  "results": [
    {
      "id": "inv-uuid",
      "invoice_number": "INV-2026-0042",
      "client": { "id": "client-uuid", "first_name": "Emma", "last_name": "Watson" },
      "invoice_date": "2026-02-15",
      "due_date": "2026-03-15",
      "total_amount": 1200.00,
      "paid_amount": 800.00,
      "balance": 400.00,
      "status": "partial"
    }
  ]
}
```

---

#### POST /api/v1/invoices/
**Frontend trigger:** BillingPage → "Create Invoice" button

**Request Body:**
```json
{
  "client_id": "client-uuid",
  "invoice_date": "2026-02-19",
  "due_date": "2026-03-19",
  "items": [
    {
      "appointment_id": "appt-uuid",
      "service_code": "97153",
      "description": "Direct 1:1 ABA Therapy",
      "units": 4.00,
      "rate": 75.00
    }
  ]
}
```

---

#### POST /api/v1/invoices/batch/
**Frontend trigger:** BillingPage → "Batch Generate" button

**Request Body:**
```json
{
  "date_from": "2026-02-01",
  "date_to": "2026-02-15",
  "client_ids": ["client-uuid-1", "client-uuid-2"]
}
```

**Response (201 Created):**
```json
{
  "invoices_created": 5,
  "total_amount": 8400.00,
  "invoice_ids": ["inv-1", "inv-2", "inv-3", "inv-4", "inv-5"]
}
```

---

#### GET /api/v1/invoices/{id}/
**Frontend trigger:** InvoiceDetailPage loads

**Response (200 OK):**
```json
{
  "id": "inv-uuid",
  "invoice_number": "INV-2026-0042",
  "client": { ... },
  "invoice_date": "2026-02-15",
  "due_date": "2026-03-15",
  "total_amount": 1200.00,
  "paid_amount": 800.00,
  "balance": 400.00,
  "status": "partial",
  "items": [
    {
      "id": "item-uuid",
      "service_code": "97153",
      "description": "Direct 1:1 ABA Therapy",
      "units": 4.00,
      "rate": 75.00,
      "amount": 300.00,
      "appointment_date": "2026-02-10"
    }
  ],
  "payments": [
    {
      "id": "pay-uuid",
      "amount": 800.00,
      "payment_method": "credit_card",
      "payment_date": "2026-02-18T10:00:00Z",
      "notes": "Insurance payment"
    }
  ]
}
```

---

#### POST /api/v1/payments/
**Frontend trigger:** InvoiceDetailPage → "Record Payment" button

**Request Body:**
```json
{
  "invoice_id": "inv-uuid",
  "amount": 400.00,
  "payment_method": "credit_card",
  "notes": "Final payment"
}
```

---

#### POST /api/v1/payments/stripe/
**Frontend trigger:** InvoiceDetailPage → "Pay with Card" (Stripe checkout)

**Request Body:**
```json
{
  "invoice_id": "inv-uuid",
  "amount": 400.00
}
```

**Response (200 OK):**
```json
{
  "client_secret": "pi_xxx_secret_xxx",
  "payment_intent_id": "pi_xxx"
}
```

---

#### GET /api/v1/claims/
**Frontend trigger:** BillingPage → Claims tab loads

**Query Parameters:** `status`, `payer_id`, `date_from`, `date_to`, `page`, `page_size`

---

#### POST /api/v1/claims/
**Frontend trigger:** BillingPage → "Generate Claim" button

**Request Body:**
```json
{
  "invoice_id": "inv-uuid",
  "payer_name": "Blue Cross Blue Shield",
  "payer_id": "BCBS-001"
}
```

---

#### POST /api/v1/claims/{id}/submit/
**Frontend trigger:** BillingPage → Claims tab → "Submit" action on a claim

---

#### GET /api/v1/clients/{id}/claims/
**Frontend trigger:** ClientDetailPage → Billing tab → Claims section loads

**Response (200 OK):**
```json
[
  {
    "id": "claim-uuid",
    "claim_number": "CLM-2026-0015",
    "payer_name": "Blue Cross",
    "status": "paid",
    "billed_amount": 300.00,
    "allowed_amount": 275.00,
    "insurance_paid": 220.00,
    "patient_responsibility": 55.00,
    "write_off_amount": 25.00,
    "submitted_at": "2026-02-10T00:00:00Z",
    "paid_at": "2026-02-18T00:00:00Z",
    "service_code": "97153",
    "session_date": "2026-02-08"
  }
]
```

---

#### POST /api/v1/claims/{id}/post-payment/
**Frontend trigger:** ClientDetailPage → Billing tab → Claims → "Post Payment" button

**Request Body:**
```json
{
  "insurance_paid": 220.00,
  "patient_responsibility": 55.00,
  "write_off_amount": 25.00,
  "reference_number": "EOB-2026-0042",
  "notes": "Contractual adjustment per fee schedule"
}
```

**Response (200 OK):** Updated claim object with payment posted

---

#### POST /api/v1/claims/{id}/write-off/
**Frontend trigger:** ClientDetailPage → Billing tab → Claims → "Write Off" action

**Request Body:**
```json
{
  "amount": 25.00,
  "reason": "Contractual adjustment",
  "notes": "Per Blue Cross fee schedule"
}
```

---


### 4.7 Reports — ReportsPage (`/reports`) & Sub-Report Pages

#### GET /api/v1/reports/session-summary/
**Frontend trigger:** SessionSummaryReportPage loads

**Query Parameters:** `date_from`, `date_to`, `provider_id`

**Response (200 OK):**
```json
{
  "summary": {
    "total_sessions": 342,
    "total_hours": 456.5,
    "total_units": 1826,
    "total_clients": 45,
    "note_completion_rate": 94.2
  },
  "by_provider": [
    {
      "provider_name": "Dr. Sarah Johnson",
      "sessions": 120,
      "hours": 160.0,
      "units": 640,
      "clients": 18,
      "note_completion": 97.5
    }
  ],
  "by_service_code": [
    {
      "code": "97153",
      "name": "Adaptive Behavior Treatment",
      "sessions": 200,
      "hours": 266.7,
      "units": 1067,
      "percentage": 58.4
    }
  ]
}
```

---

#### GET /api/v1/reports/billing-summary/
**Frontend trigger:** BillingSummaryReportPage loads

**Query Parameters:** `date_from`, `date_to`

**Response (200 OK):**
```json
{
  "summary": {
    "total_billed": 125000.00,
    "total_collected": 98500.00,
    "outstanding": 26500.00,
    "collection_rate": 78.8
  },
  "by_payer": [
    {
      "payer": "Blue Cross",
      "billed": 45000.00,
      "collected": 42000.00,
      "rate": 93.3
    }
  ],
  "accounts_receivable_aging": {
    "current": 8500.00,
    "30_days": 6000.00,
    "60_days": 4500.00,
    "90_plus": 7500.00
  }
}
```

---

#### GET /api/v1/reports/authorizations/
**Frontend trigger:** AuthorizationReportPage loads

```json
{
  "authorizations": [
    {
      "id": "auth-uuid",
      "client_name": "Emma Watson",
      "insurance": "Blue Cross",
      "auth_number": "AUTH-2026-001",
      "service_code": "97153",
      "total_units": 120,
      "used_units": 45,
      "remaining_units": 75,
      "start_date": "2026-01-01",
      "end_date": "2026-06-30",
      "status": "active",
      "utilization_percentage": 37.5
    }
  ]
}
```

---

#### GET /api/v1/reports/missing-notes/
**Frontend trigger:** MissingNotesReportPage loads

```json
{
  "missing_notes": [
    {
      "appointment_id": "appt-uuid",
      "client_name": "Michael B.",
      "provider_name": "Dr. Sarah Johnson",
      "session_date": "2026-02-15",
      "service_code": "97153",
      "days_overdue": 4
    }
  ],
  "total_missing": 15
}
```

---

### 4.8 Admin — UsersPage (`/admin/users`) & AuditLogPage (`/admin/audit`)

#### GET /api/v1/users/
**Frontend trigger:** UsersPage loads (admin only)

**Response (200 OK):**
```json
{
  "count": 12,
  "results": [
    {
      "id": "user-uuid",
      "email": "sarah@sirenahealthehr.com",
      "first_name": "Sarah",
      "last_name": "Johnson",
      "role": "admin",
      "credentials": "BCBA, LBA",
      "is_active": true,
      "last_login": "2026-02-19T08:00:00Z"
    }
  ]
}
```

---

#### POST /api/v1/users/
**Frontend trigger:** UsersPage → "Add User" modal

**Request Body:**
```json
{
  "email": "newuser@sirenahealthehr.com",
  "first_name": "John",
  "last_name": "Smith",
  "role": "clinician",
  "credentials": "RBT",
  "password": "TempPass123!"
}
```

---

#### PUT /api/v1/users/{id}/
**Frontend trigger:** UsersPage → Edit user button

---

#### DELETE /api/v1/users/{id}/
**Frontend trigger:** UsersPage → Deactivate user (ConfirmDialog)

---

#### GET /api/v1/admin/audit/
**Frontend trigger:** AuditLogPage loads, date/user filter changes

**Query Parameters:** `user_id`, `action`, `date_from`, `date_to`, `page`, `page_size`

**Response (200 OK):**
```json
{
  "count": 1542,
  "results": [
    {
      "id": "audit-uuid",
      "user": { "id": "user-uuid", "first_name": "Sarah", "last_name": "Johnson" },
      "action": "UPDATE",
      "table_name": "clients",
      "record_id": "client-uuid",
      "changes": { "phone": { "old": "(555) 111-1111", "new": "(555) 222-2222" } },
      "ip_address": "192.168.1.1",
      "timestamp": "2026-02-19T09:15:00Z"
    }
  ]
}
```

---

### 4.9 Settings — SettingsPage (`/settings`)

#### GET /api/v1/settings/organization/
**Frontend trigger:** SettingsPage loads → Organization tab

**Response (200 OK):**
```json
{
  "id": "org-uuid",
  "name": "Sirena Health",
  "tax_id": "12-3456789",
  "contact_email": "admin@sirenahealthehr.com",
  "contact_phone": "(555) 000-0000",
  "address": "456 Health Blvd, Springfield, IL 62701",
  "npis": [
    { "id": "npi-uuid", "npi_number": "1234567890", "business_name": "Sirena Health LLC" }
  ],
  "locations": [
    { "id": "loc-uuid", "name": "Main Office", "address": "456 Health Blvd" }
  ]
}
```

#### PUT /api/v1/settings/organization/
**Frontend trigger:** SettingsPage → Save organization changes

---

### 4.10 Notifications — NotificationsPage (`/notifications`)

#### GET /api/v1/notifications/
**Frontend trigger:** NotificationsPage loads, Navbar bell icon count

**Query Parameters:** `is_read`, `type`, `page`, `page_size`

**Response (200 OK):**
```json
{
  "unread_count": 5,
  "results": [
    {
      "id": "notif-uuid",
      "title": "Co-sign Required",
      "message": "Dr. Smith requested co-signature on note for Emma Watson",
      "type": "alert",
      "priority": "high",
      "is_read": false,
      "action_url": "/notes/note-uuid/edit",
      "created_at": "2026-02-19T08:30:00Z"
    }
  ]
}
```

---

#### PUT /api/v1/notifications/{id}/read/
**Frontend trigger:** NotificationsPage → click notification / "Mark as Read"

#### POST /api/v1/notifications/mark-all-read/
**Frontend trigger:** NotificationsPage → "Mark All as Read" button

---

## 5. Security Implementation

### Password Security
```python
# Django's built-in password hashing (PBKDF2 by default, can use bcrypt)
from django.contrib.auth.hashers import make_password, check_password

# settings.py
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.BCryptSHA256PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
]

AUTH_PASSWORD_VALIDATORS = [
    { 'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
      'OPTIONS': { 'min_length': 8 } },
    { 'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator' },
    { 'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator' },
]
```

### JWT Token Configuration
```python
# settings.py
from datetime import timedelta

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=15),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
}
```

### Role-Based Permissions
```python
# apps/core/permissions.py
from rest_framework.permissions import BasePermission

class IsAdmin(BasePermission):
    def has_permission(self, request, view):
        return request.user.role == 'admin'

class IsClinician(BasePermission):
    def has_permission(self, request, view):
        return request.user.role in ['clinician', 'supervisor', 'admin']

class IsBiller(BasePermission):
    def has_permission(self, request, view):
        return request.user.role in ['biller', 'admin']
```

### Multi-Tenancy (Organization Filtering)
```python
# apps/core/models.py
class OrganizationManager(models.Manager):
    def for_org(self, organization_id):
        return self.get_queryset().filter(organization_id=organization_id)

# Every ViewSet must filter by organization:
class ClientViewSet(viewsets.ModelViewSet):
    def get_queryset(self):
        return Client.objects.for_org(self.request.user.organization_id)
```

### CORS Configuration
```python
# settings.py
CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",     # Vite dev
    "https://sirenahealthehr.com",  # Production
]
CORS_ALLOW_CREDENTIALS = True
```

### Audit Logging Middleware
```python
# apps/audit/middleware.py
class AuditMiddleware:
    def __call__(self, request):
        response = self.get_response(request)
        if request.user.is_authenticated and request.method in ['POST', 'PUT', 'DELETE']:
            AuditLog.objects.create(
                user=request.user,
                organization=request.user.organization,
                action=request.method,
                path=request.path,
                ip_address=get_client_ip(request),
            )
        return response
```

---

## 6. Third-Party Integrations

| Service | Purpose | Frontend Trigger |
|---------|---------|------------------|
| **Stripe** | Credit card payments, HSA/FSA | InvoiceDetailPage → "Pay with Card" |
| **Apex EDI / Office Ally** | Insurance claim submission (HIPAA X12 837P) | BillingPage → Claims → "Submit" |
| **SendGrid / Resend** | Email notifications, appointment reminders | Background task (Celery) |
| **AWS S3 / Cloudinary** | Document uploads, signature images | ClientDetailPage → Upload, NoteEditorPage → Sign |
| **Cloudflare Turnstile** | CAPTCHA on login | LoginPage (bot protection) |

---

## 7. Environment Configuration

```bash
# .env.example

# Django
DJANGO_SECRET_KEY=your-secret-key-min-50-chars
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=api.sirenahealthehr.com

# Database
DATABASE_URL=postgres://user:pass@localhost:5432/sirena_ehr

# JWT
JWT_SECRET_KEY=your-jwt-secret

# Stripe
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...

# Email
SENDGRID_API_KEY=SG...
DEFAULT_FROM_EMAIL=no-reply@sirenahealthehr.com

# Storage
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_STORAGE_BUCKET_NAME=sirena-documents

# Frontend URL (for CORS)
FRONTEND_URL=https://sirenahealthehr.com

# Clearinghouse
EDI_SUBMITTER_ID=...
EDI_API_KEY=...
```

---

## 8. Frontend-Backend Coordination Map

This is the **master coordination table**. Every click on the frontend has a matching backend endpoint.

| Frontend Page | Frontend Route | User Action | Backend Endpoint | Method |
|---------------|----------------|-------------|------------------|--------|
| **Login** | `/login` | Sign In | `/api/v1/auth/login/` | POST |
| **Dashboard** | `/dashboard` | Page load | `/api/v1/dashboard/stats/` | GET |
| **Dashboard** | `/dashboard` | Click notification bell | `/api/v1/notifications/?is_read=false` | GET |
| **Clients** | `/clients` | Page load | `/api/v1/clients/` | GET |
| **Clients** | `/clients` | Search/filter | `/api/v1/clients/?search=...&status=...` | GET |
| **Clients** | `/clients` | Add Client | `/api/v1/clients/` | POST |
| **Client Detail** | `/clients/:id` | Page load | `/api/v1/clients/{id}/` | GET |
| **Client Detail** | `/clients/:id` | Edit client | `/api/v1/clients/{id}/` | PUT |
| **Client Detail** | `/clients/:id` | Discharge | `/api/v1/clients/{id}/` | DELETE |
| **Client Detail** | `/clients/:id` | Upload doc | `/api/v1/documents/` | POST |
| **Calendar** | `/calendar` | Page load | `/api/v1/appointments/?start_date=...&end_date=...` | GET |
| **Calendar** | `/calendar` | New appointment | `/api/v1/appointments/` | POST |
| **Calendar** | `/calendar` | Edit appointment | `/api/v1/appointments/{id}/` | PUT |
| **Calendar** | `/calendar` | Mark attended | `/api/v1/appointments/{id}/status/` | POST |
| **Calendar** | `/calendar` | Cancel | `/api/v1/appointments/{id}/` | DELETE |
| **Session Notes** | `/notes` | Page load | `/api/v1/notes/` | GET |
| **Session Notes** | `/notes` | Filter/search | `/api/v1/notes/?status=...&date_from=...` | GET |
| **Note Editor** | `/notes/new` | Create note | `/api/v1/notes/` | POST |
| **Note Editor** | `/notes/:id/edit` | Save draft | `/api/v1/notes/{id}/` | PUT |
| **Note Editor** | `/notes/:id/edit` | Sign note | `/api/v1/notes/{id}/sign/` | POST |
| **Note Editor** | `/notes/:id/edit` | Request co-sign | `/api/v1/notes/{id}/cosign/` | POST |
| **Billing** | `/billing` | Invoices tab | `/api/v1/invoices/` | GET |
| **Billing** | `/billing` | Claims tab | `/api/v1/claims/` | GET |
| **Billing** | `/billing` | Payments tab | `/api/v1/payments/` | GET |
| **Billing** | `/billing` | Create invoice | `/api/v1/invoices/` | POST |
| **Billing** | `/billing` | Batch generate | `/api/v1/invoices/batch/` | POST |
| **Billing** | `/billing` | Generate claim | `/api/v1/claims/` | POST |
| **Billing** | `/billing` | Submit claim | `/api/v1/claims/{id}/submit/` | POST |
| **Invoice Detail** | `/billing/invoices/:id` | Page load | `/api/v1/invoices/{id}/` | GET |
| **Invoice Detail** | `/billing/invoices/:id` | Record payment | `/api/v1/payments/` | POST |
| **Invoice Detail** | `/billing/invoices/:id` | Stripe payment | `/api/v1/payments/stripe/` | POST |
| **Reports** | `/reports` | Page load | `/api/v1/reports/session-summary/` | GET |
| **Auth Report** | `/reports/authorizations` | Page load | `/api/v1/reports/authorizations/` | GET |
| **Missing Notes** | `/reports/missing-notes` | Page load | `/api/v1/reports/missing-notes/` | GET |
| **Session Summary** | `/reports/session-summary` | Page load | `/api/v1/reports/session-summary/` | GET |
| **Billing Summary** | `/reports/billing-summary` | Page load | `/api/v1/reports/billing-summary/` | GET |
| **All Reports** | `/reports/*` | Export CSV/PDF | `/api/v1/reports/export/` | GET |
| **Settings** | `/settings` | Page load | `/api/v1/settings/organization/` | GET |
| **Settings** | `/settings` | Save settings | `/api/v1/settings/organization/` | PUT |
| **Settings** | `/settings` | Change password | `/api/v1/auth/password/` | PUT |
| **Notifications** | `/notifications` | Page load | `/api/v1/notifications/` | GET |
| **Notifications** | `/notifications` | Mark as read | `/api/v1/notifications/{id}/read/` | PUT |
| **Notifications** | `/notifications` | Mark all read | `/api/v1/notifications/mark-all-read/` | POST |
| **Users Admin** | `/admin/users` | Page load | `/api/v1/users/` | GET |
| **Users Admin** | `/admin/users` | Add user | `/api/v1/users/` | POST |
| **Users Admin** | `/admin/users` | Edit user | `/api/v1/users/{id}/` | PUT |
| **Users Admin** | `/admin/users` | Deactivate | `/api/v1/users/{id}/` | DELETE |
| **Audit Log** | `/admin/audit` | Page load | `/api/v1/admin/audit/` | GET |
| **Audit Log** | `/admin/audit` | Filter | `/api/v1/admin/audit/?user_id=...&date_from=...` | GET |
| **Audit Log** | `/admin/audit` | Export | `/api/v1/admin/audit/export/` | GET |

---

## CRITICAL RULES

1. ALL queries MUST filter by `organization_id` for multi-tenancy
2. ALL foreign keys MUST have ON DELETE CASCADE or RESTRICT
3. ALL timestamps use TIMESTAMP with timezone
4. ALL IDs use UUID with gen_random_uuid()
5. ALL indexes MUST be created for foreign keys
6. NEVER store plain text passwords
7. NEVER store credit card numbers (use Stripe tokens)
8. ALWAYS log PHI access in audit_logs
9. ALWAYS validate role permissions before data access
10. NEVER return data from other organizations
