---
name: ReleaseTracker Design System
description: Precision Operational Console for Continuous Deployment and Release Orchestration
colors:
  primary: "oklch(0.209 0.013 285.938)"
  primary-foreground: "oklch(0.984 0.002 247.858)"
  background: "oklch(1 0 0)"
  foreground: "oklch(0.139 0.006 285.885)"
  card: "oklch(1 0 0)"
  card-foreground: "oklch(0.139 0.006 285.885)"
  muted: "oklch(0.967 0.003 286.027)"
  muted-foreground: "oklch(0.553 0.032 285.938)"
  border: "oklch(0.921 0.007 286.067)"
  input: "oklch(0.921 0.007 286.067)"
  ring: "oklch(0.705 0.031 285.938)"
  success: "oklch(0.42 0.12 155)"
  info: "oklch(0.46 0.15 250)"
  warning: "oklch(0.42 0.11 70)"
  destructive: "oklch(0.48 0.20 27)"
typography:
  display:
    fontFamily: "Plus Jakarta Sans, system-ui, sans-serif"
    fontSize: "1.875rem"
    fontWeight: 700
    lineHeight: 1.2
  headline:
    fontFamily: "Plus Jakarta Sans, system-ui, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 600
    lineHeight: 1.3
  title:
    fontFamily: "Plus Jakarta Sans, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 600
    lineHeight: 1.4
  body:
    fontFamily: "Plus Jakarta Sans, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Plus Jakarta Sans, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 500
    lineHeight: 1
rounded:
  sm: "6px"
  md: "8px"
  lg: "10px"
  full: "9999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.primary-foreground}"
    rounded: "{rounded.md}"
    padding: "8px 16px"
    height: "36px"
  badge-outline:
    rounded: "{rounded.sm}"
    padding: "2px 8px"
---

# Design System: ReleaseTracker

## Overview

**Creative North Star: "The Telemetry Cockpit (控制台驾驶舱)"**

ReleaseTracker 是一套为现代软件发布与运行时更新编排打造的高密度、专业级运维控制台系统。它的美学哲学植根于现代工业设计与高精密仪器面板：**严整的网格对齐、清晰的视觉层级、克制而精准的状态色彩指示、以及零横向溢出的稳固布局**。它摒弃了浮夸的装饰性渐变与大面积留白，将信息的可扫描性（Scanability）与操作的确定性置于首位。

整个界面系统围绕“声明式版本追踪”与“受控物理部署”两条主线展开，界面既能在 1280px+ 桌面视口下展现高密度的版本聚合矩阵与分栏详情，又能在移动端紧凑自适应折叠，始终保证运维工程师在任何终端均能迅速识别版本演进、审查镜像 Digest、评估就绪状态并做出决策。

**Key Characteristics:**
- **信息高可扫描性**：关键实体（追踪器、执行器、镜像哈希、就绪状态）具有高度统一的视觉锚点；
- **确定性语义色彩**：严格遵循绿色（就绪/成功）、黄色（等待/预发布/警告）、红色（失败/降级）、蓝色（执行中/链接）的语义体系；
- **工业级严整排版**：严格避免整页横向滚动条，表格列宽具有明确的优先级收敛与自适应断点；
- **原生多主题支持**：基于现代化 OKLCH 色彩模型，原生支持自适应深色（Dark）与浅色（Light）模式，高对比度且不刺眼。

## Colors

调色板以中性、理性的深浅灰阶为基底，辅以高饱和度的语义状态指示色，确保无论在明暗模式下均具备符合 WCAG AA 级以上的对比度。

- **Primary & Neutral Surfaces**:
  - Light 模式采用纯净白背景（`oklch(1 0 0)`）搭配极深冷灰前景（`oklch(0.139 0.006 285.885)`）；
  - Dark 模式采用深邃且中立的深灰背景（`Neutral-950: oklch(0.139 0.006 285.885)`）搭配柔和白色前景（`oklch(0.984 0.002 247.858)`），消除了纯黑带来的生硬光晕。
- **Semantic Accents**:
  - **Success (健康就绪/发布)**: Light `oklch(0.42 0.12 155)` / Dark `oklch(0.78 0.14 155)` — 稳重鲜明的翠绿，用于健康通过与生产正式版；
  - **Info (执行中/链接)**: Light `oklch(0.46 0.15 250)` / Dark `oklch(0.76 0.13 250)` — 冷静的信号蓝，用于运行中、观察中及信息型关联；
  - **Warning (预发布/等待超时)**: Light `oklch(0.42 0.11 70)` / Dark `oklch(0.82 0.15 80)` — 警示琥珀金，用于预发布标签与等待中的就绪检查；
  - **Destructive (失败/阻断)**: Light `oklch(0.48 0.20 27)` / Dark `oklch(0.72 0.18 25)` — 鲜明冷红，用于任务失败、容器未就绪与破坏性操作确认；
  - 每个自定义主题强调色均分别定义 Light/Dark 色值，文字、图标和填充前景均需达到 WCAG AA。
  - **Muted & Borders**: `oklch(0.921 0.007 286.067)`（明）/ `oklch(0.269 0.024 286.32)`（暗）— 极细边框，提供结构骨架而不抢夺视觉焦点。

