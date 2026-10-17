"""Public generation errors never include provider credentials or raw responses."""


class GenerationError(Exception):
    def __init__(self, message: str, status_code: int = 502, **details):
        super().__init__(message)
        self.status_code = status_code
        self.detail = {"message": message, **details}
