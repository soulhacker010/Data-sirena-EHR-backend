"""
Tests for BUILD 6.1: Auto-suggest CPT codes
"""
import pytest
from django.urls import reverse
from apps.billing.cpt_catalog import CPTCatalog


@pytest.mark.django_db
class TestCPTSuggestions:
    """Test CPT code auto-suggestions."""

    def test_cpt_catalog_suggest_by_duration(self):
        """Test CPT suggestions based on duration."""
        # 45-minute appointment should suggest 90834
        suggestions = CPTCatalog.suggest_codes(duration_minutes=45)
        assert len(suggestions) > 0
        assert any(s['code'] == '90834' for s in suggestions)
        
        # 60-minute appointment should suggest 90837
        suggestions = CPTCatalog.suggest_codes(duration_minutes=60)
        assert any(s['code'] == '90837' for s in suggestions)
        
        # 2-hour ABA session should suggest 97153
        suggestions = CPTCatalog.suggest_codes(duration_minutes=120)
        assert any(s['code'] == '97153' for s in suggestions)

    def test_cpt_catalog_suggest_by_specialty(self):
        """Test CPT suggestions based on provider specialty."""
        # ABA specialty
        suggestions = CPTCatalog.suggest_codes(specialty='aba')
        aba_codes = [s['code'] for s in suggestions]
        assert '97153' in aba_codes or '97155' in aba_codes
        
        # Mental health specialty
        suggestions = CPTCatalog.suggest_codes(specialty='mental_health')
        mh_codes = [s['code'] for s in suggestions]
        assert any(code.startswith('90') for code in mh_codes)

    def test_cpt_catalog_suggest_by_type(self):
        """Test CPT suggestions based on service type."""
        # Evaluation type (evaluation codes are initial_only, so pass is_initial=True)
        suggestions = CPTCatalog.suggest_codes(service_type='evaluation', is_initial=True)
        eval_codes = [s['code'] for s in suggestions]
        assert '90791' in eval_codes or '97151' in eval_codes

        # Group therapy type
        suggestions = CPTCatalog.suggest_codes(service_type='group_therapy')
        group_codes = [s['code'] for s in suggestions]
        assert '90853' in group_codes or '97154' in group_codes

    def test_cpt_catalog_initial_only(self):
        """Test that initial-only codes are filtered correctly."""
        # Initial visit
        suggestions = CPTCatalog.suggest_codes(is_initial=True, specialty='psychiatry')
        initial_codes = [s['code'] for s in suggestions if s.get('initial_only')]
        assert len(initial_codes) > 0
        
        # Follow-up visit should not include initial-only codes
        suggestions = CPTCatalog.suggest_codes(is_initial=False, specialty='psychiatry')
        for s in suggestions:
            assert not s.get('initial_only', False)

    def test_cpt_suggestion_api_endpoint(self, admin_client):
        """Test the CPT suggestion API endpoint."""
        url = reverse('cpt-suggestions')
        
        # Test with duration and specialty
        response = admin_client.get(url, {
            'duration': '45',
            'specialty': 'mental_health'
        })
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0
        
        # Verify response structure
        first = data[0]
        assert 'code' in first
        assert 'description' in first
        assert 'typical_units' in first
        assert 'duration_min' in first
        assert 'duration_max' in first
        
    def test_cpt_suggestion_api_invalid_duration(self, admin_client):
        """Test API handles invalid duration gracefully."""
        url = reverse('cpt-suggestions')
        response = admin_client.get(url, {'duration': 'abc'})
        assert response.status_code == 400
        assert 'Duration must be a number' in response.json()['detail']

    def test_cpt_suggestion_combined_filters(self, admin_client):
        """Test API with multiple filters combined."""
        url = reverse('cpt-suggestions')
        response = admin_client.get(url, {
            'specialty': 'aba',
            'duration': '240',  # 4 hours
            'service_type': 'direct_treatment',
            'is_initial': 'false'
        })
        assert response.status_code == 200
        data = response.json()
        
        # Should prioritize 97153 for long ABA sessions
        assert data[0]['code'] == '97153'
        assert data[0]['typical_units'] == 8


@pytest.mark.django_db
class TestModifierSuggestions:
    """Test BUILD 6.2: Auto-apply modifiers."""
    
    def test_telehealth_modifiers(self):
        """Test telehealth modifier suggestions."""
        modifiers = CPTCatalog.suggest_modifiers(
            code='90834',
            is_telehealth=True
        )
        assert '-95' in modifiers
        
        # Audio-only telehealth
        modifiers = CPTCatalog.suggest_modifiers(
            code='90834',
            is_telehealth=True,
            telehealth_type='audio_only'
        )
        assert '-93' in modifiers
    
    def test_therapy_modifiers(self):
        """Test therapy discipline modifiers."""
        # OT modifier
        modifiers = CPTCatalog.suggest_modifiers(
            code='97530',
            provider_type='ot'
        )
        assert 'GO' in modifiers
        
        # PT modifier
        modifiers = CPTCatalog.suggest_modifiers(
            code='97110',
            provider_type='pt'
        )
        assert 'GP' in modifiers
    
    def test_assistant_modifiers(self):
        """Test assistant modifiers."""
        # OT assistant
        modifiers = CPTCatalog.suggest_modifiers(
            code='97530',
            provider_type='ot',
            is_assistant=True
        )
        assert 'CO' in modifiers
        
        # PT assistant
        modifiers = CPTCatalog.suggest_modifiers(
            code='97110',
            provider_type='pt',
            is_assistant=True
        )
        assert 'CQ' in modifiers
    
    def test_modifier_api_endpoint(self, admin_client):
        """Test the modifier suggestion API endpoint."""
        url = reverse('modifier-suggestions')
        
        # Test telehealth modifier
        response = admin_client.get(url, {
            'code': '90834',
            'is_telehealth': 'true'
        })
        assert response.status_code == 200
        data = response.json()
        assert 'modifiers' in data
        assert '-95' in data['modifiers']
        
        # Test missing code
        response = admin_client.get(url)
        assert response.status_code == 400
