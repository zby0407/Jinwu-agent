const fs = require('fs');
const path = require('path');
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType,
  Table, TableRow, TableCell, WidthType, ShadingType, BorderStyle,
  ImageRun, PageBreak, Footer, Header
} = require('docx');

const root = '/home/zzz/2026tzb/8.20.4';
const out = path.join(root, 'docs', '第26太阳活动周-P5-P6评委展示稿.docx');
const screenshot = path.join(root, 'research/review/evals/runs/main_sc26.primary.r41d/screenshot.png');
const dataPath = path.join(root, '.b07-r3-workspace/projects/default/runs/run_01a03993-f6c9-7453_4983b7bc/research_review/artifacts/data-artifact/v0001.json');

const blue = '17365D', light = 'EAF2F8', gold = 'F7E7CE', red = 'FCE4D6', green = 'E2F0D9', gray = 'F2F2F2';
const margins = { top: 800, bottom: 800, left: 850, right: 850 };
const runState = '正式确定性回测结果（2026-08-27）：第 26 周峰值点预测 174.994，95% 预测区间 65.806–277.656；候选滞后模型回测 MAE 高于训练均值基线，正式置信度为低。生产 WebUI B25/B26 尚未完成最终发布，失败证据保留在会话台账。';

function tx(text, opts={}) { return new TextRun({ text, font: '等线', size: opts.size || 21, bold: !!opts.bold, color: opts.color || '222222', italics: !!opts.italics }); }
function p(text='', opts={}) { return new Paragraph({ children: [tx(text, opts)], spacing: { after: opts.after || 100, line: 300 }, alignment: opts.align || AlignmentType.LEFT }); }
function rich(parts, opts={}) { return new Paragraph({ children: parts.map(x => typeof x === 'string' ? tx(x) : tx(x.text, x)), spacing:{after:opts.after||100,line:300}, alignment:opts.align||AlignmentType.LEFT }); }
function h(text, level=HeadingLevel.HEADING_1) { return new Paragraph({ children:[tx(text,{size:level===HeadingLevel.HEADING_1?30:25,bold:true,color:blue})], heading:level, spacing:{before:220,after:130} }); }
function cell(text, fill='FFFFFF', bold=false) { return new TableCell({ shading:{type:ShadingType.CLEAR,color:'FFFFFF',fill}, margins:{top:80,bottom:80,left:100,right:100}, children:[p(text,{bold,size:19,after:0})] }); }
function table(headers, rows, widths) { return new Table({ width:{size:100,type:WidthType.PERCENTAGE}, columnWidths:widths, rows:[new TableRow({children:headers.map(x=>cell(x,blue,true))}), ...rows.map(r=>new TableRow({children:r.map((x,i)=>cell(String(x),i===0?light:'FFFFFF',false))}))], borders:{top:{style:BorderStyle.SINGLE,size:4,color:'B7C9D6'},bottom:{style:BorderStyle.SINGLE,size:4,color:'B7C9D6'},insideH:{style:BorderStyle.SINGLE,size:2,color:'D9E2F3'},insideV:{style:BorderStyle.SINGLE,size:2,color:'D9E2F3'}} }); }
function bullet(text) { return new Paragraph({ children:[tx(text)], bullet:{level:0}, spacing:{after:70,line:280} }); }
function quote(text) { return new Paragraph({ children:[tx(text,{italics:true,color:'44546A'})], indent:{left:360,right:220}, border:{left:{style:BorderStyle.SINGLE,size:18,color:'5B9BD5'}}, spacing:{after:100,line:290} }); }