## Typography

系统采用无衬线系统字体栈搭配精密等宽字体（Monospace）的双轨排版：

- **UI & Controls (Sans-serif)**:
  - 采用 `"Plus Jakarta Sans", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", ui-sans-serif, system-ui, sans-serif`；
  - 几何骨架清晰，在中英混排环境下具备卓越的小字号清晰度与大字号张力。
- **Identifiers & Artifacts (Monospace)**:
  - 采用 `"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Monaco, monospace`；
  - 广泛应用于版本 Tag、Git Commit SHA、容器镜像 Digest、CRD 资源路径与端口地址，带有轻量级底色胶囊（`code` / `bg-muted/40`），支持防截断一键复制。

## Layout

- **网格与容器**：以 8px / 4px 为基础节奏单位，卡片内边距标准为 16px (p-4) 或 24px (p-6)。
- **响应式断点**：
  - 桌面端（`lg` / 1024px+）：采用 Master-Detail 拖拽分栏（如 Trackers 列表 38/62 黄金比例）、4 列指标卡与自适应宽表格；
  - 移动端（`sm` / <640px）：自动折叠为单列堆叠卡片，次要技术列（Commit SHA、发布渠道 Badge）自动收敛，保证主操作与关键版本号零溢出；
- **分栏与边框**：全站坚守统一的微边框分割（`border border-border/60`），杜绝重重阴影带来的视觉疲劳。

## Elevation & Depth

ReleaseTracker 属于高可读性控制台，拒绝重度物理投影，采用**基于表面色阶微调与微边框的平坦层级（Tonal Layering）**：

- **Level 0 (Canvas)**: `bg-background` 底板；
- **Level 1 (Card & Surface)**: `bg-card` 辅以 `border border-border/60` 与超轻阴影（`shadow-xs`）；
- **Level 2 (Active/Hover States)**: `hover:bg-muted/40`，选中行左侧带有亮色指示竖条（`w-1 bg-primary rounded-r`）；
- **Level 3 (Overlay & Sheet)**: 侧边抽屉与浮动弹窗使用 `backdrop-blur-sm` 与极低透明度的遮罩层，保留背底上下文认知。

## Shapes

- **圆角系统**：
  - 小徽标（Badge）：`rounded-full` 或 `rounded-md`（4px-6px）；
  - 按钮与输入框：`rounded-md`（6px-8px），触感适中；
  - 容器卡片与面板：`rounded-lg`（10px），规整内敛。
- **状态指示条**：左侧 3px 竖条（`rounded-r-sm`），作为条目选中态的标志性特征。

## Components

- **Table & Data List**:
  - 表头置顶（`sticky top-0 bg-background`）；
  - 悬停全行统一淡色背景，操作列背景半透明，不阻断整行高亮；
  - 统一右侧“操作”列定位，快捷图标（查看说明、外链、运行）与下拉菜单明确分工。
- **Metadata Cards & 4-Grid**:
  - 执行器目标配置统一封装为标准 4 格元数据网格（运行时连接、目标对象、位置/作用域、绑定追踪器），带明确的小号浅灰标签与加粗数值。
- **Release Matrix**:
  - 版本按逻辑 Release 与不可变 Artifact 聚合，每个版本展开显示独立的来源/版本/Digest/时间局部表头，支持精确到具体镜像 Digest 的复制与审计。

## Do's and Don'ts

### Do's
- **DO** 在展示容器镜像、哈希值或路径时始终使用等宽字体并提供一键复制与完整悬停提示；
- **DO** 在增加多列数据时使用自适应优先级断点（`hidden md:table-cell`），确保窄屏永不出现整体 X 轴滚动条；
- **DO** 严格保持多服务镜像比对的逐服务对齐结构（原镜像 → 目标镜像）；
- **DO** 在表单与列表更新时保持中英文双语字典 100% 对齐。

### Don'ts
- **DON'T** 为容器镜像擅自捏造“正式版/预览版”Badge（仅 Git Release 支持预发布语义）；
- **DON'T** 将长字符串直接用分号拼接展示在单个单元格中（多服务必须结构化拆分）；
- **DON'T** 在控制台列表过度使用花哨的动画或大面积毛玻璃模糊，影响快速扫读与操作响应；
- **DON'T** 将技术内部字段（如数据库自增主键、内部 Source Key）直接裸露在业务表格第一列。
