---
name: genui
description: 在面向用户的回答正文中输出 ```dsh-ui 围栏，把指标、对比表、流程、时间线、结论卡等渲染成内嵌交互组件。当回答包含 3 条以上并列要点、数字对比、关键指标、步骤/流程、方案权衡或需要用户点选反馈时使用；纯文字问答不需要。
---

# GenUI — 生成式 UI 输出规范

**语言**：组件内所有用户可见文本（标题、标签、内容、选项、说明）与回答正文使用同一语言——用户用中文就全中文，用户用英文就全英文。JSON 键名、组件 type、id、action 名保持不变。

你可以在**回答正文中间**输出可交互 UI 组件：写一个 `dsh-ui` 围栏（fenced block，语言标记 `dsh-ui`），内含 JSON 规格，前端渲染器会把这一整块画成真实组件，文字照常穿插在前后。组件**就是回答的一部分**，不是工具调用。

    ```dsh-ui
    {"title":"标题","gap":14,"items":[...]}
    ```

## 围栏书写铁律（先读这一节）

围栏开头那一行的语言标记**统一写 `dsh-ui`** 这六个字符，不加标点、引号或注释；收尾必须是单独一行的 ` ``` `。渲染器会容错大小写、首尾空格和部分尾部标点，但生成时不要依赖容错。

围栏位置：只写在**回答正文**里；写进 reasoning / 思考块不会渲染。

JSON 必须严格合法，发出前完成 4 步自检：

1. **括号配对**：`{` 与 `}`、`[` 与 `]` 数量相等，收尾序列逐个核对（长表格最易在最后几行错位：把 `]]}]}` 写成 `]}]}]}`）。
2. **无尾随逗号**。
3. **值内引号用中文引号**：字符串值里出现引语一律写 `“”` 或 `「」`——半角 `"` 会破坏 JSON（渲染器只修标点级小错：字符串内半角引号、尾随逗号；**缺括号/错括号等结构错误一律不修**，整条围栏降级为代码块 + 红色错误横幅，写错就重发）。
4. **最后一个字符必须是 `}`**，`}` 之后不要再写任何字符（不要追加 `</p>` 或一句解释）。

字段名逐个核对（写错一个必填字段 = 该组件被丢弃 = 整份 spec 判定不可渲染，用户只看到一段裸 JSON）。高频误写：`callout` 正文是 `content`（不是 text/body）；`table` 要 `columns` + `rows`（不是 data）；`keyvalue` 要 `pairs:[{key,value}]`（不是 items）；`diff` 是 `diffs`；图片/音视频是 `src`；`code` 是 `code`、`copy` 是 `text`。

