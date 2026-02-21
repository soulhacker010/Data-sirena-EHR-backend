"""
Custom JWT authentication that checks is_active on every request.

FIX #7: Default simplejwt only checks is_active at login time.
If an admin deactivates a user, their existing JWT tokens remain valid
until expiration (15 minutes). This custom class re-checks is_active
on every authenticated request, immediately locking out deactivated users.
"""
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed


class ActiveUserJWTAuthentication(JWTAuthentication):
    """
    Extends simplejwt to check user.is_active on every request.

    Without this, a deactivated user's access token continues working
    until it expires (currently 15 minutes). With this, deactivation
    takes effect immediately.
    """

    def get_user(self, validated_token):
        user = super().get_user(validated_token)

        if not user.is_active:
            raise AuthenticationFailed(
                'Account has been deactivated. Please contact your administrator.',
                code='user_inactive',
            )

        return user
