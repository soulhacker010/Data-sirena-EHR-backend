"""
Office Ally SFTP integration service.

Handles:
- Uploading X12 837P claim files to OA /inbound
- Polling OA /outbound for 999, 277CA, 835 response files
- Parsing all response file types and updating claim/payment records

Requires env vars:
    OA_SFTP_HOST, OA_SFTP_PORT, OA_SFTP_USERNAME, OA_SFTP_PASSWORD
    OA_SFTP_INBOUND  (default: /inbound)
    OA_SFTP_OUTBOUND (default: /outbound)
"""
import io
import logging
import os
import re
from datetime import datetime
from typing import Optional

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def _get_sftp_config() -> dict:
    return {
        'host':     getattr(settings, 'OA_SFTP_HOST', ''),
        'port':     int(getattr(settings, 'OA_SFTP_PORT', 22)),
        'username': getattr(settings, 'OA_SFTP_USERNAME', ''),
        'password': getattr(settings, 'OA_SFTP_PASSWORD', ''),
        'inbound':  getattr(settings, 'OA_SFTP_INBOUND', '/inbound'),
        'outbound': getattr(settings, 'OA_SFTP_OUTBOUND', '/outbound'),
    }


def _is_configured() -> bool:
    cfg = _get_sftp_config()
    return bool(cfg['host'] and cfg['username'] and cfg['password'])


def _get_sftp_client():
    """Return an open paramiko SFTP client. Caller must close."""
    import paramiko
    cfg = _get_sftp_config()
    transport = paramiko.Transport((cfg['host'], cfg['port']))
    transport.connect(username=cfg['username'], password=cfg['password'])
    return paramiko.SFTPClient.from_transport(transport), transport


# ─── Upload ──────────────────────────────────────────────────────────────────

def upload_claim_file(x12_content: str, filename: str) -> dict:
    """
    Upload an X12 837P file to Office Ally SFTP /inbound.

    Returns:
        {'success': True, 'filename': filename}
    Raises:
        RuntimeError if SFTP not configured or upload fails.
    """
    if not _is_configured():
        logger.warning('OA SFTP not configured — claim file not uploaded: %s', filename)
        raise RuntimeError('Office Ally SFTP credentials not configured. Add OA_SFTP_HOST, OA_SFTP_USERNAME, OA_SFTP_PASSWORD to settings.')

    cfg = _get_sftp_config()
    sftp, transport = _get_sftp_client()
    try:
        remote_path = f"{cfg['inbound']}/{filename}"
        file_bytes = x12_content.encode('utf-8')
        with sftp.open(remote_path, 'wb') as f:
            f.write(file_bytes)
        logger.info('Uploaded claim file to OA: %s (%d bytes)', filename, len(file_bytes))
        return {'success': True, 'filename': filename}
    finally:
        sftp.close()
        transport.close()


# ─── Poll Outbound ────────────────────────────────────────────────────────────

def list_outbound_files() -> list:
    """
    List all files in Office Ally SFTP /outbound directory.
    Returns list of filenames.
    """
    if not _is_configured():
        return []

    cfg = _get_sftp_config()
    sftp, transport = _get_sftp_client()
    try:
        files = sftp.listdir(cfg['outbound'])
        logger.info('OA outbound files: %d found', len(files))
        return files
    finally:
        sftp.close()
        transport.close()


def download_outbound_file(filename: str) -> str:
    """Download a file from OA /outbound and return its contents as a string."""
    cfg = _get_sftp_config()
    sftp, transport = _get_sftp_client()
    try:
        remote_path = f"{cfg['outbound']}/{filename}"
        buf = io.BytesIO()
        sftp.getfo(remote_path, buf)
        content = buf.getvalue().decode('utf-8', errors='replace')
        logger.info('Downloaded OA response file: %s (%d bytes)', filename, len(content))
        return content
    finally:
        sftp.close()
        transport.close()


def delete_outbound_file(filename: str) -> None:
    """Delete a processed file from OA /outbound to keep the directory clean."""
    cfg = _get_sftp_config()
    sftp, transport = _get_sftp_client()
    try:
        sftp.remove(f"{cfg['outbound']}/{filename}")
        logger.info('Deleted processed OA file: %s', filename)
    finally:
        sftp.close()
        transport.close()


# ─── Response Parsers ─────────────────────────────────────────────────────────

def _classify_file(filename: str) -> str:
    """Determine the type of an OA response file from its name."""
    fn = filename.lower()
    if '_999.' in fn or fn.endswith('.999'):
        return '999'
    if '277ca' in fn or fn.endswith('.277'):
        return '277ca'
    if '_835_' in fn or fn.endswith('.835'):
        return '835'
    if 'fs_hcfa' in fn or 'summary' in fn:
        return 'file_summary'
    return 'unknown'


