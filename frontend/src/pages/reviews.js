import {heading} from '../components/common/header.js';
import {reviewCard} from '../components/review.js';
import {esc} from '../utils/format.js';
import {REVIEW_STATUSES} from '../constants/enums.js';

export function reviews(c,d,selected){
 const departments=[...new Set(d.sources.map(s=>s.data.department))].sort();
 const job=d.reviews.find(j=>j.id===selected)||d.reviews[0];
 const dateTips=d.periods.map(p=>p.name).sort();
 return heading('YIELD REVIEW / 07','良率回查与切点检测','按所选试制线、日期和口径回查良率下降；每段至少七个生产日，切点两侧共同班次良率都下降才可切。30 秒内未证最优则拒绝发布，可恢复续算。')
 +`<form id="review-form" class="inline-form">
   <label>回查名称<input name="name" value="${esc(c.title.slice(0,2))}良率回查" required minlength="2"></label>
   <label>试制线<select name="department" required>${departments.map(x=>`<option>${esc(x)}</option>`).join('')}</select></label>
   <label>口径版本<select name="policy_id">${d.policies.map(p=>`<option value="${p.id}">${esc(p.version)}</option>`).join('')}</select></label>
   <label>起始日期（可空）<input name="date_from" placeholder="如 2026-11-01" list="period-list"></label>
   <label>结束日期（可空）<input name="date_to" placeholder="如 2026-11-20" list="period-list"></label>
   <label>切点罚分<input name="penalty" type="number" step="0.01" min="0" value="10" required><small>发起时填写，比较「按班二项对数似然 − 罚分」</small></label>
   <button>发起回查</button>
 </form>
 <datalist id="period-list">${dateTips.map(x=>`<option value="${esc(x)}"></option>`).join('')}</datalist>
 <p class="method">无切点方案同样参与比较；重试相同选择不会重复给结论，而是返回原任务。回查按受理时的底稿与口径冻结执行，之后底稿更正不影响本次结论。</p>
 <div class="run-layout">
   <aside class="run-list">${d.reviews.length?d.reviews.map(j=>`<button class="select-review" data-id="${j.id}">${esc(j.name)} <span class="status ${esc(j.status)}">${esc(REVIEW_STATUSES[j.status]||j.status)}</span></button>`).join(''):'<p class="empty">尚无回查任务。</p>'}</aside>
   <div>${reviewCard(job)}</div>
 </div>`;
}
