"""最小 SQLAlchemy 垫片（仅测试用，覆盖本仓模型/服务实际使用的接口）。

本地环境无 pip 且无 SQLAlchemy，服务层集成测试通过本垫片 + 标准库
sqlite3 运行；容器镜像仍安装真实 SQLAlchemy 2.0，生产代码不依赖本文件。
覆盖：DeclarativeBase、Column 与常用类型、create_engine/metadata.create_all、
sessionmaker 会话、query(get/filter_by/order_by/limit/all/first/count)、
add/delete/commit，以及 order_by 所需的 Model.col 与 Model.col.desc()。
"""
import json
import sqlite3

_TYPES = {'Integer': 'INTEGER', 'String': 'VARCHAR', 'JSON': 'TEXT',
          'Text': 'TEXT', 'Float': 'REAL', 'Boolean': 'INTEGER'}


class _Type:
    def __init__(self, name):
        self.name = name


Integer = _Type('Integer')
String = lambda length=None: _Type('String')  # noqa: E731
JSON = _Type('JSON')
Text = _Type('Text')
Float = _Type('Float')
Boolean = _Type('Boolean')


class Column:
    def __init__(self, *args, **kwargs):
        self.type = args[0] if args else _Type('VARCHAR')
        self.name = None
        self.primary_key = kwargs.get('primary_key', False)
        self.unique = kwargs.get('unique', False)
        self.index = kwargs.get('index', False)
        self.nullable = kwargs.get('nullable', True)
        self.default = kwargs.get('default')


class _ColExpr:
    def __init__(self, name):
        self.name = name

    def desc(self):
        return _Order(self.name, True)


class _Order:
    def __init__(self, name, desc=False):
        self.name = name
        self.desc = desc


class _Meta(type):
    def __new__(mcs, name, bases, ns):
        columns = {}
        for attr, val in list(ns.items()):
            if isinstance(val, Column):
                val.name = attr
                columns[attr] = val
        cls = super().__new__(mcs, name, bases, ns)
        if columns:
            cls._columns = columns
            cls.__table__ = _Table(cls, columns)
            DeclarativeBase._models[cls.__tablename__] = cls
        return cls

    def __getattribute__(cls, name):
        val = super().__getattribute__(name)
        if isinstance(val, Column):
            return _ColExpr(name)
        return val


class DeclarativeBase(metaclass=_Meta):
    _models = {}

    def __init__(self, **kwargs):
        for name in self._columns:
            setattr(self, name, None)
        for k, v in kwargs.items():
            setattr(self, k, v)


class _Table:
    def __init__(self, model, columns):
        self.name = model.__tablename__
        self.columns = columns
        self.model = model

    @property
    def ddl(self):
        parts = []
        for name, col in self.columns.items():
            sql = _TYPES[col.type.name]
            if col.primary_key:
                sql += ' PRIMARY KEY AUTOINCREMENT'
            if col.unique:
                sql += ' UNIQUE'
            if not col.nullable and not col.primary_key:
                sql += ' NOT NULL'
            parts.append('"%s" %s' % (name, sql))
        return 'CREATE TABLE IF NOT EXISTS "%s" (%s)' % (self.name, ','.join(parts))


class _MetaData:
    def create_all(self, engine):
        for model in DeclarativeBase._models.values():
            engine.conn.execute(model.__table__.ddl)
        engine.conn.commit()


Base = DeclarativeBase
Base.metadata = _MetaData()


class Engine:
    _memory_index = 0

    def __init__(self, uri):
        # 共享内存库：同 uri 的所有会话访问同一份数据，持久到进程结束
        if ':memory:' in uri:
            Engine._memory_index += 1
            self.uri = 'file:shim_mem_%d?mode=memory&cache=shared' % Engine._memory_index
        else:
            self.uri = uri
        self.conn = sqlite3.connect(self.uri, check_same_thread=False, uri=True)
        self.conn.row_factory = sqlite3.Row

    def connect(self):
        class _C:
            def __enter__(self_inner):
                self_inner.c = sqlite3.connect(self.uri, uri=True)
                return self_inner.c

            def __exit__(self_inner, *exc):
                self_inner.c.close()

            def execute(self_inner, stmt, *a):
                return self_inner.c.execute(stmt, *a)
        return _C()


def create_engine(uri, **kwargs):
    return Engine(uri)


def sessionmaker(bind=None, **kwargs):
    engine = bind
    return lambda: _Session(engine)


def text(stmt):
    return stmt


