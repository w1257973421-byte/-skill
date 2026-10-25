# 首轮真实片段：30–90 秒验证工作流

本轮状态：只准备工具、模板和环境；尚无真实电影素材，未生成电影分析或方法结论。
第一阶段的 14 个文件保持原样，新脚本通过导入现有预处理模块扩展流程。

## 云端输入

向当前聊天上传获准使用的 30–90 秒片段，或提供云端工作区中的文件路径。
同时提供电影名称、版本/来源、研究问题、已知的原片入点与 timecode 基准。
不需要本地安装 Python、FFmpeg 或其他软件。素材放到仓库忽略目录 `work/input/`，不进 Git。
如果只有较长原片，提供文件和希望研究的时间区间，由 Codex 在云端截取并另存；截取时另记原片 SHA-256、入出 PTS、音轨及 timecode 标签，不把裁剪后从零开始的秒数当作原片时间。
本脚本接受已经选定的片段，不会自行下载电影或静默裁剪长视频。

```bash
cd /workspace/-skill
.venv/bin/python .agents/skills/chow-comedy-director/scripts/prepare_review.py \
  work/input/clip.mov --output work/review-001 \
  --source-id 'user-provided-clip-001' --source-kind real_movie
```

`real_movie` 仅记录提交者的来源声明；脚本不能验证内容身份、出处或使用权限。
默认接受 30–90 秒实际视频帧时长。只有 `synthetic_test` 可显式使用 `--allow-short-test` 测试短素材，不能把它作为真实电影验证结果。

## 保留的第一阶段行为

480p（编码高度不超过 480、不放大）、全部音轨复制、帧率不重采样、原始 PTS 与代理逐帧映射、timecode 标签归档、切点候选及镜头中点关键帧。容器时间戳允许毫秒级舍入；以 frame-map.csv 和源文件为准。
原字幕、timecode 数据轨或旋转烘焙等限制仍与第一阶段 README 相同；原片不修改。

## 密集抽帧与输出

切点前后默认各 0.5 秒**逐帧**提取，不以每秒一张代替。覆盖全部算法候选，包括被最短镜头间隔过滤的候选；对首尾也抽帧。窗口通过 `--window 1.0` 扩到各 1 秒。
这降低漏掉短暂表情/动作的风险，但算法可能完全漏检切点，且远离切点的表演仍需连续观看、听音及必要的额外手工逐帧检查。不能宣称完整捕获所有笑点。
默认上限 5000 张密集帧，超限报错并保留阶段一输出供诊断，不会静默稀疏采样。确认资源后可调 `--max-dense-frames`；失败时使用新目录重试，不覆盖旧结果。

| 文件 | 含义及初始状态 |
| --- | --- |
| manifest.json / frame-map.csv | 算法参数、源片 SHA-256、原始时间信息、逐帧映射 |
| cut-candidates.csv | 切点候选表；algorithm_only_unreviewed，过滤通过不等于人工确认 |
| director-analysis.csv | 逐镜头六维导演分析表；只填算法区间，解释留空 |
| comedy-beats-review.csv | 喜剧节拍表；仅表头，没有伪造节拍 |
| boundary-frames/*.jpg | 切点前后逐帧证据，非创作图片 |
| boundary-frames.csv | 每张证据帧的 E 编号、源 PTS、帧序号、关联 C 候选编号 |
| method-cards/method-card.template.json | 原方法卡模板副本，含显式占位，非完成研究条目 |
| review-preparation.json | 准备完成标记、来源声明、未核实状态；analysis_complete=false |

原 shots.csv、comedy-beats.csv、analysis-report.md 仍保留；本轮优先填写扩展版表格，避免两份记录不一致。第一阶段模板方法卡中的零 SHA 是占位，不能当成证据。

## 证据与人工分析顺序

1. 正常速度连续观看和听音，先确认素材身份、画面方向和声画同步。标记无法观看或听音的部分。
2. 对照 cut-candidates.csv 与 boundary-frames.csv，复核突切、渐变、闪光和遮挡；确认短镜头，必要时补切点。边界每次修改增加 boundary_revision，更新受影响 shot_id/节拍关联并记录原因。不要把过滤器 accepted 当成 reviewed。
3. 填 director-analysis.csv：镜头、表演、调度、笑点、声音、剪辑。human_observation 填可观察事实；ai_interpretation 填有证据支持的解释；unverified_inference 填尚未核实的推断。AI 输出不能自动升级为人工观察。
4. 每条填写 source_start/end_pts_seconds，evidence_refs 引用 `源SHA/PTS区间;S0001;C0001;E00001`；声音证据引用原音轨和时间区间，不能用 JPG 证明声音。
5. 填 comedy-beats-review.csv，允许跨镜头和不完整节拍链；保留替代解释、观看状态与信心，不用模拟素材的节拍填表。
6. 从已复核观察提炼方法卡，将模板复制为 M0001.json 后替换所有占位。记录条件、操作、预期变化、失败条件、替代解释与原创实验。未验证的技法 status=hypothesis；不复写台词、人物造型或动作链。
7. 用 `scripts/validate_method.py` 校验方法卡；结构通过不能证明电影分析正确。报告区分算法输出、AI 解释、待核实推断与经独立原创实验支持的结果。

## 环境和自动化验证

```bash
.venv/bin/python -m pip check
.venv/bin/python -m unittest discover -s tests -v
```

测试只使用临时合成视频验证工具，包括一个 30 秒非零 PTS、有声音的三色片段与仅一帧的短暂绿色画面。
测试确保被间隔过滤的候选附近仍逐帧保留，所有解释字段为空、节拍表无数据、方法卡明确为模板；不代表已分析任何真实电影。
测试结束自动清理素材和派生图片。

## GitHub 与环境发布

本项目从原有检出继续工作，禁止重新初始化或覆盖第一阶段文件。
云端依赖安装脚本保存在环境配置草稿中；在环境设置审阅并保存后，发布环境以供后续任务复用。
Git 提交/推送、Pull Request 创建和环境发布是分别验证的步骤。远程最初为空时，首个功能分支可能成为默认分支；没有 main 就没有该 PR 的目标分支，不能宣称 PR 已创建，也不要擅自制造或改写 main。
