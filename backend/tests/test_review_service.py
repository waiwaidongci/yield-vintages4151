"""回查服务层集成测试：内存 SQLite + 桩 Redis，无需 FastAPI / MySQL / 真实 Redis。

运行：python3 -m unittest -v tests.test_review_service
"""
import os
import sys
import types
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.dirname(__file__))

# 在导入业务模块前桩掉 redis 与 sqlalchemy（本地无 pip，容器内用真实依赖）
_fake_redis = types.ModuleType('redis')


class _FakeRedis:
    def __init__(self):
        self.queues = {}

    def rpush(self, key, value):
        self.queues.setdefault(key, []).append(value)

    def lpop(self, key):
        return self.queues[key].pop(0) if self.queues.get(key) else None

    def ping(self):
        return True


_FAKE = _FakeRedis()
# from_url 必须始终返回测试持有的同一单例，review 服务在调用时才取连接
_fake_redis.Redis = types.SimpleNamespace(from_url=lambda url: _FAKE)
sys.modules['redis'] = _fake_redis

import _sa_shim  # noqa: E402
sys.modules.setdefault('sqlalchemy', _sa_shim)
sys.modules.setdefault('sqlalchemy.orm', _sa_shim)

import _pydantic_shim  # noqa: E402
sys.modules.setdefault('pydantic', _pydantic_shim)

import _fastapi_shim  # noqa: E402
sys.modules.setdefault('fastapi', _fastapi_shim)
sys.modules.setdefault('fastapi.responses', _fastapi_shim)

# settings 在 import 时读取 /app/project.json，先桩入最小配置
_settings = types.ModuleType('src.config.settings')
_settings.CONFIG = {'fields': ['period', 'department', 'shift', 'input',
                               'first_pass', 'reworked_good', 'scrap']}
_settings.DATABASE_URL = 'sqlite:///:memory:'
_settings.REDIS_URL = 'redis://fake'
_settings.SECRET = 'test-secret'
sys.modules['src.config.settings'] = _settings

from src.config.database import Base, engine  # noqa: E402
import src.models.identity  # noqa: E402,F401
import src.models.source  # noqa: E402,F401
import src.models.run  # noqa: E402,F401
import src.models.review  # noqa: E402,F401
from src.models.source import Source, Policy  # noqa: E402
from src.models.review import ReviewJob  # noqa: E402
from src.schemas.requests import ReviewInput  # noqa: E402
from src.services.review import create_review, run_review  # noqa: E402

Base.metadata.create_all(engine)
from src.config.database import Session  # noqa: E402

POLICY = {'version': '一次通过-v1', 'numerator': 'first_pass',
          'denominator': 'input', 'description': '测试口径'}
USER = SimpleNamespace(email='analyst@demo.local', role='analyst')


def _seed():
    db = Session()
    if not db.query(Policy).count():
        db.add(Policy(version=POLICY['version'], data=POLICY))
        db.commit()
    pid = db.query(Policy).first().id
    # 试制线A：30 个生产日 × 两班，第 13 天起良率阶跃下降
    for t in range(30):
        rate = 0.95 if t < 12 else 0.70
        for shift in ('早班', '晚班'):
            data = {'period': '2026-11-%02d' % (t + 1), 'department': '试制线A',
                    'shift': shift, 'input': 100,
                    'first_pass': int(100 * rate),
                    'reworked_good': 5, 'scrap': 100 - int(100 * rate) - 5}
            db.add(Source(natural_key='t-%d-%s' % (t, shift), data=data))
    # 试制线B：不应进入 A 的回查
    db.add(Source(natural_key='b-1', data={
        'period': '2026-11-01', 'department': '试制线B', 'shift': '早班',
        'input': 100, 'first_pass': 50, 'reworked_good': 5, 'scrap': 45}))
    db.commit()
    db.close()
    return pid


class ReviewServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pid = _seed()

    def setUp(self):
        _FAKE.queues.clear()
        from src.services import review as review_service
        review_service.TIME_BUDGET = 30.0
        # 同请求键全局唯一：每个用例从空任务表开始，避免复用路径干扰队列断言
        db = Session()
        db.conn.execute('DELETE FROM yield_review_jobs')
        db.conn.commit()
        db.close()

    def tearDown(self):
        from src.services import review as review_service
        review_service.TIME_BUDGET = 30.0

    def _body(self, **over):
        params = dict(name='十一月回查', department='试制线A', policy_id=self.pid,
                      penalty=10.0, date_from=None, date_to=None)
        params.update(over)
        return ReviewInput(**params)

    def test_create_snapshots_and_enqueues(self):
        db = Session()
        job = create_review(db, USER, self._body())
        self.assertEqual(job.status, 'queued')
        self.assertEqual(_FAKE.queues['review-jobs'], [job.id])
        # 快照只含试制线A
        self.assertTrue(all(s['department'] == '试制线A' for s in job.snapshot['sources']))
        self.assertEqual(len(job.snapshot['sources']), 60)
        self.assertEqual(job.snapshot['policy']['version'], POLICY['version'])
        db.close()

    def test_duplicate_request_returns_same_job(self):
        from src.services.review import QUEUE
        db = Session()
        before = len(_FAKE.queues.get(QUEUE, []))
        first = create_review(db, USER, self._body())
        again = create_review(db, USER, self._body())
        self.assertEqual(first.id, again.id)
        # 不重复入队、不重复给结论：队列只增加一次
        self.assertEqual(len(_FAKE.queues[QUEUE]), before + 1)
        db.close()

    def test_run_produces_segment_tables(self):
        db = Session()
        job = create_review(db, USER, self._body())
        run_review(db, job.id)
        done = db.get(ReviewJob, job.id)
        self.assertEqual(done.status, 'completed')
        self.assertTrue(done.result['proven_optimal'])
        self.assertEqual(done.result['cut_day'], '2026-11-13')
        self.assertEqual({r['shift'] for r in done.result['left']}, {'早班', '晚班'})
        for r in done.result['left'] + done.result['right']:
            self.assertEqual(set(r), {'shift', 'num', 'den', 'rate'})
        db.close()

    def test_resume_after_interruption_continues(self):
        from src.services import review as review_service
        from src.services.review_solver import solve
        db = Session()
        job = create_review(db, USER, self._body(name='中断恢复回查', penalty=3.0))
        # 模拟 worker 崩溃：求解器只扫描部分切点后的检查点落库，任务停在 running
        records = review_service._to_records(job.snapshot['sources'],
                                             job.snapshot['policy'])
        partial = solve(records, penalty=3.0, time_budget=30.0,
                        checkpoint={'scanned': [7, 8, 9], 'best_cut': None,
                                    'best_score': -1e18, 'best_winner': 'none',
                                    'penalty': 3.0, 'evaluated_cuts': 0,
                                    'eligible_cuts': 0})
        self.assertIn(10, partial['checkpoint']['scanned'])
        job.status = 'failed'
        job.checkpoint = partial['checkpoint']
        db.commit()
        # 恢复这次回查：沿用同一切点检查点续算，已扫描切点不重复评估
        run_review(db, job.id)
        done = db.get(ReviewJob, job.id)
        self.assertEqual(done.status, 'completed')
        self.assertIn(7, done.checkpoint['scanned'])
        self.assertTrue(done.result['proven_optimal'])
        db.close()

    def test_rejected_when_not_proven(self):
        db = Session()
        job = create_review(db, USER, self._body(name='超时拒绝回查'))
        # 零预算：首个切点前即超时，拒绝发布且保留可恢复检查点
        from src.services import review as review_service
        review_service.TIME_BUDGET = 0.0
        run_review(db, job.id)
        done = db.get(ReviewJob, job.id)
        self.assertEqual(done.status, 'rejected')
        self.assertIn('拒绝发布', done.error)
        self.assertFalse(done.result['proven_optimal'])
        self.assertIn('best_effort', done.result)
        self.assertIsNotNone(done.checkpoint)
        # 恢复续算后可完成
        review_service.TIME_BUDGET = 30.0
        run_review(db, job.id)
        self.assertEqual(db.get(ReviewJob, job.id).status, 'completed')
        db.close()

    def test_date_range_filter(self):
        db = Session()
        # 仅取前 10 天：不足 14 个生产日，无法成段
        job = create_review(db, USER, self._body(
            name='短窗口回查', date_from='2026-11-01', date_to='2026-11-10'))
        run_review(db, job.id)
        done = db.get(ReviewJob, job.id)
        self.assertEqual(done.status, 'completed')
        self.assertEqual(done.result['status'], 'infeasible')
        self.assertEqual(done.result['day_count'], 10)
        db.close()

    def test_empty_selection_rejected(self):
        db = Session()
        with self.assertRaises(Exception):
            create_review(db, USER, self._body(department='不存在的线'))
        db.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
