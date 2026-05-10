"""
Tests for apps.core.sentry — PHI scrubber.

The scrubber is the only thing standing between a Django stack trace and a
third-party error tracker. If it leaks, we have a HIPAA incident. So this
test file is intentionally pedantic — it tests every PHI field, every nesting
level, and every Sentry payload location (request body, query string, headers,
breadcrumbs, stack-frame locals, user context).

If you add a new PHI field to PHI_FIELD_NAMES, add an assertion below.
"""
from apps.core.sentry import (
    PHI_FIELD_NAMES, REDACTED, scrub_breadcrumb, scrub_event,
    _scrub_query_string, _scrub_value,
)


# ─── Recursive value scrubbing ──────────────────────────────────────────────

class TestScrubValue:
    def test_top_level_phi_field(self):
        result = _scrub_value({'first_name': 'John', 'page': 2})
        assert result == {'first_name': REDACTED, 'page': 2}

    def test_case_insensitive(self):
        result = _scrub_value({'First_Name': 'John', 'DOB': '1990-01-01'})
        assert result['First_Name'] == REDACTED
        assert result['DOB'] == REDACTED

    def test_nested_dict(self):
        result = _scrub_value({
            'client': {'first_name': 'John', 'last_name': 'Doe'},
            'page': 2,
        })
        assert result['client']['first_name'] == REDACTED
        assert result['client']['last_name'] == REDACTED
        assert result['page'] == 2

    def test_list_of_dicts(self):
        result = _scrub_value([
            {'first_name': 'A', 'id': 1},
            {'first_name': 'B', 'id': 2},
        ])
        assert all(r['first_name'] == REDACTED for r in result)
        assert [r['id'] for r in result] == [1, 2]

    def test_deeply_nested(self):
        deep = {'a': {'b': {'c': {'d': {'first_name': 'leaked'}}}}}
        result = _scrub_value(deep)
        assert result['a']['b']['c']['d']['first_name'] == REDACTED

    def test_depth_limit_drops_extreme_nesting(self):
        # Build something 15 levels deep; depth limit is 12. The contract is
        # "PHI never reaches the output", regardless of what shape the cap
        # produces. Round-trip the result to JSON-style flat string and
        # assert the leak word is gone.
        import json
        deep = current = {}
        for _ in range(15):
            current['level'] = {}
            current = current['level']
        current['first_name'] = 'leakcanary'

        result = _scrub_value(deep)
        flat = json.dumps(result)
        assert 'leakcanary' not in flat, f'PHI leaked past depth cap: {flat}'

    def test_non_phi_passthrough(self):
        result = _scrub_value({'organization_id': 'abc', 'page': 1, 'count': 100})
        assert result == {'organization_id': 'abc', 'page': 1, 'count': 100}

    def test_password_redacted(self):
        assert _scrub_value({'password': 'p@ssw0rd'}) == {'password': REDACTED}
        assert _scrub_value({'access_token': 'eyJ...'}) == {'access_token': REDACTED}

    def test_diagnoses_list_redacted(self):
        result = _scrub_value({'diagnosis_codes': ['F90.0', 'F84.0']})
        assert result['diagnosis_codes'] == REDACTED

    def test_insurance_redacted(self):
        result = _scrub_value({
            'insurance_primary_id': 'ABC123',
            'insurance_primary_name': 'Aetna',
        })
        assert result['insurance_primary_id'] == REDACTED
        assert result['insurance_primary_name'] == REDACTED


# ─── Query string scrubbing ─────────────────────────────────────────────────

class TestScrubQueryString:
    def test_redacts_phi_keys(self):
        qs = 'email=a%40b.com&page=2&dob=1990-01-01'
        out = _scrub_query_string(qs)
        assert f'email={REDACTED}' in out
        assert f'dob={REDACTED}' in out
        assert 'page=2' in out

    def test_empty(self):
        assert _scrub_query_string('') == ''

    def test_no_phi(self):
        assert _scrub_query_string('page=1&size=20') == 'page=1&size=20'


# ─── Full event scrubbing ───────────────────────────────────────────────────