不要在 dsh-ui 围栏里再包 ``` 代码围栏；不要嵌套围栏。超长表格/列表拆成多个组件分开发，宁短勿长。

## 组件词汇（只允许这些 type）

布局：`text` `row` `col` `grid` `card` `carousel` `divider` `spacer` `arrow` `hero`
展示：`stat` `badge` `progress` `list` `table` `keyvalue` `avatar` `image` `image-group` `audio` `video` `timeline` `file-tree` `breadcrumb` `diff` `json` `code` `callout` `steps` `icon` `blocks`
图表：`chart`（bars/line/donut，可多序列）`plot`（数学函数图）`echart`（ECharts 全功能图表）
交互：`button` `input` `select` `checkbox` `radio` `switch` `slider` `textarea` `tabs` `accordion` `copy` `link` `submit`
高级：`svg` `mermaid` `diagram` `scene3d` `quiz`

### 布局

- text: `{"type":"text","size":"display|h1|h2|h3|body|muted|caption","content":"...","align":"left|center|right"?}`（`tone:"danger|success|warning|info|muted"` 给整行文字上语义色；`mono:true` 等宽字体；`size:"display"` 作引言/开篇点题句，**一条回答 ≤1**）
- row / col: `{"type":"row"|"col","items":[...],"wrap":true?,"spacer":true?,"gap":n?}`；`row.justify:"center"` 主轴居中；`col.width:n`（1-100）列占行宽百分比
- grid: `{"type":"grid","cols":n,"items":[...]}`
- carousel: `{"type":"carousel","title":"...","items":[{"type":"card",...}],"itemWidth":240?,"gap":3?}` — 横向滚动卡片，最多 24 项
- hero: `{"type":"hero","title":"...","subtitle":"...","value":"99.96%","label":"指标名","delta":"+0.02%","spark":[...],"tone":"accent|success|warning|danger"}` — 封面块（eyebrow + 超大数字 + 渐变底色），**一条回答最多一个**，放最前当视觉锚点
- span: 任意 grid 子节点可加 `"span":2` 占多列——bento 排版的唯一原语
- card: `{"type":"card","title":"...","items":[...]}`；`"tone":"info|success|warning|danger"` 语义底色（结论卡/风险卡）；`"accent":"#f59e0b"` 强调色；`"flat":true` 去阴影（图里的面板必须用 flat）；`"eyebrow":"小引题"` 标题上方 meta 小字（一段回答 ≤3 个）
- palette: `chart`/`echart` 支持 `"palette":["#ff8800","#3ecf8e"]` 覆盖分类色板（只有语义需要时才写，否则跟随主题）
- divider: `{"type":"divider"}`；带章节名 `{"type":"divider","label":"章节名"}`（一条回答 ≤3 个）; spacer: `{"type":"spacer"}`

### 展示

- stat: `{"type":"stat","label":"...","value":"...","delta":"+12.4%|-3%"}`（`-` 开头自动红、`+` 绿）；`"spark":[3,5,4,8,6]` 微趋势线（2–60 个有限数值）；`"size":"hero"` 超大数字（一条回答 ≤1 次）
- badge: `{"type":"badge","label":"...","tone":"success|warn|danger|accent","icon":"emoji?"}`
- progress: `{"type":"progress","label":"...","value":0-100,"valueLabel":"70%"}`；`"variant":"ring"` 环形进度；`"target":70` 目标刻度
- avatar: `{"type":"avatar","name":"...","color":"#hex?"}`
- image: `{"type":"image","src":"...","alt":"描述"}` — 只接受 http(s) 或**同源相对地址**；懒加载；不支持 `file:`/`data:`。JW 中展示工作区文件用 `/api/workspace/file?path=<绝对路径或工作区相对路径>`（例：`/api/workspace/file?path=workspace/results/fig1.png`）；`"caption":"图注"` 在图下方加说明行
- image-group: `{"type":"image-group","title":"...","items":[{"src":"...","alt":"...","caption":"..."}]}` — 最多 24 张，点击 lightbox 放大
- audio: `{"type":"audio","src":"...","alt":"描述","loop":true?}` — 仅 http(s)/同源相对地址
- video: `{"type":"video","src":"...","alt":"描述","poster":"..."?,"loop":true?,"muted":true?,"aspectRatio":"16:9|4:3|1:1|9:16"?}`
- list: `{"type":"list","items":["..."] 或 [{"title":"...","desc":"..."}] 或嵌套节点}` — 行内可嵌节点（计入节点预算）
- table: `{"type":"table","columns":["..."],"rows":[["..."]],"types":["text|num|delta|bar|badge"]?,"details":[[...]]?,"total":true?}` — 表头点击本地排序；数值感知（千分位、`k/m/b`、`万/亿`、`%`、货币按真实数值比较）；带符号单元格自动着色（`+12.4%` 绿、`-3` 红）；`types` 按列指定 `bar`（内联进度条）`ring` `spark`（`"3,5,4,8"` 微趋势线）`badge` `delta` `num` `index`（行号）`group`（首列分组标题）；`"total":true` 合计行；`"export":true` 复制 Markdown/CSV 按钮；`"filter":"输入框id"` 绑定 input/select 即时过滤（`filterColumn` 限列，数据多时默认配一个）；`"sortField":"下拉id"` 用下拉排序；`"details"` 与 rows 同序，第 i 项是该行展开内容（任意组件，`null`=不可展开）
- keyvalue: `{"type":"keyvalue","pairs":[{"key":"...","value":"..."}]}`
- timeline: `{"type":"timeline","items":[{"title":"...","desc":"...","time":"..."}]}`
- file-tree: `{"type":"file-tree","items":[{"name":"...","type":"file|dir","children":[...]?}]}` — 本地折叠/展开
- breadcrumb: `{"type":"breadcrumb","items":["...","...","..."]}`
- diff: `{"type":"diff","diffs":[{"path":"...","oldText":"..."|null,"newText":"..."}]}`
- json: `{"type":"json","value":...}`
- code: `{"type":"code","lang":"ts","code":"..."}`
- callout: `{"type":"callout","tone":"info|success|warning|error","title":"...","content":"..."}` — 单条强调/提醒/风险摘要；`content` 是文本字符串，可用 `"\n"` 分行；多条独立事项用 `list` 的 `{title,desc}`
- steps: `{"type":"steps","current":n,"steps":[{"title":"...","desc":"..."}]}`
- arrow: `{"type":"arrow","dir":"down|right","length":16-96,"label":"..."}` — 图里两个块之间的细线连接件；上下流程图用它，不要用文字「↓」
- icon: `{"type":"icon","name":"Nodes","tone":"info|success|warning|danger|muted|default","size":16|20|24|32,"tile":true,"label":"无障碍名"}` — 内置图标集；`tile:true` 淡色圆角方底。优先用内联秒开的 64 个：Check CheckCircle Info InfoCircle Warning Error X Plus Minus Circle ArrowDown ArrowUp ArrowRight ArrowLeft ArrowCurvedRight Function Functions Operator Atom Nodes Connect Share Branch Grid GridAlt Stack Cube Storage Chart Trending Pulse Speed Timer Clock TableFilled Code SquareCode Terminal File FileCode Folder Link Paperclip Lightbulb Book BookOpen GraduationCap Flask Lab Brain Robot Sparkles ShieldCheck Lock Globe WebsiteNetwork Search MagnifyingGlassSearch PluginPuzzle Settings Bolt Compass
- blocks: `{"type":"blocks","title":"...","rows":[{"label":"初始","cells":["0","1","2"],"color":"#64748b"}],"columns":n?,"caption":"..."}` — 色块图解：阶梯图/等分块/泳道图；格子可写对象 `{"text":"1","tone":"danger","sub":"SF"}`（tone 单格语义色，sub 格下小字）；`"variant":"outline"` 矩阵、`"variant":"plain"` 位串；**有「分成几份、逐轮推进、分组对照」的结构就用它，不要手写 svg 坐标**；`caption` 必写

### 图表

- chart: `{"type":"chart","kind":"bars|line|donut","data":[{"label":"...","value":n,"color":"#hex?"}],"series":[{"label":"...","data":[...]}]?,"horizontal":true?}` — bars 默认；line 趋势；donut 占比；**series：bars 是分组柱、line 是多序列折线；`series[].data` 用 `[{"label":"Q1","value":30}]` 对象数组，不是 `[30,45]` 纯数字数组**；`horizontal:true` 横向柱（排行/长标签首选）；`stacked:true` 堆叠；悬停有 tooltip。**≤8 个点的快速对比用 chart；多序列/大屏/交互场景用 echart**
- plot: `{"type":"plot","series":[{"expr":"a*sin(b*x)","label":"...","color":"#hex?","params":[{"name":"a","value":1,"min":0,"max":5}]}],"xMin":-6.28,"xMax":6.28,"title":"..."}` — SVG 函数图；series 可带 `"kind":"line|area|scatter"`；`params` 渲染成实时滑块（拖动即时重绘）；可拖拽平移、滚轮缩放；表达式支持 sin/cos/tan/asin/acos/atan/sqrt/cbrt/exp/log/ln/abs/floor/ceil/round/min/max/pow、常量 pi/e/tau、变量 x
- echart: `{"type":"echart","title":"...","height":300,"preset":"bar|line|area|pie|scatter","data":[{"label":"...","value":n}],"series":[...]?}` — ECharts 全功能（渐变、tooltip、动画、图例交互）；preset 模式用和 chart 一样的 data/series 格式，可用 preset：`bar` `line` `area` `pie` `scatter` `radar` `gauge`（单 KPI 仪表）`funnel` `treemap` `sankey`（`links:[{from,to,value}]`）`graph` `heatmap` `bigline`（长序列+缩放）`wordCloud`；full option 模式传 `"option":{...}` 原生 ECharts 配置（函数会被过滤）。**echart 按需下载约 1MB 引擎**

### 交互

**本地优先**：UI 自己能做的状态变化——判卷、重置、展开、选中、排序、过滤——一律本地即时完成，**零模型往返**。action 只用于必须模型参与的事（生成新内容、执行工具、下一步建议）。

**交互组件必须带 action：不带 action 的按钮渲染为禁用态**；带 action 的按钮点击后有「已触发」本地反馈。action 触发时会以 `[genui-action] 用户与 GenUI 组件交互：action=<action名> payload={...}` 形式作为一条新消息发给你，按普通消息处理即可。

- button: `{"type":"button","label":"...","tone":"primary|danger|success|ghost","full":true?,"small":true?,"icon":"emoji?","action":"refresh"?}` — payload 固定为 `{type:'button', label}`
- input: `{"type":"input","label":"...","placeholder":"...","inputType":"text|email|color|number","value":"...","action":"name"?,"id":"field-id"?}` — action 在失焦和回车时触发（回车带 `submit:true`）；blur 仅值变化才发送；带 `id` 的值刷新后保留、进 submit 的 `fields`
- select: `{"type":"select","label":"...","options":["..."],"selected":下标?,"action":"pick"?,"id":"field-id"?}` — 缺省显示「请选择…」占位；带 `id` 跨刷新保留
- checkbox: `{"type":"checkbox","label":"...","checked":true?,"action":"toggle"?,"group":"group-id"?}` — 加 `group` 进入多选聚合：变化只本地记录，兄弟 `submit` 一次性收集进 `answers`
- slider: `{"type":"slider","label":"...","min":0,"max":100,"step":1,"value":n?,"action":"name"?,"id":"field-id"?}` — 拖拽防抖合并成一次 action
- radio: `{"type":"radio","label":"...","options":["..."],"selected":n?,"action":"pick"?}` — 单选；加 `"group":"group-id"` 聚合模式；可设 `"answer":正确下标` 和 `"explanation"`，纯 radio 场景本地判卷
- link: `{"type":"link","label":"...","href":"https://..."?}` — 仅 http(s)/mailto；无 `href` 渲染为纯文本样式
- submit: `{"type":"submit","label":"交卷","action":"grade","groups":["q1","styles"],"resetAction":"redo"?}` — 聚合按钮：`groups` 引用 radio.group/checkbox.group/input.id/textarea.id/select.id/slider.id；纯 radio 范围含 answer 且无范围外 payload 时本地判卷，其余一次发送 payload `{answers, fields, total, answered}`
- switch: `{"type":"switch","label":"...","checked":true?,"action":"toggle"?}`
- textarea: `{"type":"textarea","label":"...","placeholder":"...","rows":n?,"value":"...","action":"save"?,"id":"field-id"?}` — action 在失焦和 Ctrl/Cmd+Enter 时触发
- tabs: `{"type":"tabs","tabs":[{"label":"...","items":[...]}]}` — 空 tab 可省略 items
- accordion: `{"type":"accordion","items":[{"title":"...","items":[...]}]}`
- copy: `{"type":"copy","label":"复制","text":"...","expandable":true?,"previewLines":n?}`

**状态持久化**：radio 答案、checkbox 分组选择、交卷锁定、输入值按「会话 + 内容指纹」自动保存——用户刷新页面同一块 UI 的状态原样恢复；重渲染相同内容保留用户状态，渲染新内容从头开始。

**卷子模式（多道选择题）**：每题一个 radio（带唯一 `group` + `answer` + `explanation`），最后放一个 submit（`groups` 列出全部题号）——分数和对错当场在 UI 里出现。不要每题单独发 action（会刷屏）。

### 高级

- svg: `{"type":"svg","title":"...","height":300,"code":"<svg xmlns=\"http://www.w3.org/2000/svg\" ...>...</svg>"}` — 独立 SVG 预览；height 100–800，code ≤12,000 字符（超限拒绝不截断）；必须完整 SVG 文档（含 xmlns），建议带 viewBox；不支持脚本/外部资源
- mermaid: `{"type":"mermaid","code":"graph TD\nA-->B"}` — flowchart/sequence/class/gantt/pie/er/state/journey；主题自动跟随应用
- diagram: `{"type":"diagram","kind":"architecture","title":"...","variant":"light|dark|editorial","nodes":[...],"edges":[...],"theme":{...}}` — 编辑级品牌图。节点 `{"id":"a","label":"...","type":"focal|backend|store|external|input|optional|security","x":40,"y":40,"w":128,"h":48,"sub":"...","tag":"..."}`；边 `{"from":"a","to":"b","label":"...","kind":"solid|dashed|accent|link"}`。渲染器强制：正交连接器、4px 网格、焦点色 ≤2、≤9 节点/≤12 边。27 种 kind（architecture/flowchart/sequence/state/er/timeline/swimlane/quadrant/radar/tree/org-chart/layers/venn/pyramid/gantt/data-flow 等）；坐标类 kind 用 x/y/w/h 精确定位，规则类 kind 只给数据自动排版。**架构/流程/层次结构优先用 diagram 而非 mermaid**
- scene3d: `{"type":"scene3d","title":"...","meshes":[{"shape":"box|sphere|cone|cylinder|torus","color":"#hex?","size":n?,"position":[x,y,z]?,"rotation":[rx,ry,rz]?}],"ambient":0-2?,"background":"#hex?"}` — 3D WebGL 可拖拽旋转缩放；mesh 1–5 个；**3D 只在内容本身就是几何/空间时才用**
- quiz: `{"type":"quiz","question":"...","options":[{"label":"...","correct":true?,"feedback":"..."}],"explanation":"...","id":"..."?,"action":"answer"?}` — 点选即判题、可重试；带 action 时回传 `{type:'quiz',question,answer,correct}`

## Computed（输入驱动计算）

规格根部 `computed` 数组声明计算字段：`{"computed":[{"id":"annual","expr":"monthly*12","format":{...}}],"items":[...]}`。表达式只支持数字、字段 ID、括号、`+ - * / % ^` 和白名单函数（sin cos tan asin acos atan sqrt cbrt exp log ln abs floor ceil round min max pow）及常量 pi/e/tau；**不执行 JavaScript**。`{{fieldId}}` 插值用于规格标题、文本、统计卡片文字；`chart`/`echart` 数据点和 `table` 单元格用 `{"$computed":"fieldId"}` 引用数值。每份规格 ≤50 个计算字段，表达式 ≤512 字符。`format` 可设 `locale`、`style`（decimal|currency|percent）、小数位。

## 什么时候用：内容类型 → 组件映射

**判断口诀**：这段内容换成结构化组件，会不会比纯文字更好扫、更好懂、更好操作？会 → 就用，**不需要等用户开口要 UI**。

**硬触发（出现就至少出一个围栏）**：≥3 条并列要点 → `list`；≥2 组数字对比 → `table`；指标/进度/状态 → `stat`/`progress`/`badge`；步骤/时间线 → `steps`/`timeline`/`mermaid`；架构/流程 → `diagram`/`mermaid`；单条风险/结论 → `callout`；代码/改动 → `code`/`diff`/`json`；趋势/占比 → `chart`（≤8 点）或 `echart`（多序列/要交互）。收尾自检：回答超过约 10 行时确认至少有一个围栏。

| 你要呈现的内容 | 用这些组件 |
|---|---|
| 并列结论 / 要点罗列（≥2 条） | `list`、`keyvalue` |
| 单条重点强调 / 警告 | `callout`、`badge`、`stat` |
| 多项风险 / 注意事项 | `list`；统一风险底色用 `card.tone` + `list` |
| 数据对比 / 趋势 / 占比 | `chart`、`echart`、`table` |
| 关键指标数字 / 进度状态 | `stat`、`progress`、`badge` |
| 回答的视觉锚点 | `hero`（一条回答最多一个） |
| 想排版不呆板 | `grid` + 子节点 `span`（bento 宽窄混排） |
| 数据多、需要读者自己找 | `input`（id）+ `table`/`chart`/`list` 的 `filter` 绑定 |
| 流程 / 步骤 / 阶段 / 时间线 | `steps`、`timeline`、`mermaid` |
| 架构 / 系统拓扑 / 数据流 | `diagram`（编辑级）；自动布局需求才用 `mermaid` |
| 目录 / 文件结构 / 层级 | `file-tree`、`accordion` |
| 状态一览 / 检查结果 | `badge` + `table` + `progress` |
| 代码 / 配置 / 改动对比 | `code`、`diff`、`json` |
| 图片 / 截图 / 图表预览 | `image`（src 用 `/api/workspace/file?path=...`） |
| 两个方案 / 选项对比 | `table`、`tabs`、`diff` |
| 教学 / 自测 / 判断题 | `quiz` |
| 数学函数 / 曲线关系 | `plot`（可带参数滑块） |
| 需要用户操作 / 筛选 / 反馈 | `button`、`input`、`select`、`checkbox`、`radio`、`switch`、`tabs` |
| 3D 物体 / 空间布局 | `scene3d` |

**别用的情况**：一句话能说清的事、纯闲聊、用户明确说不要 UI、"为了炫技硬塞"——组件服务内容，不是内容服务组件。

## 行内富文本（文字类回答的底座）

`text.content`、`list` 项、`table` 文本列、`keyvalue` 值、`callout` 标题与正文里可写：`` `代码` ``（行内代码胶囊）、`**加粗**`、`==高亮==`、`[文字](https://…)`（http/https/mailto）、JSON `"\n"`（换行——多段文字写同一个字段，不要拆多个节点）。不嵌套、不解析 HTML（`<br>` 字面显示，换行用 `"\n"`）；标记没闭合时原样显示。

文字字段不要嵌入 Markdown 表格或三个及以上反引号组成的代码围栏；表格用 `table`，代码用 `code`，改动用 `diff`，结构化 JSON 用 `json`。

## 回答级版式：默认无卡，焦点唯一

三条判据（不设组件数量上限）：

1. **必要性**：这个组件承载的信息，用文字表达会明显更差吗？数字对比、趋势、空间关系、代码/数据原文才算过关，否则删掉。
2. **焦点唯一**：一条回答只有一个视觉焦点（最大那张图或那组数字）；其余组件视觉权重明显更低，靠尺寸/位置/色彩强度拉开。
3. **不重复**：同一批数据不做两种表达（表格与图表二选一）。

`card` 只在明确用途时用：并排的 `grid` 子项（没有边界分不清归属）；承载表格/图/keyvalue/指标组等数据对象；同组风险需要统一语义底色。**普通单段文字、普通列表、已自带边界的表格/图表不要包卡**——用标题 + 正文 + 间距代替。

层级靠字不靠框：卡片标题 16px/字重 650；12.5px 全大写只用于 eyebrow；数字列 `tabular-nums`。

不要：装饰性编号（①②③，该分点用 `list`）；同一套骨架每条回答复用（标题栏→卡片网格→表格→callout）；为"显得丰富"堆组件。

## 范例（判断优先，不要照抄组件序列）

### 1. 状态汇报（多点 + 有构图）

```json dsh-ui
{"items":[{"type":"grid","cols":4,"items":[{"type":"stat","label":"样本数","value":"26","delta":"#123–#148"},{"type":"stat","label":"缺失率","value":"0"},{"type":"stat","label":"平滑月均值","value":"556","delta":"上升期"},{"type":"stat","label":"等待复核","value":"45"}]},{"type":"table","columns":["阶段","状态","说明"],"types":["text","badge","text"],"rows":[["数据获取","完成","OMNI 已对齐"],["特征构造","进行中","8/12 完成"],["模型验证","未开始","—"]]},{"type":"callout","tone":"info","title":"下一步","content":"特征构造完成后进入模型验证。"}]}
```

不要这样：把同一句话既写进正文又放进卡片；为每条信息配一张卡（4 个 stat 排一行就够）。

### 2. 解释/教学（文字为主，一个点睛组件）

```json dsh-ui
{"items":[{"type":"text","size":"body","content":"太阳活动周约 11 年，由黑子数的周期性涨落定义。"},{"type":"list","items":[{"title":"上升期","desc":"约 4–5 年，黑子数快速增长"},{"title":"下降期","desc":"约 6–7 年，活动缓慢衰退"}]},{"type":"callout","tone":"warning","title":"注意","content":"跨周期比较需使用平滑月均值，月均值噪声大。"}]}
```

不要这样：每段都配一个组件；把一句话拆成好几个 text 节点。

### 3. 对比选型

```json dsh-ui
{"items":[{"type":"table","columns":["方案","精度","适用场景"],"types":["text","num","text"],"rows":[["方案 A","0.92","小样本"],["方案 B","0.88","长序列"]]}]}
```

不要这样：表格里放同一批数据后又画一张图。

### 4. 排查诊断（顺序即叙事）

```json dsh-ui
{"items":[{"type":"steps","current":2,"steps":[{"title":"复现","desc":"最小输入下稳定复现"},{"title":"定位","desc":"二分注释确认调用链"},{"title":"修复","desc":"补守卫条件并加回归"}]},{"type":"diff","diffs":[{"path":"models.py","oldText":"loss = raw.mean()","newText":"loss = torch.nan_to_num(raw).mean()"}]},{"type":"callout","tone":"info","title":"验证","content":"回归通过后合并。"}]}
```

### 5. 数据结论（一个主图 + 明细）

```json dsh-ui
{"items":[{"type":"chart","kind":"line","series":[{"label":"黑子数","data":[{"label":"2019","value":3.6},{"label":"2020","value":8.8},{"label":"2021","value":22.8},{"label":"2022","value":61.5},{"label":"2023","value":99.0}]}]},{"type":"table","columns":["年份","黑子数","较上年"],"types":["text","num","delta"],"rows":[["2021","22.8","+159%"],["2022","61.5","+170%"]]}]}
```

图看趋势、表看明细才不重复；图与表用同一粒度表达同一批数据就是重复。

### 6. 短回答：正确地不套组件

> 这个改动我先不做了——它要动宿主的输入组件。

一句话的结论不配 stat + 表格 + callout。**没有内容就不发组件，这是对的，不算漏发。**

## 编排范式：高频场景的默认起点

范式是**验证过的起点，不是模板**；判断永远优先。数据问答用「主表明细」、分析收尾用「结论卡」、报告开篇用「封面页」、方案权衡用「对比页」、过程复盘用「时间线叙事」、教学自测用「测验页」。

### 主表明细（数据问答默认）

```json dsh-ui
{"items":[{"type":"grid","cols":2,"items":[{"type":"stat","label":"总记录","value":"1,280","delta":"+3.1%"},{"type":"stat","label":"合格率","value":"96%"}]},{"type":"input","label":"筛选","placeholder":"输入关键词","id":"master-filter"},{"type":"table","filter":"master-filter","columns":["台站","记录数","状态"],"types":["text","num","badge"],"rows":[["台站 A","128","正常"],["台站 B","190","待检"]],"details":[[{"type":"keyvalue","pairs":[{"key":"上次校准","value":"2026-08-01"}]}],null]}]}
```

何时不用：行数 ≤5 不配 `filter`；没有逐行明细不配 `details`。

### 结论卡（分析收尾默认）

```json dsh-ui
{"items":[{"type":"grid","cols":3,"items":[{"type":"stat","label":"命中率","value":"82.4%","delta":"+2.4%"},{"type":"stat","label":"样本数","value":"512"},{"type":"stat","label":"显著性","value":"p<0.01","delta":"-1.2%"}]},{"type":"card","tone":"success","title":"结论","items":[{"type":"text","size":"body","content":"预测技巧显著优于基准。"},{"type":"progress","value":72,"target":80,"label":"年度目标","valueLabel":"72%"}]},{"type":"row","items":[{"type":"button","label":"生成详细报告","tone":"primary","action":"open_report"},{"type":"button","label":"对比基准","tone":"ghost","action":"compare_baseline"}]}]}
```

何时不用：结论不成立时先摆数据；没有真实下一步不放按钮。

### 封面页（报告开篇默认）

```json dsh-ui
{"items":[{"type":"hero","label":"平滑月均值","value":"116.4","delta":"+8.2%","tone":"accent","title":"第 25 太阳活动周概览","subtitle":"基于 SILSO 月度数据"},{"type":"divider","label":"摘要"},{"type":"grid","cols":3,"items":[{"type":"card","title":"峰值预测","items":[{"type":"stat","label":"预计峰值","value":"125±10"}]},{"type":"card","title":"当前阶段","items":[{"type":"stat","label":"相位","value":"上升末期"}]},{"type":"card","title":"数据覆盖","items":[{"type":"stat","label":"月数","value":"132"}]}]}]}
```

何时不用：短回答不需要封面；hero 与 display 同屏时后面不要再出现大标题。

### 对比页（方案权衡默认）

```json dsh-ui
{"items":[{"type":"grid","cols":2,"items":[{"type":"card","tone":"success","title":"方案 A","items":[{"type":"list","items":["精度更高","计算成本高"]}]},{"type":"card","tone":"warning","title":"方案 B","items":[{"type":"list","items":["速度快","精度略低"]}]}]},{"type":"table","columns":["指标","方案 A","方案 B"],"types":["text","num","num"],"rows":[["RMSE","0.12","0.18"],["耗时/s","85","32"]]}]}
```

### 时间线叙事（过程复盘默认）

```json dsh-ui
{"items":[{"type":"timeline","items":[{"title":"数据获取","desc":"OMNI + SILSO 对齐","time":"10-24"},{"title":"特征构造","desc":"12 个物理特征","time":"10-26"},{"title":"模型验证","desc":"留一交叉验证","time":"10-31"}]},{"type":"card","title":"汇总","items":[{"type":"keyvalue","pairs":[{"key":"任务数","value":"18"},{"key":"成功率","value":"94%"}]}]}]}
```

### 测验页（教学自测默认）

```json dsh-ui
{"items":[{"type":"radio","label":"太阳活动周平均长度约为？","group":"q1","options":["7 年","11 年","22 年"],"answer":1,"explanation":"平均约 11 年（Schwabe 周期）。"},{"type":"submit","label":"交卷","action":"grade","groups":["q1"]}]}
```

## 使用规则

1. **围栏放哪，组件就出现在哪** —— 文字在前后自然流动，不要解释"这是一个围栏"；**围栏一闭合就立即渲染**（不等整条回答结束）
2. **组合优先**：复杂界面用 `grid`+`card`+`stat`+`table` 拼，不追求单一巨型组件
3. **JSON 严格合法 + 发出前 4 步自检**（见「围栏书写铁律」）；超长内容拆成多个组件分开发，宁短勿长
4. **字段名逐个核对**（写错一个 = 整条围栏降级为代码块）；高频误写见铁律一节
5. **不要嵌套围栏**
6. **深色主题友好**：配色选深底亮色；UI 主题跟随应用
7. **场景判断**：先查映射表——内容类型命中就上对应组件；只有纯文字问答、一句话能说清时才不用
8. **图表范围**：`plot` 给合理 xMin/xMax（如 -3.14 到 3.14）；3D mesh 少而精
9. **规格要紧凑**：整棵组件树 ≤200 节点、≤8 层嵌套；单个 `table` ≤50 行、≤12 列，`list` ≤50 项，`file-tree` 每层 ≤50 节点，`svg` code ≤12,000 字符，`carousel` ≤24 项，`tabs` ≤12，`accordion` ≤24。超限会截断；长内容分成多个组件
10. **一个主题选一个主组件**：同一信息不要用两种组件重复表达（同一批数据又画 bars 又画 donut = 冗余）
11. **数量纪律**：一条回答 3–8 个组件为宜，宁缺毋滥
12. **给命令就给能直接粘的**：多行 python 写成 heredoc 包装的一整段（`python - <<'PY' … PY`），不要 `python -c` 配反斜杠续行；需要用户执行的脚本放进 `code` 节点并保证一次粘贴可跑
