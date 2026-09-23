class SourceError(Exception):
    def __init__(self, code: str, message: str, method: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.method = method

    @property
    def status(self) -> str:
        if self.code == "TIMEOUT": return "timeout"
        if self.code == "HTTP_401" or self.code == "AUTH_REQUIRED": return "authentication_required"
        if self.code == "HTTP_403" or self.code == "BLOCKED": return "blocked"
        if self.code == "HTTP_429" or self.code == "RATE_LIMITED": return "rate_limited"
        if self.code == "API_KEY_REQUIRED": return "api_key_required"
        if self.code == "SOURCE_DISABLED": return "unsupported"
        if self.code.startswith("PARSE"): return "parse_error"
        if self.code == "UNSUPPORTED": return "unsupported"
        return "network_error"
