"""Safe, structured errors for a Registry tag-list request."""


class RegistryAuthenticationError(ValueError):
    """Configured credentials were rejected; no upstream body or secrets escape."""

    def __init__(self, status: int):
        self.code = "registry_authentication_failed" if status == 401 else "registry_access_denied"
        super().__init__(
            "Image registry authentication failed: invalid credential username or password."
            if status == 401
            else "Image registry access denied: check credential validity and repository read permissions."
        )


class RegistryTagListError(ValueError):
    """Keep upstream bodies, credentials and request URLs out of diagnostics."""

    DETAILS = {
        401: (
            "registry_authentication_failed",
            "Image registry authentication failed. Check the configured credentials.",
        ),
        403: (
            "registry_access_denied",
            "Image registry access denied. Check repository read permissions.",
        ),
        404: (
            "registry_repository_not_found",
            "Image repository not found or not visible. Check the image path; "
            "if the repository was renamed, build and push the image before fetching again.",
        ),
    }

    def __init__(self, status: int):
        self.code, message = self.DETAILS[status]
        super().__init__(message)
