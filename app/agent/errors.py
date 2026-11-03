class RuntimeFailure(Exception):
    """可向调用者展示的运行错误；不包含对话或上游错误正文。"""

    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
