"""
Custom exception handler for consistent API error responses.

Every error response follows this format:
{
    "error": true,
    "message": "Human-readable message",
    "errors": { "field_name": ["error details"] }  // optional
}
"""
from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status


def custom_exception_handler(exc, context):
    """Custom exception handler that wraps DRF errors in a consistent format."""
    response = exception_handler(exc, context)

    if response is not None:
        custom_data = {
            'error': True,
            'message': _get_error_message(response),
        }

        # Include field-level errors if present
        if isinstance(response.data, dict) and 'detail' not in response.data:
            custom_data['errors'] = response.data
        elif isinstance(response.data, list):
            custom_data['errors'] = {'non_field_errors': response.data}

        response.data = custom_data

    return response


def _get_error_message(response):
    """Extract a human-readable message from the response data."""
    if isinstance(response.data, dict):
        if 'detail' in response.data:
            return str(response.data['detail'])
        # Get first field error
        for field, errors in response.data.items():
            if isinstance(errors, list) and errors:
                return f"{field}: {errors[0]}"
            elif isinstance(errors, str):
                return f"{field}: {errors}"

    status_messages = {
        400: 'Bad request',
        401: 'Authentication required',
        403: 'Permission denied',
        404: 'Not found',
        405: 'Method not allowed',
        500: 'Internal server error',
    }
    return status_messages.get(response.status_code, 'An error occurred')
