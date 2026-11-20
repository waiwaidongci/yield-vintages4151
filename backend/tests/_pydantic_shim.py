"""最小 pydantic 垫片（仅测试用，支持本仓 schemas 的 BaseModel/Field 用法）。

容器镜像安装真实 pydantic；本地无 pip 时服务层测试用本垫片完成构造与校验：
必填、min/max 长度、数值 ge/le、None 默认。
"""


class _Missing:
    pass


MISSING = _Missing()


class FieldInfo:
    def __init__(self, default=MISSING, **constraints):
        self.default = default
        self.constraints = constraints


def Field(default=MISSING, **constraints):
    return FieldInfo(default, **constraints)


class BaseModel:
    def __init__(self, **data):
        annotations = {}
        for cls in reversed(type(self).__mro__):
            annotations.update(getattr(cls, '__annotations__', {}))
        for name in annotations:
            info = getattr(type(self), name, MISSING)
            if name in data:
                value = data[name]
            elif isinstance(info, FieldInfo):
                if info.default is MISSING:
                    raise ValueError('%s 为必填项' % name)
                value = info.default
            elif info is MISSING:
                raise ValueError('%s 为必填项' % name)
            else:
                value = info
            self._validate(name, value, info if isinstance(info, FieldInfo) else None)
            setattr(self, name, value)

    @staticmethod
    def _validate(name, value, info):
        if not info or value is None:
            return
        c = info.constraints
        if 'min_length' in c and isinstance(value, str) and len(value) < c['min_length']:
            raise ValueError('%s 长度不足' % name)
        if 'max_length' in c and isinstance(value, str) and len(value) > c['max_length']:
            raise ValueError('%s 长度超限' % name)
        if 'ge' in c and isinstance(value, (int, float)) and value < c['ge']:
            raise ValueError('%s 小于下限' % name)
        if 'le' in c and isinstance(value, (int, float)) and value > c['le']:
            raise ValueError('%s 大于上限' % name)

    def dict(self):
        return dict(self.__dict__)