class TestScrubEvent:
    def test_request_data_scrubbed(self):
        event = {
            'request': {
                'data': {'first_name': 'John', 'page': 1},
                'method': 'POST',
                'url': 'https://example.com/api/v1/clients/',
            },
        }
        result = scrub_event(event)
        assert result['request']['data']['first_name'] == REDACTED
        assert result['request']['data']['page'] == 1

    def test_request_query_string_scrubbed(self):
        event = {
            'request': {
                'query_string': 'email=test%40x.com&page=1',
            },
        }
        result = scrub_event(event)
        assert REDACTED in result['request']['query_string']
        assert 'page=1' in result['request']['query_string']

    def test_authorization_header_redacted(self):
        event = {
            'request': {
                'headers': {
                    'Authorization': 'Bearer eyJhbGc...',
                    'Cookie': 'sessionid=xyz',
                    'User-Agent': 'Mozilla/5.0',
                },
            },
        }
        result = scrub_event(event)
        assert result['request']['headers']['Authorization'] == REDACTED
        assert result['request']['headers']['Cookie'] == REDACTED
        assert result['request']['headers']['User-Agent'] == 'Mozilla/5.0'

    def test_cookies_dropped(self):
        event = {'request': {'cookies': {'sessionid': 'xyz', 'csrftoken': 'abc'}}}
        result = scrub_event(event)
        assert 'cookies' not in result['request']

    def test_user_context_keeps_only_id(self):
        event = {'user': {'id': '123', 'email': 'a@b.com', 'username': 'jane', 'ip_address': '1.2.3.4'}}
        result = scrub_event(event)
        assert result['user'] == {'id': '123'}

    def test_breadcrumb_data_scrubbed(self):
        event = {
            'breadcrumbs': {
                'values': [
                    {'category': 'http', 'data': {'first_name': 'John', 'method': 'POST'}},
                ],
            },
        }
        result = scrub_event(event)
        crumb = result['breadcrumbs']['values'][0]
        assert crumb['data']['first_name'] == REDACTED
        assert crumb['data']['method'] == 'POST'

    def test_stack_frame_locals_scrubbed(self):
        event = {
            'exception': {
                'values': [
                    {
                        'type': 'ValueError',
                        'stacktrace': {
                            'frames': [
                                {
                                    'function': 'create_client',
                                    'vars': {
                                        'client_data': {
                                            'first_name': 'John',
                                            'last_name': 'Doe',
                                            'dob': '1990-01-01',
                                        },
                                        'org_id': 'abc',
                                    },
                                },
                            ],
                        },
                    },
                ],
            },
        }
        result = scrub_event(event)
        frame_vars = result['exception']['values'][0]['stacktrace']['frames'][0]['vars']
        assert frame_vars['client_data']['first_name'] == REDACTED
        assert frame_vars['client_data']['last_name'] == REDACTED
        assert frame_vars['client_data']['dob'] == REDACTED
        assert frame_vars['org_id'] == 'abc'

    def test_extra_and_tags_scrubbed(self):
        event = {
            'extra': {'patient_name': 'Leak Me', 'op': 'create'},
            'tags': {'email': 'a@b.com', 'env': 'prod'},
        }
        result = scrub_event(event)
        assert result['extra']['patient_name'] == REDACTED
        assert result['extra']['op'] == 'create'
        assert result['tags']['email'] == REDACTED
        assert result['tags']['env'] == 'prod'


# ─── Breadcrumb hook ────────────────────────────────────────────────────────

class TestScrubBreadcrumb:
    def test_data_scrubbed(self):
        crumb = {'category': 'http', 'data': {'first_name': 'John', 'method': 'POST'}}
        result = scrub_breadcrumb(crumb)
        assert result['data']['first_name'] == REDACTED
        assert result['data']['method'] == 'POST'

    def test_message_querystring_scrubbed(self):
        crumb = {'message': 'GET /api/clients/?email=a@b.com&page=1'}
        result = scrub_breadcrumb(crumb)
        assert REDACTED in result['message']
        assert 'page=1' in result['message']


# ─── PHI_FIELD_NAMES sanity ─────────────────────────────────────────────────

class TestPHIFieldRoster:
    def test_all_known_phi_fields_listed(self):
        # Tripwire: if anyone removes a critical PHI key from the set this
        # test fails loudly. Don't shrink without thinking.
        critical = {
            'first_name', 'last_name', 'date_of_birth', 'dob', 'phone',
            'email', 'address', 'ssn', 'mrn', 'diagnosis_codes',
            'note_content', 'narrative', 'soap',
            'password', 'token', 'access', 'refresh',
        }
        missing = critical - PHI_FIELD_NAMES
        assert not missing, f'PHI tripwire fields removed from set: {missing}'
