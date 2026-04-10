"""
Comprehensive CPT code catalog with metadata for auto-suggestion.
BUILD 6.1: Auto-suggest CPT codes based on specialty, duration, and type.
BUILD 6.2: Auto-apply modifiers based on service type and delivery method.
"""
from typing import Dict, List, Any, Optional
from datetime import timedelta


class CPTCatalog:
    """CPT code catalog with rich metadata for intelligent suggestions."""
    
    CODES: Dict[str, Dict[str, Any]] = {
        # Psychotherapy Services (90xxx)
        '90791': {
            'description': 'Psychiatric diagnostic evaluation',
            'specialty': ['psychiatry', 'mental_health'],
            'duration_min': 60,
            'duration_max': 90,
            'type': 'evaluation',
            'typical_units': 1,
            'modifiers': ['telehealth'],
            'initial_only': True
        },
        '90792': {
            'description': 'Psychiatric diagnostic evaluation with medical services',
            'specialty': ['psychiatry'],
            'duration_min': 60,
            'duration_max': 90,
            'type': 'evaluation',
            'typical_units': 1,
            'modifiers': ['telehealth'],
            'initial_only': True
        },
        '90832': {
            'description': 'Psychotherapy, 30 minutes',
            'specialty': ['psychiatry', 'mental_health', 'psychology'],
            'duration_min': 16,
            'duration_max': 37,
            'type': 'individual_therapy',
            'typical_units': 1,
            'modifiers': ['telehealth', 'therapy']
        },
        '90834': {
            'description': 'Psychotherapy, 45 minutes',
            'specialty': ['psychiatry', 'mental_health', 'psychology'],
            'duration_min': 38,
            'duration_max': 52,
            'type': 'individual_therapy',
            'typical_units': 1,
            'modifiers': ['telehealth', 'therapy']
        },
        '90837': {
            'description': 'Psychotherapy, 60 minutes',
            'specialty': ['psychiatry', 'mental_health', 'psychology'],
            'duration_min': 53,
            'duration_max': 75,
            'type': 'individual_therapy',
            'typical_units': 1,
            'modifiers': ['telehealth', 'therapy']
        },
        '90846': {
            'description': 'Family psychotherapy without patient present',
            'specialty': ['psychiatry', 'mental_health', 'psychology'],
            'duration_min': 45,
            'duration_max': 60,
            'type': 'family_therapy',
            'typical_units': 1,
            'modifiers': ['telehealth', 'therapy']
        },
        '90847': {
            'description': 'Family psychotherapy with patient present',
            'specialty': ['psychiatry', 'mental_health', 'psychology'],
            'duration_min': 50,
            'duration_max': 60,
            'type': 'family_therapy',
            'typical_units': 1,
            'modifiers': ['telehealth', 'therapy']
        },
        '90853': {
            'description': 'Group psychotherapy',
            'specialty': ['psychiatry', 'mental_health', 'psychology'],
            'duration_min': 45,
            'duration_max': 90,
            'type': 'group_therapy',
            'typical_units': 1,
            'modifiers': ['therapy']
        },
        
        # ABA/Behavioral Services (97xxx)
        '97151': {
            'description': 'Behavior identification assessment',
            'specialty': ['aba', 'bcba'],
            'duration_min': 120,
            'duration_max': 240,
            'type': 'assessment',
            'typical_units': 4,
            'modifiers': [],
            'initial_only': True
        },
        '97152': {
            'description': 'Behavior identification supporting assessment',
            'specialty': ['aba', 'bcba'],
            'duration_min': 60,
            'duration_max': 120,
            'type': 'assessment',
            'typical_units': 2,
            'modifiers': ['assistant']
        },
        '97153': {
            'description': 'Adaptive behavior treatment by protocol',
            'specialty': ['aba', 'rbt', 'behavior_tech'],
            'duration_min': 60,
            'duration_max': 480,
            'type': 'direct_treatment',
            'typical_units': 8,
            'modifiers': ['assistant']
        },
        '97154': {
            'description': 'Group adaptive behavior treatment by protocol',
            'specialty': ['aba', 'rbt', 'behavior_tech'],
            'duration_min': 60,
            'duration_max': 240,
            'type': 'group_treatment',
            'typical_units': 4,
            'modifiers': ['assistant']
        },
        '97155': {
            'description': 'Adaptive behavior treatment with protocol modification',
            'specialty': ['aba', 'bcba'],
            'duration_min': 60,
            'duration_max': 240,
            'type': 'supervision',
            'typical_units': 4,
            'modifiers': []
        },
        '97156': {
            'description': 'Family adaptive behavior treatment guidance',
            'specialty': ['aba', 'bcba'],
            'duration_min': 60,
            'duration_max': 120,
            'type': 'family_training',
            'typical_units': 2,
            'modifiers': []
        },
        '97157': {
            'description': 'Multiple-family group adaptive behavior treatment',
            'specialty': ['aba', 'bcba'],
            'duration_min': 60,
            'duration_max': 120,
            'type': 'group_family_training',
            'typical_units': 2,
            'modifiers': []
        },
        '97158': {
            'description': 'Group adaptive behavior treatment with protocol modification',
            'specialty': ['aba', 'bcba'],
            'duration_min': 60,
            'duration_max': 120,
            'type': 'group_supervision',
            'typical_units': 2,
            'modifiers': []
        },
        
        # Speech Therapy (92xxx)
        '92507': {
            'description': 'Speech therapy, individual',
            'specialty': ['speech', 'slp'],
            'duration_min': 30,
            'duration_max': 60,
            'type': 'individual_therapy',
            'typical_units': 1,
            'modifiers': ['therapy']
        },
        '92508': {
            'description': 'Speech therapy, group',
            'specialty': ['speech', 'slp'],
            'duration_min': 30,
            'duration_max': 60,
            'type': 'group_therapy',
            'typical_units': 1,
            'modifiers': ['therapy']
        },
        
        # Occupational Therapy (97xxx)
        '97165': {
            'description': 'OT evaluation, low complexity',
            'specialty': ['ot'],
            'duration_min': 30,
            'duration_max': 45,
            'type': 'evaluation',
            'typical_units': 1,
            'modifiers': ['therapy'],
            'initial_only': True
        },
        '97166': {
            'description': 'OT evaluation, moderate complexity',
            'specialty': ['ot'],
            'duration_min': 45,
            'duration_max': 60,
            'type': 'evaluation',
            'typical_units': 1,
            'modifiers': ['therapy'],
            'initial_only': True
        },
        '97167': {
            'description': 'OT evaluation, high complexity',
            'specialty': ['ot'],
            'duration_min': 60,
            'duration_max': 90,
            'type': 'evaluation',
            'typical_units': 1,
            'modifiers': ['therapy'],
            'initial_only': True
        },
        '97530': {
            'description': 'Therapeutic activities',
            'specialty': ['ot', 'pt'],
            'duration_min': 15,
            'duration_max': 60,
            'type': 'individual_therapy',
            'typical_units': 1,
            'modifiers': ['therapy', 'assistant']
        },
        
        # Physical Therapy (97xxx)
        '97161': {
            'description': 'PT evaluation, low complexity',
            'specialty': ['pt'],
            'duration_min': 20,
            'duration_max': 30,
            'type': 'evaluation',
            'typical_units': 1,
            'modifiers': ['therapy'],
            'initial_only': True
        },
        '97162': {
            'description': 'PT evaluation, moderate complexity',
            'specialty': ['pt'],
            'duration_min': 30,
            'duration_max': 45,
            'type': 'evaluation',
            'typical_units': 1,
            'modifiers': ['therapy'],
            'initial_only': True
        },
        '97163': {
            'description': 'PT evaluation, high complexity',
            'specialty': ['pt'],
            'duration_min': 45,
            'duration_max': 60,
            'type': 'evaluation',
            'typical_units': 1,
            'modifiers': ['therapy'],
            'initial_only': True
        },
        '97110': {
            'description': 'Therapeutic exercises',
            'specialty': ['pt', 'ot'],
            'duration_min': 15,
            'duration_max': 30,
            'type': 'individual_therapy',
            'typical_units': 1,
            'modifiers': ['therapy', 'assistant']
        },
    }
    
    @classmethod
    def suggest_codes(
        cls, 
        specialty: Optional[str] = None,
        duration_minutes: Optional[int] = None,
        service_type: Optional[str] = None,
        is_initial: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Suggest CPT codes based on criteria.
        
        Args:
            specialty: Provider specialty (e.g., 'aba', 'psychiatry', 'pt')
            duration_minutes: Appointment duration in minutes
            service_type: Type of service (e.g., 'evaluation', 'individual_therapy')
            is_initial: Whether this is an initial visit
            
        Returns:
            List of matching CPT codes with metadata, sorted by relevance
        """
        matches = []
        
        for code, meta in cls.CODES.items():
            score = 0
            
            # Check specialty match
            if specialty and specialty.lower() in meta.get('specialty', []):
                score += 3
            
            # Check duration match
            if duration_minutes:
                min_dur = meta.get('duration_min', 0)
                max_dur = meta.get('duration_max', 999)
                if min_dur <= duration_minutes <= max_dur:
                    score += 2
                elif abs(duration_minutes - min_dur) <= 15 or abs(duration_minutes - max_dur) <= 15:
                    score += 1
            
            # Check service type match
            if service_type and service_type == meta.get('type'):
                score += 2
            
            # Check initial visit
            if is_initial and meta.get('initial_only'):
                score += 1
            elif not is_initial and meta.get('initial_only'):
                continue  # Skip initial-only codes for follow-ups
            
            if score > 0:
                matches.append({
                    'code': code,
                    'score': score,
                    **meta
                })
        
        # Sort by score descending
        matches.sort(key=lambda x: x['score'], reverse=True)
        
        # Return top 5 matches
        return matches[:5]
    
    @classmethod
    def get_code(cls, code: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a specific CPT code."""
        if code in cls.CODES:
            return {'code': code, **cls.CODES[code]}
        return None
    
    # BUILD 6.2: Modifier constants
    TELEHEALTH_MODIFIERS = {
        'default': '-95',      # Synchronous telemedicine via real-time audio/video
        'audio_only': '-93',   # Telephone/audio-only 
        'store_forward': '-GQ', # Asynchronous telecommunications
        'cms': '-GT',          # CMS telehealth modifier
    }
    
    THERAPY_MODIFIERS = {
        'ot': 'GO',     # Occupational therapy
        'pt': 'GP',     # Physical therapy 
        'st': 'GN',     # Speech therapy
    }
    
    ASSISTANT_MODIFIERS = {
        'ot_assistant': 'CO',  # OT assistant
        'pt_assistant': 'CQ',  # PT assistant
    }
    
    @classmethod
    def suggest_modifiers(
        cls,
        code: str,
        is_telehealth: bool = False,
        telehealth_type: str = 'default',
        provider_type: Optional[str] = None,
        is_assistant: bool = False
    ) -> List[str]:
        """
        Suggest appropriate modifiers for a CPT code.
        
        Args:
            code: CPT code
            is_telehealth: Whether service is delivered via telehealth
            telehealth_type: Type of telehealth (default, audio_only, store_forward, cms)
            provider_type: Provider specialty/type (ot, pt, st)
            is_assistant: Whether provider is an assistant
            
        Returns:
            List of suggested modifier codes
        """
        modifiers = []
        code_meta = cls.get_code(code)
        if not code_meta:
            return modifiers
        
        # Check if code supports telehealth
        if is_telehealth and 'telehealth' in code_meta.get('modifiers', []):
            modifier = cls.TELEHEALTH_MODIFIERS.get(telehealth_type, '-95')
            modifiers.append(modifier)
        
        # Check if code supports therapy modifiers
        if provider_type and 'therapy' in code_meta.get('modifiers', []):
            if provider_type in cls.THERAPY_MODIFIERS:
                modifiers.append(cls.THERAPY_MODIFIERS[provider_type])
        
        # Check if code supports assistant modifiers  
        if is_assistant and 'assistant' in code_meta.get('modifiers', []):
            if provider_type == 'ot':
                modifiers.append(cls.ASSISTANT_MODIFIERS['ot_assistant'])
            elif provider_type == 'pt':
                modifiers.append(cls.ASSISTANT_MODIFIERS['pt_assistant'])
        
        return modifiers
