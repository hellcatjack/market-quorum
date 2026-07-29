class IdentityError(Exception):
    def __init__(self, code: str, status_code: int, message: str):
        self.code = code
        self.status_code = status_code
        self.message = message
        super().__init__(message)


def identity_error(code: str) -> IdentityError:
    definitions = {
        "username_conflict": (409, "The username is already in use"),
        "email_conflict": (409, "The email address is already in use"),
        "user_not_found": (404, "The user was not found"),
        "identity_role_invalid": (409, "The identity has an invalid formal role assignment"),
        "identity_provider_forbidden": (503, "Identity management is not configured correctly"),
        "identity_provider_unavailable": (503, "The identity provider is temporarily unavailable"),
        "identity_sync_pending": (503, "The identity changed but local synchronization is pending"),
        "self_admin_change_forbidden": (409, "The current administrator cannot remove own access"),
        "last_admin_protected": (409, "The final enabled administrator is protected"),
    }
    status_code, message = definitions[code]
    return IdentityError(code, status_code, message)