def parse_file_summary(content: str) -> dict:
    """
    Parse Office Ally File Summary (FS_HCFA_*.txt).

    Returns:
        {
          'accepted': [{'claim_num', 'oa_claim_id', 'patient_id', 'cpt', 'payer'}],
          'rejected': [{'claim_num', 'oa_claim_id', 'error'}],
          'pending':  [...],
        }
    """
    result = {'accepted': [], 'rejected': [], 'pending': []}
    section = None

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        if 'ACCEPTED CLAIM' in stripped:
            section = 'accepted'
            continue
        if 'ERROR CLAIM' in stripped or 'REJECTED' in stripped:
            section = 'rejected'
            continue
        if 'PENDING CLAIM' in stripped:
            section = 'pending'
            continue

        # Skip header lines
        if stripped.startswith('CLAIM#') or stripped.startswith('---') or stripped.startswith('Field'):
            continue

        if section and len(line) >= 20:
            try:
                claim_num  = line[0:6].strip()
                oa_claim   = line[7:17].strip()
                patient_id = line[19:37].strip()
                name       = line[37:57].strip()
                cpt        = line[92:98].strip() if len(line) > 92 else ''
                payer      = line[146:151].strip() if len(line) > 146 else ''
                error      = line[153:183].strip() if len(line) > 153 and section == 'rejected' else ''

                entry = {
                    'claim_num': claim_num,
                    'oa_claim_id': oa_claim,
                    'patient_id': patient_id,
                    'name': name,
                    'cpt': cpt,
                    'payer': payer,
                }
                if section == 'rejected':
                    entry['error'] = error
                result[section].append(entry)
            except Exception as e:
                logger.debug('File summary parse error on line: %s — %s', line[:50], e)

    return result


def parse_999(content: str) -> dict:
    """
    Parse X12 999 acknowledgement.
    Returns {'accepted': bool, 'errors': [str]}
    """
    errors = []
    accepted = True

    for line in content.splitlines():
        seg = line.strip().rstrip('~')
        if seg.startswith('AK9'):
            parts = seg.split('*')
            if len(parts) > 1 and parts[1] != 'A':
                accepted = False
        if seg.startswith('IK5'):
            parts = seg.split('*')
            if len(parts) > 1 and parts[1] != 'A':
                accepted = False
        if seg.startswith('CTX') or seg.startswith('IK4'):
            errors.append(seg)

    return {'accepted': accepted, 'errors': errors}


def parse_277ca(content: str) -> list:
    """
    Parse X12 277CA (Claim Acknowledgement).

    Returns list of:
        {'patient_acct_num': str, 'status_code': str, 'status_text': str, 'payer_ref': str}
    """
    STATUS_CODES = {
        'A1': 'Acknowledged',
        'A2': 'Acknowledged with errors',
        'A3': 'Rejected — not forwarded',
        'A4': 'Not found',
        'A5': 'Split claim',
        'A6': 'Duplicate',
        'A7': 'Rejected',
        'A8': 'Accepted',
    }
    results = []
    current = {}

    for line in content.splitlines():
        seg = line.strip().rstrip('~')
        parts = seg.split('*')

        if parts[0] == 'CLM' and len(parts) > 1:
            if current:
                results.append(current)
            current = {'patient_acct_num': parts[1]}

        elif parts[0] == 'STC' and len(parts) > 1:
            code = parts[1].split(':')[0] if ':' in parts[1] else parts[1]
            current['status_code'] = code
            current['status_text'] = STATUS_CODES.get(code, code)

        elif parts[0] == 'REF' and len(parts) > 2 and parts[1] == 'D9':
            current['payer_ref'] = parts[2]

    if current:
        results.append(current)

    return results


def parse_835(content: str) -> dict:
    """
    Parse X12 835 ERA (Electronic Remittance Advice).

    Returns:
        {
          'check_number': str,
          'payment_date': str,
          'total_payment': str,
          'payer_name': str,
          'claims': [{'patient_acct_num', 'billed', 'allowed', 'paid', 'adjustments': [...]}]
        }
    """
    result = {
        'check_number': '',
        'payment_date': '',
        'total_payment': '',
        'payer_name': '',
        'claims': [],
    }
    current_claim = None

    ADJUSTMENT_REASONS = {
        'CO': 'Contractual Obligation',
        'PR': 'Patient Responsibility',
        'OA': 'Other Adjustment',
        'PI': 'Payor Initiated',
    }

    for line in content.splitlines():
        seg = line.strip().rstrip('~')
        parts = seg.split('*')

        if parts[0] == 'BPR' and len(parts) > 4:
            result['total_payment'] = parts[2]
            result['payment_date'] = parts[16] if len(parts) > 16 else ''

        elif parts[0] == 'TRN' and len(parts) > 2:
            result['check_number'] = parts[2]

        elif parts[0] == 'NM1' and len(parts) > 3 and parts[1] == 'PR':
            result['payer_name'] = parts[3]

        elif parts[0] == 'CLP' and len(parts) > 6:
            if current_claim:
                result['claims'].append(current_claim)
            current_claim = {
                'patient_acct_num': parts[1],
                'status': parts[2],
                'billed': parts[3],
                'allowed': parts[4] if len(parts) > 4 else '',
                'paid': parts[4] if len(parts) > 4 else '',
                'adjustments': [],
            }
            if len(parts) > 5:
                current_claim['paid'] = parts[5]

        elif parts[0] == 'CAS' and current_claim and len(parts) > 3:
            group = parts[1]
            reason = parts[2]
            amount = parts[3] if len(parts) > 3 else ''
            current_claim['adjustments'].append({
                'group': group,
                'group_desc': ADJUSTMENT_REASONS.get(group, group),
                'reason_code': reason,
                'amount': amount,
            })

    if current_claim:
        result['claims'].append(current_claim)

    return result


