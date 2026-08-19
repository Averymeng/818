/* 运行时冒烟测试：DOM 桩 + 真实 API（node ≥18 自带 fetch） */
const fs = require('fs');
const path = require('path');

function el(id){
  return {
    id, _cls:new Set(), innerHTML:'', innerText:'', value:'', textContent:'',
    disabled:false,
    classList:{
      contains(c){ return el(id)._cls.has(c); },
      add(c){ el(id)._cls.add(c); },
      remove(c){ el(id)._cls.delete(c); },
      toggle(){},
    },
    addEventListener(){},
    dataset:{},
    appendChild(){},
    select(){},
    style:{},
  };
}
const els={};
global.document={
  getElementById(id){ if(!els[id]) els[id]=el(id); return els[id]; },
  querySelectorAll(){ return []; },
  createElement(t){ return el('tmp_'+t+Math.random()); },
  body:{ appendChild(){}, removeChild(){} },
  execCommand(){},
};
global.window={ open(){} };
global.navigator={ clipboard:{ writeText(){} } };
global.location={ origin:'http://127.0.0.1:8000' };
global.alert=()=>{};
const realFetch=global.fetch;
global.fetch=(u,o)=>realFetch('http://127.0.0.1:8000'+u,o);

(async ()=>{
  const code=fs.readFileSync(path.join(__dirname,'..','web','app.js'),'utf8')
    + '\n;global.__T={showView, onTimeChange, openDetail};'
    + 'Object.defineProperty(global.__T,"WIN",{get:()=>WIN});'
    + 'Object.defineProperty(global.__T,"CUSTOMERS",{get:()=>CUSTOMERS});';
  eval(code);
  const T=global.__T;
  // boot() 已在 eval 内启动；等待若干轮 microtask + 计时
  await new Promise(r=>setTimeout(r,3000));
  const grid=document.getElementById('grid').innerHTML;
  console.log('cards rendered:', (grid.match(/cust-card/g)||[]).length);
  // 切到当日监控
  T.showView('overview');
  const ov=document.getElementById('overviewView').innerHTML;
  console.log('cards rendered:', (grid.match(/cust-card/g)||[]).length);
  console.log('overview renderOverview len:', ov.length);
  // 重点变化归因：等 /api/attrib 异步回填后，attr-* 元素应被真实归因文案替换
  // （DOM 桩中 textContent 不写回 innerHTML，故直接检查元素注册表）
  await new Promise(r=>setTimeout(r,1500));
  const attrEls=Object.keys(els).filter(k=>/^attr-\d+$/.test(k));
  const attrPatched=attrEls.filter(k=>els[k].textContent.includes('消耗环比')).length;
  const attrReal=attrEls.filter(k=>/未见显著异常|基建|转化下滑|成本/.test(els[k].textContent)).length;
  console.log('attribution:', {items: attrEls.length, patchedWithReason: attrPatched, realBackendReason: attrReal});
  console.log('overview modules:', {
    kpi: ov.includes('总消耗')&&ov.includes('留资数')&&ov.includes('留资成本'),
    trend: ov.includes('polyline'),
    process: ov.includes('CTR')&&ov.includes('按钮率')&&ov.includes('留资CVR')&&ov.includes('CPC')&&ov.includes('过程数据'),
    base: ov.includes('基建情况')&&ov.includes('在投笔记')&&ov.includes('新投笔记')&&ov.includes('在投计划')&&ov.includes('新投计划'),
    alerts: ov.includes('今日诊断速览'),
    mon: ov.includes('客户每日监控'),
    attrib: ov.includes('重点变化归因')&&ov.includes('掉量 TOP')&&ov.includes('增量 TOP'),
  });
  console.log('WIN:', T.WIN);
  console.log('status counts:', T.CUSTOMERS.reduce((m,c)=>(m[c.st]=(m[c.st]||0)+1,m),{}));
  // 切换时间窗口：近7天
  document.getElementById('fRange').value='近7天';
  await T.onTimeChange();
  console.log('after 近7天 WIN:', T.WIN, '| grid cards:', (document.getElementById('grid').innerHTML.match(/cust-card/g)||[]).length);
  // 打开详情
  T.openDetail(0);
  const charts=document.getElementById('detailCharts').innerHTML;
  const tbl=document.getElementById('detailTable').innerHTML;
  console.log('detail charts:', (charts.match(/small-cell/g)||[]).length, '| table rows:', (tbl.match(/<tr>/g)||[]).length);
  console.log('detail table fields:', (tbl.match(/<th/g)||[]).length);
  // 今日窗口
  document.getElementById('fRange').value='今日';
  await T.onTimeChange();
  console.log('after 今日 WIN:', T.WIN, '| chart cells:', (document.getElementById('detailCharts').innerHTML.match(/small-cell/g)||[]).length, '| table rows:', (document.getElementById('detailTable').innerHTML.match(/<tr>/g)||[]).length);
  process.exit(0);
})().catch(e=>{ console.error('SMOKE FAIL:', e); process.exit(1); });
