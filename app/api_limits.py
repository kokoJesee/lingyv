"""Bound wearable uploads before FastAPI JSON parsing; local single-process API."""
from starlette.responses import JSONResponse
from app.services.wearable_service import MAX_BYTES


class WearableUploadLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope.get('path', '').endswith('/wearables/import'):
            return await self.app(scope, receive, send)
        chunks, total = [], 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            body = message.get('body', b'')
            total += len(body)
            if total > MAX_BYTES:
                response = JSONResponse(status_code=413, content={'detail': {
                    'code': 'wearable_too_large', 'message': '导入文件超过 4 MiB。'}})
                return await response(scope, receive, send)
            chunks.append(body)
            if not message.get('more_body', False): break
        delivered = False
        async def replay():
            nonlocal delivered
            if delivered: return await receive()
            delivered = True
            return {'type':'http.request', 'body':b''.join(chunks), 'more_body':False}
        await self.app(scope, replay, send)
