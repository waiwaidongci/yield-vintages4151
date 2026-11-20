"""良率回查求解器离线单测：python3 -m unittest -v tests.test_review_solver

只使用标准库，可在无 pip / 无 Docker 的环境直接运行。
"""
import math
import os
import random
import sys
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.services.review_solver import (  # noqa: E402
    MIN_SEGMENT_DAYS, binom_ll, build_grid, cut_is_valid, solve,
)

SHIFTS = ['早班', '晚班']


def rec(day_index, shift, num, den, prefix='2026-11-', pad=2):
    return {'day': prefix + str(day_index + 1).zfill(pad), 'shift': shift,
            'num': num, 'den': den}


def flat(day_index, early_num, early_den, late_num, late_den):
    return [rec(day_index, '早班', early_num, early_den),
            rec(day_index, '晚班', late_num, late_den)]


class BinomialLikelihoodTests(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(binom_ll(0, 0), 0.0)
        self.assertEqual(binom_ll(5, 10), binom_ll(5, 10))
        self.assertTrue(math.isfinite(binom_ll(0, 10)))
        self.assertTrue(math.isfinite(binom_ll(10, 10)))

    def test_mle_is_maximum(self):
        # 同组数据在 MLE p=k/n 处的参数部分似然不低于任意邻近比例
        k, n = 30, 100
        mle = binom_ll(k, n)
        for q in (0.1, 0.2, 0.45, 0.55, 0.9):
            other = k * math.log(q) + (n - k) * math.log(1 - q)
            self.assertGreaterEqual(mle, other)

    def test_illegal(self):
        with self.assertRaises(ValueError):
            binom_ll(11, 10)


class GridTests(unittest.TestCase):
    def test_aggregation_and_prefix(self):
        records = flat(0, 80, 100, 60, 100) + flat(1, 90, 100, 50, 100)
        days, shifts, nums, dens, active = build_grid(records)
        self.assertEqual(days, ['2026-11-01', '2026-11-02'])
        self.assertEqual(shifts, SHIFTS)
        s = shifts.index('早班')
        self.assertEqual(dens[s][2], 200)
        self.assertEqual(nums[s][2], 170)
        self.assertEqual(active[s], [0, 1])


class CutValidityTests(unittest.TestCase):
    def test_too_few_days_infeasible(self):
        records = []
        for t in range(MIN_SEGMENT_DAYS * 2 - 1):
            records += flat(t, 90, 100, 80, 100)
        out = solve(records, penalty=10.0)
        self.assertEqual(out['status'], 'infeasible')
        self.assertIn('无法成段', out['failure_reason'])

    def test_common_shift_required(self):
        # 左段只有早班、右段只有晚班：没有任何共同班次
        records = []
        for t in range(20):
            if t < 10:
                records.append(rec(t, '早班', 90, 100))
            else:
                records.append(rec(t, '晚班', 50, 100))
        out = solve(records, penalty=0.0)
        self.assertEqual(out['status'], 'no_cut')
        self.assertEqual(out['eligible_cuts'], 0)
        self.assertIn('共同班次', out['failure_reason'])

    def test_all_common_shifts_must_decline(self):
        # 早班下降但晚班持平：任何切点都不合法
        records = []
        for t in range(20):
            rate_early = 0.95 if t < 10 else 0.80
            records.append(rec(t, '早班', int(100 * rate_early), 100))
            records.append(rec(t, '晚班', 70, 100))  # 持平
        out = solve(records, penalty=0.0)
        self.assertEqual(out['eligible_cuts'], 0)
        self.assertEqual(out['status'], 'no_cut')

    def test_min_segment_days_enforced(self):
        records = []
        for t in range(20):
            rate = 0.95 if t < 10 else 0.80
            records += flat(t, int(100 * rate), 100, int(100 * rate), 100)
        days, shifts, nums, dens, active = build_grid(records)
        for t in range(1, MIN_SEGMENT_DAYS):
            ok, reason = cut_is_valid(t, shifts, nums, dens, active, 20)
            self.assertFalse(ok)
        for t in range(20 - MIN_SEGMENT_DAYS + 1, 20):
            ok, reason = cut_is_valid(t, shifts, nums, dens, active, 20)
            self.assertFalse(ok)

    def test_valid_cut_detected_at_change(self):
        records = []
        for t in range(30):
            rate = 0.95 if t < 12 else 0.70
            records += flat(t, int(100 * rate), 100, int(100 * rate), 100)
        out = solve(records, penalty=0.0)
        self.assertEqual(out['status'], 'cut')
        self.assertEqual(out['cut'], 12)
        self.assertEqual(out['cut_day'], '2026-11-13')
        self.assertTrue(out['proven_optimal'])
        # 分段两侧班次表都给出分子分母
        self.assertEqual({r['shift'] for r in out['left']}, set(SHIFTS))
        self.assertEqual({r['shift'] for r in out['right']}, set(SHIFTS))
        for r in out['left'] + out['right']:
            self.assertIn('num', r)
            self.assertIn('den', r)
            self.assertAlmostEqual(r['rate'], r['num'] / r['den'])

    def test_penalty_can_suppress_cut(self):
        records = []
        for t in range(30):
            rate = 0.95 if t < 12 else 0.70
            records += flat(t, int(100 * rate), 100, int(100 * rate), 100)
        cut = solve(records, penalty=0.0)
        gain = cut['ll'] - cut['no_cut_ll']
        self.assertGreater(gain, 0)
        suppressed = solve(records, penalty=gain + 1.0)
        self.assertEqual(suppressed['status'], 'no_cut')
        # 无切点方案也参与比较且最优性已证明
        self.assertTrue(suppressed['proven_optimal'])


class ShiftMixConfoundingTests(unittest.TestCase):
    def test_aggregate_rate_drops_without_shift_level_change(self):
        # 各班次自身良率全程不变，仅班次占比改变 → 整线良率"看错"，
        # 按班拟合不应产生任何合法切点。
        records = []
        for t in range(30):
            records += flat(t, 95, 100, 60, 100)
        out = solve(records, penalty=0.0)
        self.assertEqual(out['eligible_cuts'], 0)
        self.assertEqual(out['status'], 'no_cut')

    def test_shift_mix_masks_a_real_decline(self):
        # 两班良率都真实下降，但高良率早班占比大幅上升，整线良率反而抬升，
        # 按班拟合仍应在变化点切出（这正是"班次占比一变，整线良率就可能看错"）。
        records = []
        for t in range(30):
            if t < 14:
                # 整线 (95+80)/200 = 0.875
                records += flat(t, 95, 100, 80, 100)
            else:
                # 早班 0.95→0.93、晚班 0.80→0.55 均下降；
                # 早班占比升到 90%，整线 (837+55)/1000 = 0.892 反而上升
                for _ in range(9):
                    records.append(rec(t, '早班', 93, 100))
                records.append(rec(t, '晚班', 55, 100))
        line_before = (95 + 80) / 200
        line_after = (837 + 55) / 1000
        self.assertGreater(line_after, line_before)
        out = solve(records, penalty=0.0)
        self.assertEqual(out['status'], 'cut')
        self.assertEqual(out['cut'], 14)
        for name in SHIFTS:
            left = next(r for r in out['left'] if r['shift'] == name)
            right = next(r for r in out['right'] if r['shift'] == name)
            self.assertLess(right['rate'], left['rate'])


class CheckpointTests(unittest.TestCase):
    def _data(self):
        records = []
        for t in range(40):
            rate = 0.95 if t < 18 else 0.65
            records += flat(t, int(100 * rate), 100, int(100 * rate), 100)
        return records

    def test_interrupt_and_resume_matches_full_run(self):
        records = self._data()
        full = solve(records, penalty=5.0)
        captured = {}

        def stop_early(cp):
            captured['cp'] = cp
            raise _Interrupt()

        with self.assertRaises(_Interrupt):
            solve(records, penalty=5.0, on_checkpoint=stop_early)
        self.assertTrue(captured['cp']['scanned'])

        resumed = solve(records, penalty=5.0, checkpoint=captured['cp'])
        self.assertTrue(resumed['proven_optimal'])
        self.assertEqual(resumed['cut'], full['cut'])
        self.assertAlmostEqual(resumed['score'], full['score'])
        self.assertEqual(resumed['status'], full['status'])
        # 恢复后扫描集合是检查点的超集且不重复
        self.assertTrue(set(captured['cp']['scanned']).issubset(
            set(resumed['checkpoint']['scanned'])))

    def test_timeout_rejects_and_is_resumable(self):
        records = self._data()
        # 零预算：第一个切点前即超时
        out = solve(records, penalty=5.0, time_budget=0.0)
        self.assertEqual(out['status'], 'rejected')
        self.assertFalse(out['proven_optimal'])
        self.assertIn('拒绝发布', out['failure_reason'])
        self.assertIsNotNone(out['resume_from'])
        resumed = solve(records, penalty=5.0, checkpoint=out['resume_from'])
        self.assertEqual(resumed['status'], 'cut')
        self.assertTrue(resumed['proven_optimal'])


class PerformanceTests(unittest.TestCase):
    def test_800_days_under_30_seconds(self):
        rng = random.Random(20261120)
        records = []
        for t in range(800):
            for shift in SHIFTS:
                base = 0.92 if t < 400 else 0.78
                p = min(0.999, max(0.001, base + rng.uniform(-0.01, 0.01)))
                n = rng.randint(200, 1200)
                records.append(rec(t, shift, _binom(rng, n, p), n,
                                   prefix='2026-01-', pad=4))
        started = time.monotonic()
        out = solve(records, penalty=20.0, time_budget=30.0)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 30.0)
        self.assertEqual(out['status'], 'cut')
        self.assertTrue(out['proven_optimal'])
        self.assertEqual(out['day_count'], 800)
        self.assertAlmostEqual(out['cut'], 400, delta=15)
        print('\n800生产日枚举耗时 %.3f 秒，合法切点 %d 个'
              % (elapsed, out['eligible_cuts']))


class _Interrupt(Exception):
    pass


def _binom(rng, n, p):
    # 标准库无 numpy，用正态近似取整（仅造数，不参与被测逻辑）
    import math
    k = int(round(rng.gauss(n * p, math.sqrt(max(1.0, n * p * (1 - p))))))
    return max(0, min(n, k))


if __name__ == '__main__':
    unittest.main(verbosity=2)
