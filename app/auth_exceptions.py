class AuthenticationRequired(Exception):
    """Raised when authentication is required but not provided."""

    def __init__(self, redirect_url: str = "/auth/login"):
        self.redirect_url = redirect_url
        super().__init__("Authentication required")
