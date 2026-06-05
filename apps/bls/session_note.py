"""
Session-note auto-log integration.

When a BLS session ends and the session was attached to an appointment, we
append a structured "BLS auto-logged" entry to that appointment's
SessionNote.note_data. Format defined in BLS-SYSTEM-DESIGN.md §8.

Why we add to note_data['bls_auto_log'] rather than mutating the narrative
text:
  1. Sign-and-seal — a signed note's content is immutable, but appending a
     structured array entry preserves the clinical record's integrity. We
     enforce this by refusing to update notes that are already signed.
  2. Future-proof rendering — the frontend can render the structured data
     however it likes (collapsed pill, expanded table, etc.) without parsing
     a free-form text section.
  3. Multiple BLS sessions per appointment — clinically possible. We append
     each entry rather than overwriting.

The function is best-effort: any failure logs a warning and the BLS session
itself isn't blocked. Auto-logging is value-add, not a hard dependency.
"""
from __future__ import annotations

import logging

from django.utils import timezone

logger = logging.getLogger(__name__)


def append_bls_auto_log_to_session_note(session) -> bool:
    """
    Append a BLS summary to the appointment's session note. Returns True on
    success, False if there was nothing to do (no appointment) or the note
    is locked/signed.

    The session argument is a BLSSession instance that has already been
    transitioned to status=ended.
    """
    if session.appointment_id is None:
        return False

    # Build the structured entry per design doc §8 format.
    entry = _build_entry(session)

    from apps.clinical.models import SessionNote
    note, created = SessionNote.objects.get_or_create(
        appointment_id=session.appointment_id,
        defaults={
            'client_id': session.client_id,
            'provider_id': session.therapist_id,
            'note_data': {},
            'status': 'draft',
        },
    )

    if note.is_locked or note.status in ('signed', 'co_signed'):
        # Clinical record integrity — don't mutate signed notes. The audit
        # trail still has the BLS session; the clinician can manually add
        # an addendum if they need the auto-log on a signed note.
        logger.info(
            'BLS auto-log skipped — note %s is locked/signed',
            note.id,
        )
        return False

    note_data = note.note_data or {}
    log_entries = note_data.get('bls_auto_log', [])
    if not isinstance(log_entries, list):
        log_entries = []
    log_entries.append(entry)
    note_data['bls_auto_log'] = log_entries
    note.note_data = note_data
    note.save(update_fields=['note_data', 'updated_at'])

    logger.info(
        'BLS auto-log appended to note %s (created=%s, entries=%d)',
        note.id, created, len(log_entries),
    )
    return True


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _build_entry(session) -> dict:
    """Compose the auto-log entry. Pure function for testability."""
    snapshot = session.settings_snapshot or {}

    # Visual descriptor (color or illustration), audio descriptor (sound +
    # volume). Both pulled from the snapshot the frontend persisted with
    # end_session.
    visual = _describe_visual(snapshot)
    audio = _describe_audio(snapshot)

    return {
        'session_id': str(session.id),
        'ended_at': timezone.now().isoformat(),
        'duration_seconds': session.duration_seconds,
        'pass_count': session.pass_count,
        'set_count': session.set_count,
        'modality': session.modality,
        'visual': visual,
        'audio': audio,
        'speed': snapshot.get('speed'),
    }


def _describe_visual(snapshot: dict) -> str:
    stimulus = snapshot.get('stimulus') or 'dot'
    direction = snapshot.get('direction') or 'horizontal'
    color = snapshot.get('color') or ''
    background = snapshot.get('background') or ''
    glyph = snapshot.get('stimulus_glyph')

    if stimulus == 'illustration' and snapshot.get('illustrationId'):
        return f'Illustration ({snapshot["illustrationId"]}) — {direction}'
    if stimulus == 'emoji' and glyph:
        return f'Emoji {glyph} — {direction}'
    if stimulus == 'animal' and glyph:
        return f'Animal {glyph} — {direction}'

    parts = [direction]
    if color:
        parts.append(f'{color} dot')
    if background:
        parts.append(f'on {background} background')
    return ' '.join(parts)


def _describe_audio(snapshot: dict) -> str:
    sound = snapshot.get('sound') or ''
    volume = snapshot.get('volume')
    if not sound:
        return 'no audio'
    if isinstance(volume, (int, float)):
        return f'{sound} (volume {int(volume * 100)}%)'
    return sound
