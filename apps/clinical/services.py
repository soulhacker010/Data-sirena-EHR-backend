import logging

import boto3
from botocore.exceptions import ClientError
from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify
from rest_framework.exceptions import ValidationError

logger = logging.getLogger(__name__)


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
    """Handles document upload/download/delete via AWS S3."""

    @staticmethod
    def _get_s3_client():
        return boto3.client(
            's3',
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION_NAME,
            config=boto3.session.Config(signature_version=settings.AWS_S3_SIGNATURE_VERSION),
        )

    @staticmethod
    def _is_configured():
        return bool(
            settings.AWS_ACCESS_KEY_ID
            and settings.AWS_SECRET_ACCESS_KEY
            and settings.AWS_STORAGE_BUCKET_NAME
        )

    @staticmethod
    def _build_s3_key(client, file_name):
        date_folder = timezone.now().strftime('%Y/%m')
        client_name = f'{getattr(client, "first_name", "")} {getattr(client, "last_name", "")}'.strip()
        client_slug = slugify(client_name) or f'client-{client.id}'
        safe_name = slugify(file_name.rsplit('.', 1)[0]) if '.' in file_name else slugify(file_name)
        ext = file_name.rsplit('.', 1)[1].lower() if '.' in file_name else ''
        ts = int(timezone.now().timestamp())
        return f'documents/{date_folder}/{client_slug}-{client.id}/{safe_name}-{ts}.{ext}'

    @classmethod
    def upload_document(cls, file, client):
        if not cls._is_configured():
            return {
                'file_path': f'documents/{file.name}',
                's3_key': '',
            }

        s3_key = cls._build_s3_key(client, file.name)
        try:
            s3 = cls._get_s3_client()
            s3.put_object(
                Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                Key=s3_key,
                Body=file.read(),
                ContentType=file.content_type or 'application/octet-stream',
            )
        except ClientError as exc:
            logger.error('S3 upload failed: %s', exc)
            raise ValidationError({'file': 'Document upload failed. Please try again.'})

        return {
            'file_path': s3_key,
            's3_key': s3_key,
        }

    @classmethod
    def delete_document(cls, *, s3_key, **_kwargs):
        if not s3_key or not cls._is_configured():
            return

        try:
            s3 = cls._get_s3_client()
            s3.delete_object(
                Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                Key=s3_key,
            )
        except ClientError as exc:
            logger.error('S3 delete failed: %s', exc)
            raise ValidationError({'file': 'Document deletion failed. Please try again.'})

    @classmethod
    def generate_access_url(cls, document, *, as_attachment=False):
        key = document.s3_key or document.file_path
        if not key or not cls._is_configured():
            raise ValidationError({'file': 'Document access is unavailable.'})

        try:
            s3 = cls._get_s3_client()
            params = {
                'Bucket': settings.AWS_STORAGE_BUCKET_NAME,
                'Key': key,
            }
            if as_attachment:
                params['ResponseContentDisposition'] = (
                    f'attachment; filename="{document.file_name}"'
                )
            url = s3.generate_presigned_url(
                'get_object',
                Params=params,
                ExpiresIn=settings.AWS_QUERYSTRING_EXPIRE,
            )
        except ClientError as exc:
            logger.error('S3 presigned URL failed: %s', exc)
            raise ValidationError({'file': 'Document access could not be generated.'})

        return url
