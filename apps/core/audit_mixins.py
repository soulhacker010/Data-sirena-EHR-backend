"""
Reusable audit mixins for DRF ViewSets that touch PHI.

The HIPAA Security Rule requires recording who accessed which patient record
and when. AuditMiddleware already covers writes (POST/PUT/PATCH/DELETE);
these mixins fill the read side: every GET on a single PHI record creates a
``phi_access`` audit row.

Hard rule: the audit log itself MUST NOT contain PHI. The whole point of the
audit log is to record who accessed PHI — if the log itself stored names,
DOBs, or diagnoses, exporting the log (e.g. for compliance review or analytics)
would re-leak the data we're trying to track. So `changes` is restricted to
non-PHI metadata: what kind of resource, scope, optional FK to the parent
record (already an opaque UUID).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class PHIAccessAuditMixin:
    """
    Mixin that records a ``phi_access`` audit row each time a single PHI
    record is retrieved.

    Subclasses set ``audit_table_name`` (e.g. 'notes', 'intakes') so the
    audit row's ``table_name`` is human-readable. If unset, the URL resolver
    in middleware will fall back to whatever path mapping it can extract.

    ``retrieve`` is wrapped, not replaced — the parent class's behavior
    (serialization, permissions, 404, etc.) is preserved exactly. Audit
    failures are caught and logged so they cannot bring down a request.
    """

    audit_table_name: str = ''

    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        try:
            self._write_phi_access_audit(request, kwargs)
        except Exception:
            logger.exception(
                'PHIAccessAuditMixin failed to write audit for %s',
                self.__class__.__name__,
            )
        return response

    def _write_phi_access_audit(self, request, view_kwargs):
        from apps.audit.utils import write_audit

        record_id = view_kwargs.get('pk') or view_kwargs.get(self.lookup_url_kwarg or 'pk')
        # Stringify so a UUID/int both serialize cleanly into JSONField.
        if record_id is not None:
            record_id = str(record_id)

        write_audit(
            request,
            action='phi_access',
            table_name=self.audit_table_name or 'unknown',
            record_id=record_id,
            # NO PHI here. Only safe metadata: nothing that could re-identify
            # a patient if the audit log is later exported.
            changes=None,
        )
