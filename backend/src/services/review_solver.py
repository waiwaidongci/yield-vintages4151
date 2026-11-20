"""良率回查切点求解器（纯标准库，可离线单测）。

业务口径（与需求逐条对应）：
- 每段至少 MIN_SEGMENT_DAYS 个生产日；
- 切点两侧至少有一个共同班次（该班次两侧分母均大于 0）；
- 所有共同班次的良率都必须下降，切点才合法；
- 在全期候选上比较「按班次拟合的二项对数似然 − 切点罚分」，无切点方案同样参与比较；
- 30 秒预算（默认）内未完成全部枚举即未证最优，按失败处理并保留检查点，可恢复续算。

只依赖数学库，不触碰数据库与 Redis，便于在任何 Python3.11 环境直接单测。
"""
import math
import time

MIN_SEGMENT_DAYS = 7
DEFAULT_TIME_BUDGET = 30.0
CHECKPOINT_EVERY = 10


def binom_ll(k, n):
    """单段二项对数似然的参数相关部分 k·log p + (n-k)·log(1-p)，p=k/n。

    边界 n=0 返回 0（无数据即无贡献）；p=0/1 时以 0·log0=0 处理。
    组合数项 log C(n,k) 不含拟合参数，且按 Vandermonde 恒等式，任何切点两侧的
    组合系数之积都不大于合并值——计入它会对所有切点施加一道不由发起人控制的
    隐式罚分，与「罚分由发起时填写」冲突，故分段比较只取参数部分；
    切点取舍完全由发起人填写的罚分调节。
    """
    if n <= 0:
        return 0.0
    if k < 0 or k > n:
        raise ValueError('分子必须介于 0 与分母之间')
    if k == 0 or k == n:
        return 0.0
    p = k / n
    return k * math.log(p) + (n - k) * math.log(1.0 - p)


def build_grid(records):
    """把按日×班次的分子分母整理成搜索网格。

    records: [{'day': 'YYYY-MM-DD', 'shift': str, 'num': int, 'den': int}, ...]
    返回：
      days        升序去重的生产日列表
      shifts      稳定排序的班次列表
      nums[s][t]  班次 s 在第 t 个生产日（含）之前的分子累计
      dens[s][t]  班次 s 在第 t 个生产日（含）之前的分母累计；t=0 行为 0
      active[s]   班次 s 实际有分母的生产日下标（升序），用于共同班次判定
    """
    by_day = {}
    shift_order = []
    for rec in records:
        day = rec['day']
        shift = rec['shift']
        num = int(rec['num'])
        den = int(rec['den'])
        if den < 0 or num < 0 or num > den:
            raise ValueError('分子分母不合法：%s %s' % (day, shift))
        if day not in by_day:
            by_day[day] = {}
        cell = by_day[day].setdefault(shift, [0, 0])
        cell[0] += num
        cell[1] += den
        if shift not in shift_order:
            shift_order.append(shift)
    days = sorted(by_day)
    shifts = sorted(shift_order)
    s_count = len(shifts)
    nums = [[0] * (len(days) + 1) for _ in range(s_count)]
    dens = [[0] * (len(days) + 1) for _ in range(s_count)]
    active = [[] for _ in range(s_count)]
    index = {name: i for i, name in enumerate(shifts)}
    for t, day in enumerate(days, start=1):
        for s, name in enumerate(shifts):
            cell = by_day[day].get(name)
            nums[s][t] = nums[s][t - 1] + (cell[0] if cell else 0)
            dens[s][t] = dens[s][t - 1] + (cell[1] if cell else 0)
            if cell and cell[1] > 0:
                active[s].append(t - 1)
    return days, shifts, nums, dens, active


def _range_ll(prefix_num, prefix_den, start, end):
    """生产日下标 [start, end] 区间（含端点）的二项对数似然。"""
    k = prefix_num[end + 1] - prefix_num[start]
    n = prefix_den[end + 1] - prefix_den[start]
    return binom_ll(k, n)


