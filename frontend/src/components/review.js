import {esc,fmt} from '../utils/format.js';
import {REVIEW_STATUSES} from '../constants/enums.js';

const pct=v=>(v*100).toFixed(2)+'%';

function shiftRows(rows){
 if(!rows||!rows.length)return '<p class="empty">该段无班次数据。</p>';
 return '<div class="table-wrap"><table><thead><tr><th>班次</th><th>分子</th><th>分母</th><th>良率</th></tr></thead><tbody>'
  +rows.map(r=>`<tr><td>${esc(r.shift)}</td><td>${fmt(r.num)}</td><td>${fmt(r.den)}</td><td>${pct(r.rate)}</td></tr>`).join('')
  +'</tbody></table></div>';
}

export function reviewCard(job){
 if(!job)return '<p class="empty">选择一个回查任务查看分段结论。</p>';
 const r=job.result;
 const badge='<span class="status '+esc(job.status)+'">'+esc(REVIEW_STATUSES[job.status]||job.status)+'</span>';
 let body='';
 if(job.error){
  body+=`<div class="error-box"><b>失败原因</b><p>${esc(job.error)}</p></div>`;
 }
 if(r){
  if(r.status==='infeasible'){
   body+=`<div class="warning-box"><b>无法成段</b><p>${esc(r.failure_reason||'')}</p><p>全期 ${fmt(r.day_count)} 个生产日。</p></div>`;
  }else if(job.status==='rejected'){
   body+=`<div class="warning-box"><b>未证最优，拒绝发布</b><p>${esc(r.failure_reason||job.error||'')}</p>`
     +(r.best_effort?`<p>当前最优仅为尽力结果（${esc(r.best_effort.winner==='cut'?'切点 '+esc(r.best_effort.cut_day):'无切点')}），不作为结论发布。</p>`:'')
     +'</div>';
  }else if(r.status==='no_cut'){
   body+=`<div class="ok-box"><b>结论：无切点</b><p>${esc(r.failure_reason||'所有候选切点均未通过共同班次良率全降校验，或罚分后无切点方案最优。')}</p>`
     +`<p>全期二项对数似然 ${fmt(r.ll)}，已枚举 ${fmt(r.eligible_cuts)} 个合法切点，最优性已证明。</p></div>`;
  }else if(r.status==='cut'){
   body+=`<div class="ok-box"><b>结论：切点 ${esc(r.cut_day)}</b>`
     +`<p>切点后首个生产日为 <b>${esc(r.cut_day)}</b>；分段得分（按班二项对数似然 − 罚分）${fmt(r.score)}，`
     +`似然 ${fmt(r.ll)}，罚分 ${fmt(r.penalty)}，无切点似然 ${fmt(r.no_cut_ll)}。</p>`
     +`<p>合法切点 ${fmt(r.eligible_cuts)} 个，已全部枚举，最优性已证明。</p></div>`;
  }
  if(r.left&&r.left.length){
   body+='<h3>切点前分段（按班次分子/分母）</h3>'+shiftRows(r.left);
  }
  if(r.right&&r.right.length){
   body+='<h3>切点后分段（按班次分子/分母）</h3>'+shiftRows(r.right);
  }
  if(r.shifts){
   body+=`<p class="method">回查范围 ${fmt(r.day_count)} 个生产日，班次：${r.shifts.map(esc).join('、')}；合计分子 ${fmt(r.total_num)} / 分母 ${fmt(r.total_den)}。</p>`;
  }
 }
 const canResume=job.status==='failed'||job.status==='rejected';
 return `<section class="report review-card"><div class="report-top"><h2>${esc(job.name)}</h2>${badge}</div>`
  +`<p>试制线 ${esc(job.department)} · 日期 ${esc(job.date_from||'最早')} 至 ${esc(job.date_to||'最新')} · 罚分 ${fmt(job.penalty)}</p>`
  +`<p class="checksum">SHA256 ${esc(job.checksum)}</p>${body}`
  +(canResume?`<form id="resume-form" data-id="${job.id}"><label>恢复说明（可选）<input name="note" minlength="4" placeholder="说明本次恢复续算的原因"></label><button>恢复这次回查</button></form>`:'')
  +'<details><summary>受理时冻结快照与求解检查点</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px">'+esc(JSON.stringify({snapshot:job.snapshot,checkpoint:job.checkpoint},null,2))+'</pre></details></section>';
}
