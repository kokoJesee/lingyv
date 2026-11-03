class LLMError(Exception):
    """仅携带可展示的错误信息，不保留上游响应或密钥。"""

    def __init__(self, code: str, message: str, status_code: int = 502):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