const children = [];
children.push(new Paragraph({ children:[tx('第 26 太阳活动周初步概率预测', {size: forty=40,bold:true,color:blue})], alignment:AlignmentType.CENTER, spacing:{before:900,after:260} }));
children.push(new Paragraph({ children:[tx('P5–P6 评委展示稿｜方向 2B：太阳物理假设生成与证据推理',{size:25,bold:true,color:'5B9BD5'})], alignment:AlignmentType.CENTER, spacing:{after:450} }));
children.push(p('目标口径：WDC–SILSO Version 2.0，13 个月平滑月均总太阳黑子数峰值。', {align:AlignmentType.CENTER,size:22}));
children.push(p('资料截止：2026-06-30｜展示版本：2026-08-26', {align:AlignmentType.CENTER,size:20,color:'666666'}));
children.push(p('最新正式回测摘要：点预测 174.994；95% 区间 65.806–277.656；训练均值基线 178.7。候选滞后模型 MAE 46.00，高于基线 42.42，差值 +3.576（bootstrap 95% CI [−11.590, 3.891]），因此不宣称正技能。', {align:AlignmentType.CENTER,size:20,color:'8B0000',after:120}));
children.push(new Paragraph({ children:[tx('给评委的一句话', {size:24,bold:true,color:blue})], alignment:AlignmentType.CENTER, spacing:{before:700,after:140} }));
children.push(quote('我们把“能否做前兆型正式分类”和“今天能否给出可更新的概率预测”分成两条证据路径：前者目前未就绪，后者可以用同口径历史分布、竞争情景和显式模型差异给出宽区间，并保留可复核的失败案例。'));
children.push(p('状态边界：'+runState,{size:18,color:'666666',italics:true,after:0}));
children.push(new PageBreak());

children.push(h('一、P5.1 可检验假设组合'));
children.push(p('三个候选假设均把目标锁定在同一个平滑峰值定义；预测置信度只反映当前证据，不因“必须出现高置信度”而人为上调。'));
children.push(table(['假设','可观测量与预测','置信度 / 来源','优先级'],[
 ['H1 持续性–历史分布基线','SC26 平滑峰值服从已完成周期峰值的经验分布；用逐周期留一回报检验区间覆盖与误差。','中等；官方 SILSO 平滑序列 + 确定性复算','主分析'],
 ['H2 共享数据模型情景集合','已发表/可复算的目标兼容情景作为一个相关组；用组内差异而非独立票数扩张证据。','中等偏下；结构多样但训练历史共享','敏感性分析'],
 ['H3 极区前兆条件分支','极小确认且同口径极区场/轴向偶极矩可用后，前兆似然更新 H1/H2；当前不把缺失前兆当数值输入。','低；机制可检验，当前观测尚未形成','更新触发器'],
], [2100,3600,2500,1300]));

children.push(h('二、P5.2 证据链', HeadingLevel.HEADING_1));
children.push(table(['证据来源','直接观察 / 复算','逻辑链接'],[
 ['SILSO v2 平滑序列','2026-01 非缺测值 104.2；SC25 暂定平滑峰值 160.9（2024-10）。','定义与锚点可复核；支持 H1 的目标一致性，不等于 SC26 前兆。'],
 ['SILSO 极值表','SC25/26 极小尚未正式确认，SC25 官方最大值未发布。','H3 的前兆分支保持“未就绪”，不能给窄的前兆型区间。'],
 ['WSO Polar.html','截止窗内最后有效观测 2026-01-09；显式 XXX 缺测 17 行。','缺测被保留为证据边界；不插值、不把历史 MWO/WSO 年表替代当前前兆。'],
 ['NOAA/SWPC F10.7','2025 周期状态指标；截至 2026-06 月值 138.21。','只能约束当前活动状态，不能直接充当 SC26 极区前兆。'],
], [2600,3900,3000]));

children.push(h('三、P5.3 反例、未支持证据与修订')); 
children.push(bullet('反例 1：把未平滑月均峰值与 13 个月平滑峰值混用，会得到不可比的峰值；修订为全流程只使用平滑月均总黑子数。'));
children.push(bullet('反例 2：把六个共享历史训练数据的模型当作六次独立观测，会虚假收窄区间；修订为等权替代情景混合，并显式加入模型差异尺度。'));
children.push(bullet('反例 3：旧版 HTML 预览只暴露页首，WSO 解析器看见零行并误报“数据为空”；修订为 HTML 白名单 + 长文件头尾预览，保留 2026-01-09 与 17 个 XXX 行。'));
children.push(bullet('未支持证据：截至截止日没有可绑定的同口径极小附近极区前兆；因此 H3 只能是更新规则，不能升级为当前数值预测。'));
children.push(new PageBreak());