class Query:
    def __init__(self, session, model):
        self.session = session
        self.model = model
        self._filters = {}
        self._order = None
        self._limit = None

    def filter_by(self, **kwargs):
        self._filters.update(kwargs)
        return self

    def order_by(self, expr):
        self._order = expr if isinstance(expr, _Order) else _Order(expr.name)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _load(self):
        table = self.model.__table__
        pk_col = next(c for c in table.columns.values() if c.primary_key)
        cur = self.session.conn.execute('SELECT * FROM "%s"' % table.name)
        objects = []
        for row in cur.fetchall():
            if not all(row[k] == v for k, v in self._filters.items()):
                continue
            key = (table.name, row[pk_col.name])
            obj = self.session._identity.get(key)
            if obj is None:
                obj = self.model()
                for name, col in table.columns.items():
                    v = row[name]
                    if col.type.name == 'JSON' and v is not None:
                        v = json.loads(v)
                    setattr(obj, name, v)
                self.session._identity[key] = obj
            objects.append(obj)
        if self._order:
            objects.sort(
                key=lambda o: (getattr(o, self._order.name) is None,
                               getattr(o, self._order.name)),
                reverse=self._order.desc)
        if self._limit:
            objects = objects[:self._limit]
        return objects

    def all(self):
        return self._load()

    def first(self):
        rows = self._load()
        return rows[0] if rows else None

    def count(self):
        return len(self._load())


class _Session:
    def __init__(self, engine):
        self.engine = engine
        self.conn = sqlite3.connect(engine.uri, check_same_thread=False, uri=True)
        self.conn.row_factory = sqlite3.Row
        self._pending_add = []
        self._pending_del = []
        self._identity = {}

    def add(self, obj):
        self._pending_add.append(obj)

    def delete(self, obj):
        self._pending_del.append(obj)

    def get(self, model, pk):
        key = (model.__tablename__, pk)
        if key in self._identity:
            return self._identity[key]
        table = model.__table__
        pk_col = next(c for c in table.columns.values() if c.primary_key)
        cur = self.conn.execute(
            'SELECT * FROM "%s" WHERE "%s"=?' % (table.name, pk_col.name), (pk,))
        row = cur.fetchone()
        if not row:
            return None
        obj = model()
        for name, col in table.columns.items():
            v = row[name]
            if col.type.name == 'JSON' and v is not None:
                v = json.loads(v)
            setattr(obj, name, v)
        self._identity[key] = obj
        return obj

    def query(self, model):
        return Query(self, model)

    def commit(self):
        for obj in self._pending_del:
            table = type(obj).__table__
            pk_col = next(c for c in table.columns.values() if c.primary_key)
            pk_val = getattr(obj, pk_col.name)
            self.conn.execute('DELETE FROM "%s" WHERE "%s"=?'
                              % (table.name, pk_col.name), (pk_val,))
            self._identity.pop((table.name, pk_val), None)
        for obj in self._pending_add:
            table = type(obj).__table__
            pk_col = next(c for c in table.columns.values() if c.primary_key)
            pk_val = getattr(obj, pk_col.name)
            existing = None
            if pk_val is not None:
                cur = self.conn.execute(
                    'SELECT 1 FROM "%s" WHERE "%s"=?' % (table.name, pk_col.name),
                    (pk_val,))
                existing = cur.fetchone()
            if pk_val is None and 'id' in table.columns:
                cur = self.conn.execute('SELECT MAX(id) FROM "%s"' % table.name)
                obj.id = (cur.fetchone()[0] or 0) + 1
                pk_val = obj.id
            cols = list(table.columns)
            values = []
            for name in cols:
                v = getattr(obj, name, None)
                if table.columns[name].type.name == 'JSON' and v is not None:
                    v = json.dumps(v, ensure_ascii=False, default=str)
                values.append(v)
            if existing:
                sets = ','.join('"%s"=?' % c for c in cols)
                self.conn.execute(
                    'UPDATE "%s" SET %s WHERE "%s"=?'
                    % (table.name, sets, pk_col.name), values + [getattr(obj, pk_col.name)])
            else:
                self.conn.execute(
                    'INSERT INTO "%s" (%s) VALUES (%s)'
                    % (table.name, ','.join('"%s"' % c for c in cols),
                       ','.join('?' for _ in cols)), values)
            self._identity[(table.name, getattr(obj, pk_col.name))] = obj
        self.conn.commit()
        self._pending_add.clear()
        self._pending_del.clear()

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def func():
    raise NotImplementedError
