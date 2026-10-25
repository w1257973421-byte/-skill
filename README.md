# chow-comedy-director

Codex Agent Skill：从周星驰电影片段及其他无厘头喜剧中，研究镜头、表演、调度、笑点、声音与剪辑，建立可迁移的原创喜剧导演方法库。第一阶段提供分析工具与结构，不附电影素材、不宣称已有电影研究结论，不生成视听成品。

## 云端使用

所有命令在 Codex 云端现有仓库根目录执行，无需用户本地电脑。
Codex 从 `.agents/skills/chow-comedy-director/SKILL.md` 加载 Skill，用 `$chow-comedy-director` 调用。
例如：“使用 $chow-comedy-director 分析已提供片段的反应镜头与停顿，建立有时间证据的方法卡。”
没有片段时只准备分析计划与模板。

### 依赖

- Python 3.10+；预处理仅依赖标准库。
- FFmpeg 与 ffprobe 6+，包含 libx264、scale、select、showinfo、MJPEG 编码器。
- 开发依赖与方法卡校验：`requirements-dev.txt`。云端已提供 FFmpeg 时无需再次安装。
- Debian/Ubuntu 缺失 FFmpeg 时使用系统包管理器安装 `ffmpeg`（需要相应系统权限）。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
ffmpeg -version
ffprobe -version
.venv/bin/python -m unittest discover -s tests -v
```

### 预处理

将获准使用的素材放在云端 `work/input/`。输出目录必须不存在，不会覆盖输入或既有输出。
以下是待输入真实素材后运行的命令，不代表仓库附带该素材：

```bash
.venv/bin/python .agents/skills/chow-comedy-director/scripts/preprocess.py \
  work/input/clip.mov --output work/clip-review --threshold 0.30 --min-shot 0.15
```

生成研究用 `proxy.mkv`（编码高度不超过 480、保持宽高比例、不放大小片段、偶数尺寸）、全部原音轨的无重编码复制、切点候选、每个候选镜头的中点 JPG、逐帧时间映射与可填写分析表。代理不是视频成品；关键帧不是创作图片。媒体与工作输出默认被 Git 忽略。
使用 `-copyts` 和 VFR passthrough，不改变速度或丢弃帧。Matroska 时间基精度通常为毫秒；脚本验证帧数与逐帧 PTS 差不超过 2ms，原始时间戳精度保存在 CSV。不会把原始时间码仅替换成从零计时。

- `source.ffprobe.json` / `proxy.ffprobe.json`：原始/代理流信息与标签。
- `manifest.json`：来源 SHA-256、原 timecode 标签、切点、镜头区间、关键帧定位、工具版本。
- `frame-map.csv`：源帧序号、源 PTS 秒、代理 PTS 秒。
- `shots.csv`：逐镜头六维分析、观察、假设、信心与反例字段。
- `comedy-beats.csv`：跨镜头的铺垫、预期、偏移、兑现、反应与余波。
- `analysis-report.md`：研究报告模板。

源 PTS 与片段相对秒数分别存储；SMPTE timecode 标签原样保留在清单中，不自动进行丢帧时间码换算。不会保证原 MOV timecode 数据轨保留为代理容器中的数据轨；原片与清单是权威来源。
音轨通过 stream copy 保存；无法封装进 Matroska 的音频会明确失败，不会静默转码或去除。字幕、附件和其他数据轨不复制。旋转不烘焙，编码方向及原显示矩阵见源元数据。

### 分析与方法归档

先复核候选边界，再填 CSV；跨镜头笑点需关联多个 shot_id。
关键帧不能证明完整表演、声画关系或节奏，没有连续观看/听音的维度保留为未核实。
复制 `assets/method-card.json` 到 `work/methods/M0001.json` 并替换占位；模板不是电影证据。

```bash
.venv/bin/python .agents/skills/chow-comedy-director/scripts/validate_method.py \
  .agents/skills/chow-comedy-director/assets/method-card.json
```

方法采用 JSON Schema 2020-12，包含适用条件、导演操作、观众预期效果、失败条件、证据区间、六维标签、原创迁移与可证伪实验。`confirmed` 要求复核证据与非空实验结果；结构校验不能代替研究质量审核。库以每条方法一个 JSON 文件组织，method_id 保持唯一，稳定后可将不含电影媒体和敏感来源路径的研究卡纳入版本控制。

## 结构

```text
.agents/skills/chow-comedy-director/
  SKILL.md
  agents/openai.yaml
  scripts/preprocess.py
  scripts/validate_method.py
  references/analysis-protocol.md
  references/method-library.schema.json
  assets/analysis-report.md
  assets/comedy-beats.csv
  assets/method-card.json
tests/test_preprocess.py
requirements-dev.txt
README.md
.github/workflows/test.yml
```

## 验证与边界

测试只在临时目录生成几秒钟色块、测试图及正弦音，结束自动清理；不下载或复制电影，不输出成品。
覆盖突切定位、关键帧颜色/镜头归属、480p、音频压缩载荷一致性与时间戳、非零源 PTS、timecode 标签、VFR、无音轨、不放大、重复输出拒绝、损坏输入、模板和方法结构校验。

切点使用 FFmpeg scene 分数阈值，是候选检测而非语义镜头识别。渐变、闪光、快速运动、短镜头可能误判；`--min-shot` 只用于过滤过近候选。降低阈值可提高灵敏度，不能保证召回率。
第一阶段面向短片段：逐帧索引载入内存，每镜头关键帧提取重新解码，长片请先分成研究片段并记录原时间基准。封面视频轨先于主视频、异常/重复时间戳或极小尺寸输入不在当前支持范围。无实时服务、无额外密钥要求。

下一阶段建议：用获准使用的多段真实素材校准误检漏检；增加人工边界修订工具与修订记录；引入声音事件标注；对跨情境方法做原创对照实验与观众反馈，避免把单片观察当成普遍规律。
