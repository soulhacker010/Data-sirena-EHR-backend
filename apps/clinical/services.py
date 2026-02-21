"""Note signing service — handles the sign/co-sign/lock lifecycle."""
from django.utils import timezone


class NoteSigningService:
    """Manages the session note signing workflow."""

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

        if supervisor.role not in ('admin', 'supervisor'):
            raise ValueError('Only supervisors can co-sign notes')

        note.supervisor_signature = supervisor_signature
        note.co_signed_at = timezone.now()
        note.co_signed_by = supervisor
        note.status = 'co_signed'
        note.is_locked = True
        note.version += 1
        note.save(update_fields=[
            'supervisor_signature', 'co_signed_at', 'co_signed_by',
            'status', 'is_locked', 'version', 'updated_at'
        ])

        return note