children.push(h('四、P5.4 可执行的下一步验证计划'));
children.push(table(['触发条件','数据 / 设施','验证周期','成功判据'],[
 ['T1：SILSO 确认 SC25/26 极小','WDC–SILSO 官方周期极值表','每月重跑一次边界状态','极小月与定义进入正式表，周期起点可复核'],
 ['T2：同口径极区场/轴向偶极矩可用','WSO 同定义磁图或经标定的轴向偶极矩序列','T1 后下一观测窗口','连续、非 XXX 的极区场输入；与历史标定口径一致'],
 ['T1+T2 联合更新','同一平滑目标、前兆似然 + H1/H2 先验','触发后 48 小时内','回报区间覆盖、中心移动方向和敏感性分析均可复算'],
], [2800,3200,1800,3700]));
children.push(p('更新规则：若极区场在下降期后段持续显著弱于历史同口径水平，降低预测中心；若明显增强，则上调中心；在正式极小落定后再收缩峰值时间区间。', {after:0}));

children.push(h('五、P6.1 成功案例：可展示的概率闭环')); 
children.push(p('成功案例采用可复核的独立确定性计算作为数值展示基线：六个目标兼容情景等权混合，模型差异尺度 30，固定随机种子，2,000,000 次抽样。该数值展示不把尚未完成的 fresh B07 WebUI 运行伪装成已完成结果。'));
children.push(table(['输出量','展示值','解释'],[
 ['正式点预测（中位数）','174.994','由 cycles 1–24 训练、cycle 25 滞后输入得到；置信度低'],
 ['95% 预测区间','65.806–277.656','固定种子 20260827、周期级 bootstrap 10,000 次'],
 ['滞后回测 MAE','候选 46.00；基线 42.42','候选较基线高 3.576，未显示正技能'],
 ['峰值时间','中位约 2034.8 年；80% 约 2033.4–2036.2','由历史周期长度情景混合得到'],
 ['相对强度概率','低于 SC25 暂定 160.9：72.0%；超过历史均值 183：13.1%','概率可更新，不宣称确定性结论'],
], [3000,3000,4700]));
children.push(h('六、P6.2 经典失败案例：已修复的证据可见性问题'));
children.push(p('旧版失败不是科学结论，而是输入可见性故障：长 HTML 只提供页首预览，WSO 解析器看不到尾部观测与缺测行，随后把“未读到”误判为“零数据”，并在审查阶段阻断。'));
children.push(table(['阶段','旧表现','修复与当前边界'],[
 ['输入预览','Polar.html 只见 head；解析结果 0 行或 37/17 不一致。','加入 .html 后缀白名单；长文本保留 head + tail，尾部锚点可见。'],
 ['科学解释','把工具读取失败误写成观测缺失，混淆技术失败与证据不足。','技术错误显式抛出；真实缺测保留为 WSO_CUTOFF_WINDOW_MISSING。'],
 ['交付决策','旧 B06/r41d 只输出“暂不启动”，违反本题必须给概率预测的要求。','B07 强制携带 preliminary forecast 分支；最终结果仍须等 fresh B07 的实验与发布阶段完成。'],
], [2200,3600,4900]));
if (fs.existsSync(screenshot)) {
  const img = fs.readFileSync(screenshot);
  children.push(p('旧版生产界面截图（仅作为失败案例，不代表本次成功结果）：',{size:18,color:'666666',after:50}));
  children.push(new Paragraph({children:[new ImageRun({data:img,transformation:{width:640,height:360}})],alignment:AlignmentType.CENTER,spacing:{after:80}}));
}
children.push(p('评委判读边界：本稿展示了“最好的一面”（可复核的概率闭环与清晰证据链）以及一个已经修复的经典失败。fresh B07 r8 的生产状态仍以其 run_state、实验结果和最终审查为准；在这些文件落盘前，不应把本稿中的独立计算数字称为 WebUI 端到端正式发布。', {size:18,color:'666666',italics:true}));

const doc = new Document({ sections:[{properties:{page:{margin:margins}}, headers:{default:new Header({children:[p('太阳活动周研究｜P5–P6 评委展示稿',{size:17,color:'7F7F7F',after:0})]})}, footers:{default:new Footer({children:[new Paragraph({children:[tx('第 26 周期初步概率预测 · 证据截止 2026-06-30',{size:16,color:'7F7F7F'})],alignment:AlignmentType.CENTER})]})}, children}] });
Packer.toBuffer(doc).then(buf => { fs.writeFileSync(out,buf); console.log(out); });
