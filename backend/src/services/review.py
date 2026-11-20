"""良率回查的发起、执行与恢复。

回查按受理时的底稿与口径冻结快照执行；同一选择条件（线、日期、口径、罚分）
的重复请求返回同一任务，不重复给结论。worker 中断后凭 checkpoint 续算，
未在预算内证明最优时落 rejected，普通核算（yield_runs）不受影响。
"""
import hashlib
import json
import time

from fastapi import HTTPException

from src.models.source import Policy, Source
from src.models.review import ReviewJob
from src.services.audit import log
from src.services.validation import validate_source, validate_policy
from src.services.review_solver import solve, DEFAULT_TIME_BUDGET
from src.config.settings import REDIS_URL
import redis

QUEUE = 'review-jobs'
# 模块级预算，测试可 monkeypatch；生产固定为求解器默认 30 秒
TIME_BUDGET = DEFAULT_TIME_BUDGET


def _select_sources(db, department, date_from, date_to):
    chosen = []
    for obj in db.query(Source).order_by(Source.id).all():
        data = obj.data
        if data.get('department') != department:
            continue
        day = data.get('period', '')
        if date_from and day < date_from:
            continue
        if date_to and day > date_to:
            continue
        validate_source(data)
        chosen.append(data)
    return chosen


def _to_records(sources, policy):
    """按冻结口径把底稿折算成 (日期,班次) 的分子分母并同日同班合并。"""
    records = []
    for s in sources:
        if policy['numerator'] == 'first_pass':
            num = s['first_pass']
        else:
            num = s['first_pass'] + s['reworked_good']
        den = s['input'] if policy['denominator'] == 'input' else s['input'] - s['scrap']
        if den <= 0:
            raise ValueError('口径分母为0：' + s['department'] + ' ' + s['period'])
        records.append({'day': s['period'], 'shift': s['shift'], 'num': num, 'den': den})
    return records


def _request_key(department, policy_id, date_from, date_to, penalty):
    raw = json.dumps([department, policy_id, date_from or '', date_to or '', round(float(penalty), 6)],
                     sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def create_review(db, user, body):
    policy = db.get(Policy, body.policy_id)
    if not policy:
        raise HTTPException(404, '口径版本不存在')
    validate_policy(policy.data)
    if body.date_from and body.date_to and body.date_from > body.date_to:
        raise HTTPException(422, '起始日期不能晚于结束日期')
    sources = _select_sources(db, body.department, body.date_from, body.date_to)
    if not sources:
        raise HTTPException(422, '所选试制线与日期范围内没有底稿')
    key = _request_key(body.department, body.policy_id, body.date_from, body.date_to, body.penalty)
    # 重试同一回查不重复给结论：同键任务直接返回
    existing = db.query(ReviewJob).filter_by(request_key=key).first()
    if existing:
        log(db, user.email, 'review.reuse', existing.id)
        db.commit()
        return existing
    snapshot = {'policy': policy.data, 'sources': sources,
                'department': body.department,
                'date_from': body.date_from, 'date_to': body.date_to}
    checksum = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    now = time.time()
    obj = ReviewJob(name=body.name, status='queued', department=body.department,
                    policy_id=body.policy_id, date_from=body.date_from, date_to=body.date_to,
                    penalty=float(body.penalty), request_key=key, checksum=checksum,
                    snapshot=snapshot, result=None, checkpoint=None, error=None,
                    created=now, updated=now, created_by=user.email)
    db.add(obj)
    log(db, user.email, 'review.create', body.name)
    db.commit()
    redis.Redis.from_url(REDIS_URL).rpush(QUEUE, obj.id)
    return obj


def run_review(db, job_id):
    """worker 调用：含断点恢复与超时拒绝。返回落库后的任务对象。

    queued/running 为正常领取；rejected 为超时未证最优后的恢复续算，
    检查点保证已扫描切点不重复评估；failed 为数据异常，修正后也允许重跑。
    """
    job = db.get(ReviewJob, job_id)
    if not job or job.status not in ('queued', 'running', 'rejected', 'failed'):
        return job
    job.status = 'running'
    job.updated = time.time()
    db.commit()
    try:
        records = _to_records(job.snapshot['sources'], job.snapshot['policy'])
        out = solve(records, penalty=job.penalty, time_budget=TIME_BUDGET,
                    checkpoint=job.checkpoint)
        job.checkpoint = out.get('checkpoint')
        if out['status'] == 'rejected':
            job.status = 'rejected'
            job.error = out['failure_reason']
            job.result = _public_result(out)
        else:
            job.status = 'completed'
            job.error = None
            job.result = _public_result(out)
    except Exception as exc:  # 数据/求解异常：保留检查点以便恢复，不产出结论
        job.status = 'failed'
        job.error = str(exc)
    job.updated = time.time()
    db.commit()
    return job


def _public_result(out):
    """剥离内部检查点细节，保留页面所需的分段前后班次分子分母与判定说明。"""
    keep = ('status', 'winner', 'cut', 'cut_day', 'day_count', 'days', 'shifts',
            'total_num', 'total_den', 'eligible_cuts', 'evaluated_cuts',
            'proven_optimal', 'score', 'll', 'no_cut_ll', 'penalty',
            'left', 'right', 'failure_reason', 'best_effort')
    return {k: out[k] for k in keep if k in out}
