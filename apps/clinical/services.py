import cloudinary
import cloudinary.uploader
from cloudinary.utils import private_download_url
from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify
from rest_framework.exceptions import ValidationError


class NoteSigningService:
    """Manages the session note signing workflow."""

    @staticmethod
    def request_co_sign_note(note, recipient, requested_by, message=''):
        """
        Store a co-sign request on a signed note.

        This keeps the request lightweight without introducing a new model.
        """
        if note.is_locked:
            raise ValueError('Note is already locked and cannot be modified')

        if note.status != 'signed':
            raise ValueError('Note must be signed before co-sign can be requested')

        if recipient.pk == requested_by.pk:
            raise ValueError('You cannot request a co-sign from yourself')

        note_data = dict(note.note_data or {})
        note_data['co_sign_request'] = {
            'recipient_id': str(recipient.id),
            'recipient_name': recipient.full_name,
            'requested_by_id': str(requested_by.id),
            'requested_by_name': requested_by.full_name,
            'message': message or '',
            'requested_at': timezone.now().isoformat(),
        }

        note.note_data = note_data
        note.version += 1
        note.save(update_fields=['note_data', 'version', 'updated_at'])
        return note

    @staticmethod
    def sign_note(note, signature_data, user):
        """
        Sign a session note.

        Validates required fields, sets signature, and advances status.
        """
        if note.is_locked:
            raise ValueError('Note is already locked and cannot be modified')

        if note.status not in ('draft', 'completed'):
            raise ValueError(f'Note cannot be signed from status: {note.status}')

        # Validate required fields from template
        if note.template and note.template.required_fields:
            missing = []
            for field in note.template.required_fields:
                if not note.note_data.get(field):
                    missing.append(field)
            if missing:
                raise ValueError(f'Missing required fields: {", ".join(missing)}')

        note.signature_data = signature_data
        note.signed_at = timezone.now()
        note.status = 'signed'
        note.version += 1
        note.save(update_fields=[
            'signature_data', 'signed_at', 'status', 'version', 'updated_at'
        ])

        return note

    @staticmethod
    def co_sign_note(note, supervisor_signature, supervisor):
        """
        Co-sign a note (supervisor only).

        Locks the note after co-signing to prevent further edits.
        """
        if note.status != 'signed':
            raise ValueError('Note must be signed before it can be co-signed')

        note_data = dict(note.note_data or {})
        co_sign_request = note_data.get('co_sign_request') or {}
        requested_recipient_id = co_sign_request.get('recipient_id')

        if requested_recipient_id:
            if str(supervisor.id) != str(requested_recipient_id):
                raise ValueError('This co-sign request is assigned to another user')
        elif supervisor.role not in ('admin', 'supervisor'):
            raise ValueError('Only supervisors can co-sign notes')

        note.supervisor_signature = supervisor_signature
        note.co_signed_at = timezone.now()
        note.co_signed_by = supervisor
        note.status = 'co_signed'
        note.is_locked = True
        note.version += 1
        note_data.pop('co_sign_request', None)
        note.note_data = note_data
        note.save(update_fields=[
            'supervisor_signature', 'co_signed_at', 'co_signed_by',
            'status', 'is_locked', 'version', 'note_data', 'updated_at'
        ])

        return note


class DocumentStorageService:
    ACCESS_URL_TTL_SECONDS = 300

    @staticmethod
    def _configure_cloudinary():
        config = settings.CLOUDINARY_STORAGE
        cloudinary.config(
            cloud_name=config.get('CLOUD_NAME', ''),
            api_key=config.get('API_KEY', ''),
            api_secret=config.get('API_SECRET', ''),
            secure=True,
        )

    @staticmethod
    def _is_cloudinary_configured():
        config = settings.CLOUDINARY_STORAGE
        return all([
            config.get('CLOUD_NAME') and config.get('CLOUD_NAME') != 'placeholder',
            config.get('API_KEY') and config.get('API_KEY') != 'placeholder',
            config.get('API_SECRET') and config.get('API_SECRET') != 'placeholder',
        ])

    @staticmethod
    def _get_resource_type(file_name, content_type=''):
        mime = (content_type or '').lower()
        lower_name = (file_name or '').lower()
        if mime.startswith('image/') or lower_name.endswith(('.jpg', '.jpeg', '.png')):
            return 'image'
        return 'raw'

    @staticmethod
    def _get_file_format(file_name):
        if not file_name or '.' not in file_name:
            raise ValidationError({'file': 'Document format could not be determined.'})
        return file_name.rsplit('.', 1)[1].lower()

    @staticmethod
    def _build_folder(client):
        date_folder = timezone.now().strftime('%Y-%m-%d')
        client_name = f'{getattr(client, "first_name", "")} {getattr(client, "last_name", "")}'.strip()
        client_slug = slugify(client_name) or f'client-{client.id}'
        return f'sirena/client-documents/{date_folder}/{client_slug}-{client.id}'

    @classmethod
    def upload_document(cls, file, client):
        if not cls._is_cloudinary_configured():
            return {
                'file_path': f'documents/{file.name}',
                'cloudinary_public_id': '',
            }

        cls._configure_cloudinary()
        upload_result = cloudinary.uploader.upload(
            file,
            folder=cls._build_folder(client),
            resource_type='auto',
            type='authenticated',
            use_filename=True,
            unique_filename=True,
            overwrite=False,
        )
        public_id = upload_result.get('public_id')
        if not public_id:
            raise ValidationError({'file': 'Document upload failed. Please try again.'})
        return {
            'file_path': '',
            'cloudinary_public_id': public_id,
        }

    @classmethod
    def delete_document(cls, *, cloudinary_public_id, file_name, file_type=''):
        if not cloudinary_public_id or not cls._is_cloudinary_configured():
            return

        cls._configure_cloudinary()
        result = cloudinary.uploader.destroy(
            cloudinary_public_id,
            resource_type=cls._get_resource_type(file_name, file_type),
            type='authenticated',
            invalidate=True,
        )
        if result.get('result') not in {'ok', 'not found'}:
            raise ValidationError({'file': 'Document deletion failed. Please try again.'})

    @classmethod
    def generate_access_url(cls, document, *, as_attachment=False):
        if not document.cloudinary_public_id or not cls._is_cloudinary_configured():
            if not document.file_path:
                raise ValidationError({'file': 'Document access is unavailable.'})
            return document.file_path

        cls._configure_cloudinary()
        expires_at = int(timezone.now().timestamp()) + cls.ACCESS_URL_TTL_SECONDS
        signed_url = private_download_url(
            document.cloudinary_public_id,
            cls._get_file_format(document.file_name),
            resource_type=cls._get_resource_type(document.file_name, document.file_type),
            type='authenticated',
            expires_at=expires_at,
            attachment=as_attachment,
        )
        if not signed_url:
            raise ValidationError({'file': 'Document access could not be generated.'})
        return signed_url