def cut_is_valid(t, shifts, nums, dens, active, day_count):
    """切点 t 表示第 t 个生产日之后切开（左段 0..t-1，右段 t..day_count-1）。"""
    left_days = t
    right_days = day_count - t
    if left_days < MIN_SEGMENT_DAYS or right_days < MIN_SEGMENT_DAYS:
        return False, '每段不足 %d 个生产日' % MIN_SEGMENT_DAYS
    common = 0
    for s in range(len(shifts)):
        left_n = dens[s][t] - dens[s][0]
        right_n = dens[s][day_count] - dens[s][t]
        if left_n <= 0 or right_n <= 0:
            continue  # 共同班次要求两侧分母均大于 0
        # 必须真实出现在两侧的生产日中（active 列表二分定位）
        seen_left = _has_active_before(active[s], t)
        seen_right = _has_active_after(active[s], t)
        if not (seen_left and seen_right):
            continue
        common += 1
        left_k = nums[s][t] - nums[s][0]
        right_k = nums[s][day_count] - nums[s][t]
        # 严格下降：右段良率 < 左段良率，整数交叉相乘避免浮点误差
        if right_k * left_n >= left_k * right_n:
            return False, '共同班次「%s」良率未下降' % shifts[s]
    if common == 0:
        return False, '切点两侧没有共同班次'
    return True, ''


def _has_active_before(active_days, t):
    # active_days 升序，是否存在 < t 的元素
    import bisect
    return bisect.bisect_left(active_days, t) > 0


def _has_active_after(active_days, t):
    import bisect
    return bisect.bisect_left(active_days, t) < len(active_days)


def shift_table(nums, dens, shifts, start, end):
    """生成某生产日区间 [start,end] 内各班次的分子/分母/良率。"""
    rows = []
    for s, name in enumerate(shifts):
        k = nums[s][end + 1] - nums[s][start]
        n = dens[s][end + 1] - dens[s][start]
        if n <= 0:
            continue
        rows.append({'shift': name, 'num': k, 'den': n, 'rate': k / n})
    rows.sort(key=lambda x: x['shift'])
    return rows


def solve(records, penalty, time_budget=DEFAULT_TIME_BUDGET, checkpoint=None,
          deadline=None, on_checkpoint=None):
    """枚举全部合法切点，返回似然减罚分最高的分段。

    checkpoint: 中断恢复时传入上次写出的检查点，已扫描切点不会重复评估；
    deadline:   绝对单调时钟上限（time.monotonic()），默认按 time_budget 现取；
    on_checkpoint(cp): 每 CHECKPOINT_EVERY 个切点回调一次，模拟求解中断由测试驱动。
    返回 dict 结构见 README；未在预算内枚举完时 status='rejected'。
    """
    if not isinstance(penalty, (int, float)) or penalty < 0 or not math.isfinite(penalty):
        raise ValueError('切点罚分必须为非负有限数')
    days, shifts, nums, dens, active = build_grid(records)
    day_count = len(days)
    total_num = sum(nums[s][day_count] for s in range(len(shifts)))
    total_den = sum(dens[s][day_count] for s in range(len(shifts)))

    base = {
        'day_count': day_count,
        'days': days,
        'shifts': shifts,
        'total_num': total_num,
        'total_den': total_den,
        'eligible_cuts': 0,
        'evaluated_cuts': 0,
        'proven_optimal': False,
        'checkpoint': None,
    }
    if day_count < MIN_SEGMENT_DAYS * 2:
        base.update(status='infeasible', cut=None, winner='none',
                    failure_reason='全期仅 %d 个生产日，两段各至少 %d 个生产日，无法成段'
                    % (day_count, MIN_SEGMENT_DAYS))
        return base
    if total_den <= 0:
        base.update(status='infeasible', cut=None, winner='none',
                    failure_reason='所选范围内没有有效分母')
        return base

    started = time.monotonic()
    if deadline is None:
        deadline = started + time_budget

    # 无切点方案：每个班次在全期各拟合一个二项比例
    no_cut_ll = sum(_range_ll(nums[s], dens[s], 0, day_count - 1)
                    for s in range(len(shifts)))
    best = {'winner': 'none', 'cut': None, 'score': no_cut_ll, 'll': no_cut_ll,
            'penalty': 0.0, 'left': shift_table(nums, dens, shifts, 0, day_count - 1),
            'right': []}

    # 恢复检查点
    scanned = set()
    if checkpoint:
        scanned = set(checkpoint.get('scanned', []))
        saved_best = checkpoint.get('best')
        if saved_best and saved_best.get('score', float('-inf')) > best['score']:
            best = _restore_best(saved_best, nums, dens, shifts, day_count, penalty)
        base['evaluated_cuts'] = checkpoint.get('evaluated_cuts', 0)
        base['eligible_cuts'] = checkpoint.get('eligible_cuts', 0)

    candidates = range(MIN_SEGMENT_DAYS, day_count - MIN_SEGMENT_DAYS + 1)
    timed_out = False
    batch = 0
    for t in candidates:
        if t in scanned:
            continue
        if time.monotonic() >= deadline:
            timed_out = True
            break
        ok, reason = cut_is_valid(t, shifts, nums, dens, active, day_count)
        if ok:
            base['eligible_cuts'] += 1
            ll = 0.0
            for s in range(len(shifts)):
                ll += _range_ll(nums[s], dens[s], 0, t - 1)
                ll += _range_ll(nums[s], dens[s], t, day_count - 1)
            score = ll - float(penalty)
            base['evaluated_cuts'] += 1
            if score > best['score']:
                best = {'winner': 'cut', 'cut': t, 'score': score, 'll': ll,
                        'penalty': float(penalty),
                        'cut_day': days[t],
                        'left': shift_table(nums, dens, shifts, 0, t - 1),
                        'right': shift_table(nums, dens, shifts, t, day_count - 1)}
        scanned.add(t)
        batch += 1
        if batch >= CHECKPOINT_EVERY:
            batch = 0
            cp = _make_checkpoint(scanned, best, base)
            base['checkpoint'] = cp
            if on_checkpoint:
                on_checkpoint(cp)
            if time.monotonic() >= deadline:
                timed_out = True
                break

    last_t = day_count - MIN_SEGMENT_DAYS
    fully_scanned = all(t in scanned for t in
                        range(MIN_SEGMENT_DAYS, last_t + 1))
    base['checkpoint'] = _make_checkpoint(scanned, best, base)

    if timed_out or not fully_scanned:
        base.update(status='rejected', cut=best.get('cut'), winner=best['winner'],
                    failure_reason='超过 %g 秒预算仍未枚举完全部切点，最优性未证明，拒绝发布结论'
                    % time_budget, best_effort=_public_best(best, days),
                    resume_from=base['checkpoint'])
        return base

    base['proven_optimal'] = True
    base['winner'] = best['winner']
    base['cut'] = best['cut']
    if best['winner'] == 'cut':
        base['status'] = 'cut'
        base['cut_day'] = best['cut_day']
        base['score'] = best['score']
        base['ll'] = best['ll']
        base['penalty'] = best['penalty']
        base['no_cut_ll'] = no_cut_ll
        base['left'] = best['left']
        base['right'] = best['right']
    else:
        base['status'] = 'no_cut'
        base['cut'] = None
        base['cut_day'] = None
        base['score'] = no_cut_ll
        base['ll'] = no_cut_ll
        base['penalty'] = 0.0
        base['no_cut_ll'] = no_cut_ll
        base['left'] = best['left']
        base['right'] = []
        if base['eligible_cuts'] == 0:
            base['failure_reason'] = '全期没有满足七生产日、共同班次且共同班次良率全降的切点'
    return base


