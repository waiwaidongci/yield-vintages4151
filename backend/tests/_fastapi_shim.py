"""最小 fastapi 垫片（仅测试用，覆盖本仓服务/路由使用的接口）。

容器镜像安装真实 FastAPI；本地无 pip 时服务层测试用本垫片，
仅提供 HTTPException、APIRouter、Depends、Header 与响应类的占位实现。
"""


class HTTPException(Exception):
    def __init__(self, status_code=400, detail='错误'):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class APIRouter:
    def __init__(self, *args, **kwargs):
        self.routes = []

    def _register(self, method, path, **kwargs):
        def deco(fn):
            self.routes.append((method, path, fn))
            return fn
        return deco

    def get(self, path, **kwargs):
        return self._register('GET', path, **kwargs)

    def post(self, path, **kwargs):
        return self._register('POST', path, **kwargs)

    def put(self, path, **kwargs):
        return self._register('PUT', path, **kwargs)

    def delete(self, path, **kwargs):
        return self._register('DELETE', path, **kwargs)


def Depends(fn=None):
    return fn


def Header(default='', **kwargs):
    return default


class Response:
    def __init__(self, content='', media_type=None, headers=None):
        self.content = content
        self.media_type = media_type
        self.headers = headers or {}