# ─── Main Processor ───────────────────────────────────────────────────────────

def process_outbound_files(organization) -> dict:
    """
    Download and process all pending response files from OA /outbound.
    Updates Claim records and creates OASubmissionLog entries.

    Returns summary dict of what was processed.
    """
    from apps.billing.models import Claim, OASubmissionLog

    if not _is_configured():
        return {'error': 'SFTP not configured'}

    files = list_outbound_files()
    summary = {'processed': 0, 'errors': 0, 'files': []}

    for filename in files:
        file_type = _classify_file(filename)
        if file_type == 'unknown':
            continue

        try:
            content = download_outbound_file(filename)
            log = OASubmissionLog.objects.create(
                organization=organization,
                file_type=file_type,
                filename=filename,
                raw_response=content[:50000],
                processed_at=timezone.now(),
            )

            if file_type == 'file_summary':
                parsed = parse_file_summary(content)
                _apply_file_summary(parsed)
                log.accepted_count = len(parsed['accepted'])
                log.rejected_count = len(parsed['rejected'])
                log.status = 'processed'

            elif file_type == '999':
                parsed = parse_999(content)
                log.status = 'accepted' if parsed['accepted'] else 'rejected'

            elif file_type == '277ca':
                parsed = parse_277ca(content)
                _apply_277ca(parsed)
                log.claim_count = len(parsed)
                log.status = 'processed'

            elif file_type == '835':
                parsed = parse_835(content)
                _apply_835(parsed, organization)
                log.claim_count = len(parsed.get('claims', []))
                log.status = 'processed'

            log.save(update_fields=['accepted_count', 'rejected_count', 'claim_count', 'status'])
            summary['processed'] += 1
            summary['files'].append(filename)

            # Delete from outbound once processed
            delete_outbound_file(filename)

        except Exception as e:
            logger.error('Failed to process OA file %s: %s', filename, e, exc_info=True)
            summary['errors'] += 1

    return summary


def _apply_file_summary(parsed: dict) -> None:
    """Update Claim.oa_status from File Summary data."""
    from apps.billing.models import Claim

    for item in parsed.get('accepted', []):
        acct = item.get('patient_acct_num') or item.get('claim_num', '')
        if acct:
            Claim.objects.filter(id=acct).update(
                oa_claim_id=item.get('oa_claim_id', ''),
                oa_status='accepted',
                status='accepted',
            )

    for item in parsed.get('rejected', []):
        acct = item.get('patient_acct_num') or item.get('claim_num', '')
        if acct:
            Claim.objects.filter(id=acct).update(
                oa_status='rejected',
                oa_rejection_reason=item.get('error', ''),
                status='denied',
            )


def _apply_277ca(parsed: list) -> None:
    """Update Claim status from 277CA data."""
    from apps.billing.models import Claim

    for item in parsed:
        acct = item.get('patient_acct_num', '')
        if not acct:
            continue
        status_code = item.get('status_code', '')
        is_accepted = status_code in ('A1', 'A5', 'A8')
        Claim.objects.filter(id=acct).update(
            oa_status=item.get('status_text', status_code),
            status='accepted' if is_accepted else 'denied',
            oa_rejection_reason='' if is_accepted else item.get('status_text', ''),
        )


def _apply_835(parsed: dict, organization) -> None:
    """Post 835 ERA payments to Claim and Invoice records."""
    from apps.billing.models import Claim, Payment, Invoice

    for claim_data in parsed.get('claims', []):
        acct = claim_data.get('patient_acct_num', '')
        if not acct:
            continue
        try:
            claim = Claim.objects.get(id=acct)
        except Claim.DoesNotExist:
            logger.warning('835 ERA: claim not found for acct %s', acct)
            continue

        paid = float(claim_data.get('paid', 0) or 0)
        if paid > 0:
            Payment.objects.create(
                invoice=claim.invoice,
                claim=claim,
                client=claim.client,
                amount=paid,
                payment_type='payment',
                payer_type='insurance',
                payment_method='ERA',
                reference_number=parsed.get('check_number', ''),
                notes=f"ERA auto-posted. Check: {parsed.get('check_number', '')} Date: {parsed.get('payment_date', '')}",
            )
            claim.insurance_paid = paid
            claim.status = 'paid'
            claim.paid_at = timezone.now()
            claim.save(update_fields=['insurance_paid', 'status', 'paid_at', 'updated_at'])
            claim.invoice.recalculate_balance()