def _make_checkpoint(scanned, best, base):
    return {'scanned': sorted(scanned), 'best_cut': best.get('cut'),
            'best_score': best.get('score'), 'best_winner': best.get('winner'),
            'penalty': best.get('penalty', 0.0),
            'evaluated_cuts': base['evaluated_cuts'],
            'eligible_cuts': base['eligible_cuts']}


def _restore_best(saved, nums, dens, shifts, day_count, penalty):
    """检查点只存切点序号，恢复时按网格与同一罚分重算，避免序列化整张班次表。"""
    t = saved.get('best_cut')
    if t is None:
        return {'winner': 'none', 'cut': None, 'score': saved.get('best_score'),
                'll': saved.get('best_score'), 'penalty': 0.0,
                'left': shift_table(nums, dens, shifts, 0, day_count - 1), 'right': []}
    ll = 0.0
    for s in range(len(shifts)):
        ll += _range_ll(nums[s], dens[s], 0, t - 1)
        ll += _range_ll(nums[s], dens[s], t, day_count - 1)
    return {'winner': 'cut', 'cut': t, 'score': ll - float(penalty), 'll': ll,
            'penalty': float(penalty), 'cut_day': None,
            'left': shift_table(nums, dens, shifts, 0, t - 1),
            'right': shift_table(nums, dens, shifts, t, day_count - 1)}


def _public_best(best, days):
    out = {'winner': best['winner'], 'cut': best.get('cut'),
           'cut_day': best.get('cut_day') or (days[best['cut']] if best.get('cut') is not None else None),
           'score': best.get('score'), 'left': best.get('left'), 'right': best.get('right')}
    return out
