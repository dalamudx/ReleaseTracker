# ReleaseTracker 实机联调与回测记录

## 当前结论

原七项联调缺陷及后续发现的缓存回收、重复取消、停机关闭链、启动中断清理问题已修复。没有操作原业务负载，也没有修剪在线数据库。修改尚未提交或部署；生产升级前须备份数据库与密钥。

## 最新验证（不能与前轮计数相加冒充全量）

- 37 个明确许可的相关后端测试文件：**938 passed**，1 warning。
- 公开探针单元测试：**8 passed**；10-tag 基线对照：**2 passed**。
- 本轮合计 **948 个不同检查通过**，并非最新代码的完整后端测试集。
- 全量命令连显式排除受限文件也被工具权限策略阻止，未绕过。凭证专项未获准执行，不能计为通过。
- Ruff、Black（309 个 Python 文件，含新脚本）、`git diff --check` 通过。
- MkDocs strict 构建、13 个双语主题和 104 个旧锚点验证通过。
- 前轮后端全量 2086 passed、1 skipped；显式启用的隔离 DinD 用例单独通过。该计数不代表后续生命周期/Manifest改动后的全量结果。
- 最新前端全量：52 文件、**423 passed**（单独执行、`--maxWorkers=2`，没有放宽原 5 秒时限、跳过测试或设置重试）。完整日志 `act`、`NO_I18NEXT_INSTANCE` 和 Dialog 缺少描述警告均为 **0**。
- 本轮新增 8 项真实中英 Dialog/Sheet 无障碍测试，先确认 8 failed，再与既有流程合计 25 passed；描述 ID、读屏文本、sr-only、create/edit 与关闭重开均覆盖。
- 最新生产构建版 Chromium **全量 57 passed**（`CI=1`、2 workers、重试 0；126 秒内完成）。包含 8 项执行器保存链路和 4 项运行时 create/edit 双语/桌面手机/ESC 描述验收，不是跨轮计数相加。
- ESLint、TypeScript、diff 检查及一次 UI 机械检测通过。首次将全量单测与生产构建并行执行时，2 个文件出现 3 项失败；构建结束后只复跑失败文件 7 passed，再单独限制 2 workers 得到 423 passed。报告保留首次失败，不将失败解释为已修复的生产 bug。

## 弹窗可访问描述修复

`ExecutorSheet` 和 `RuntimeConnectionDialog` 的 `aria-describedby` 原来指向不存在的节点，是实际读屏描述缺失。现分别使用已有 `SheetDescription` / `DialogDescription`，以 `sr-only` 保留屏幕阅读器访问，同时不改变可见布局。执行器填充已有空白中英描述键，运行时复用已有文案；没有清空 ARIA、关闭库警告或改表单保存/权限逻辑。

浏览器真实生产 CSS 验证描述为 absolute、1px × 1px，并具有正确可访问描述；原目标配置保存不污染仍通过。原始 15 条缺描述警告已经归零，不声称完成所有 WCAG 项或人工屏幕阅读器验收。

日志：`/tmp/rt-dialog-description-red.log`、`/tmp/rt-dialog-description-green.log`、`/tmp/rt-dialog-e2e.log`、`/tmp/rt-dialog-full-frontend.log`（首次失败）、`/tmp/rt-dialog-failure-retest.log`、`/tmp/rt-dialog-full-frontend-final.log`（423 passed）。

## Manifest 请求优化

实现：`backend/src/releasetracker/trackers/docker.py`。

需要 OCI 元数据的新候选优先用一个 GET 同时取得 digest 和正文，复用同一响应读取配置，减少重复 HEAD/GET。响应不跨候选或抓取轮次缓存；已知元数据和 `first_observed` 仍用 HEAD。可选 GET 失败或缺少 digest 时回到原 HEAD 探测，不混合旧 GET 正文与后续 HEAD 的新 digest。此类失败回退可能增加一次可选探测，不承诺所有失败场景都更快。

- 新增 17 项回归覆盖 single/multiarch、Tag 变化、温缓存、401/403、429、超时、缺 header、token 刷新及基线对照。
- 相同响应下读取 10 个新版本：single Manifest 请求 **20 → 10**；multiarch **30 → 20**。
- 两条路径的版本、digest、时间来源和 OCI 元数据完全相同，Blob 均为 10 次。
- 保持现有单请求超时、Blob 最多 5 秒、鉴权拒绝、重定向安全、限流冷却及别名扫描顺序；没有增加并发。
- 旧 HTTP fixture 已按真实 OCI GET 的 digest header 和新请求方式校准，未移除安全断言。

## 实机证据与限制

- 前轮公开 `reg.aoodc.com` 的两种时间模式均返回 10 个真实 digest；`auto` 当时约 112 秒，慢点是 Manifest，9 次 Blob 最多约 0.02 秒。
- 优化后的首轮探针失败：Tag 列表 10 秒超时，退出 1，未进入 Manifest。约两小时后的一次复查 **成功返回 10 个真实 digest**，总耗时 **148.625 秒**；Manifest 23 GET + 4 HEAD，其中 4 次 GET 在 10 秒内超时并成功回退。9 次 Blob 全部 200，最慢 0.021 秒；Tag 列表耗时约 8.189 秒。线上正确性与真实超时回退已验证，但没有比旧观测 112 秒更快，不能宣称实机延迟优化已达成。
- 隔离合成证书 DinD：真实 mTLS、部署和不可变镜像恢复通过；不能替代仍不可达的原业务 Docker 端点或受限现有证书验收。
- 隔离 Portainer 2.45.1：Stack 发现、两服务镜像交换、原生 image ID、原始 Compose 恢复和严格健康验证通过；不等于生产 Portainer 浏览器完整链路通过。
- Kubernetes：实机只读命名空间/目标发现及实际镜像查询通过；未擅自执行原业务部署恢复。
- 数据库副本备份恢复、凭证解密及审批保护通过；备份只清理快照孤儿记录，原数据不改写。
- 隔离 Vite/FastAPI 认证代理的登录、CSRF、续期、HttpOnly Cookie 和登出通过。

## 可复现的公开只读探针

从 `backend/` 运行：

```bash
.venv/bin/python scripts/probe_registry_manifest.py \
  --registry reg.aoodc.com --image fawney19/aether --mode auto
```

默认 10 个版本、单请求 10 秒、Blob 最多 5 秒、总观察预算 240 秒；可用 `--mode first_observed` 对照。脚本不读取 DB、存储凭证或运行时连接，不执行部署恢复；只输出阶段、方法、数量、状态和耗时，不输出 token、URL 或正文。失败或缺少所需 digest 时退出 1。

双语使用说明：`docs/guides/runtime-connections.md` / `.en.md` 的 `registry-probe` 节。

## 手动隔离实机 CI

新增 `.github/workflows/runtime-acceptance.yml`：仅 `workflow_dispatch`，不会向普通 PR/push 引入特权容器。`dind` 在 GitHub-hosted `ubuntu-24.04` 临时 runner 上用 rootful Podman、新合成证书、独立 DinD 和已实测镜像的固定 digest；checkout 不持久化 token，权限仅 `contents: read`。公开只读 Registry job 默认关闭，无密码/端点输入、无生产 secrets，保留原请求预算。原 `.github/workflows/ci.yml` 未改。

- 最新工作流契约 **7 项通过**，覆盖三种 job 的手动触发、临时 runner、固定 actions/image、rootful cache 与 test 一致、无宿主业务 Socket/全局 prune/生产端点接入。Portainer fixture 离线契约 **9 项通过**（启动/拉取/API/body失败、取消、清理失败保留原异常、HTTP关闭、精确删除、inventory 404严格失败）。与既有 Portainer 恢复/快照/客户端回归合计 **111 passed**，不是完整后端测试集。
- 临时目录安装的固定版 actionlint v1.7.7 验证工作流语法/表达式通过（未安装可选 shellcheck/pyflakes；不把它算作这两个工具的检查）。
- 最新代码隔离 DinD mTLS、CA/DNS 拒绝、真实部署、不可变恢复及清理用例：**1 passed，0 skipped，27.09 秒**。JUnit 已核对，`rt-owned-mtls-*` 无容器残留。
- **未提交或在 GitHub 触发此工作流**，不能声称远端 Ubuntu runner/network/pull 环境验证通过。只对可信、已审查的 ref 手动运行；特权 runner 并非用于运行不可信代码的安全沙箱。
- Portainer 2.45.1 现已固化：`backend/tests/real_portainer_fixture.py` 和 `test_real_portainer_acceptance.py`，由默认关闭的 `portainer` 手动 job 启用。Portainer 在独立 DinD 内使用其内部 Socket；外层无任何宿主 volume，Docker 只监听内部 unix Socket，仅映射临时 loopback HTTP 端口。镜像固定 digest、临时随机合成管理员/密钥、没有现有端点输入或在线库访问。
- 当前代码 Portainer 实机 **1 passed、0 skipped、35.81 秒**：发现、分组镜像查询、详情处理器显示不污染配置、原生 inventory 收敛后的 snapshot、两服务交换/native image ID、严格恢复及原 Compose 一致均通过。JUnit 已核对，`rt-owned-portainer-*` 无残留；整台独立引擎及其匿名卷删除，因此嵌套 Portainer/业务夹具/凭证一并清理，不全局 prune。
- 开发说明：`backend/tests/RUNTIME_ACCEPTANCE.md`，列出本地命令、420 秒测试/600 秒进程时限、HTTP/setup-token-bypass 仅隔离夹具用途与 hard-kill 清理边界。上述 fixture 不是生产 Portainer 浏览器到后端完整验收。现有业务端点没有自动加入工作流。

证据：`/tmp/rt-e2e-final-57.log`、`/tmp/rt-ci-dind-current.log`、`/tmp/rt-ci-dind-current.xml`、`/tmp/rt-portainer-ci-current.log`、`/tmp/rt-portainer-ci-current.xml`；相关检查不与此前 948 项相加冒充后端全量。

## 尚未完成

1. 获准后执行最新代码全量及受限凭证专项。
2. 仓库优化路径实机正向已通过；线上性能改善仍需同一环境基线对照，当前约 149 秒，不提高生产超时来掩盖失败。
3. 原业务 Docker 端点和生产 Portainer/Kubernetes 写入链路需权限与隔离目标；未计通过。
4. DinD、Portainer 和公开探针的手动 CI 工作流已固化并本地验证，但未提交或在 GitHub 触发；远端 CI 验收及提交/发布仍未完成。
5. 在真实测试环境实机联调中发现并修复了 `/history` 页面外链 tooltip 缺失 `common.openInNewTab` 翻译的问题，双语文案已补全。
6. 使用独立 NGINX 测试容器全流程验证了从版本拉取、审批阻断、变更执行、自动快照、原生就绪核验到快照回滚与级联清理的完整 14 步生命周期闭环；在此过程中发现并修复了 `DeployTasks.identity` 因包含瞬态 `container_id` 导致部署成功后就绪核验误报 `readiness_superseded` 的关键缺陷，已补齐单元回归测试。
7. 优化了任务队列待审批任务卡片的展示与交互：去除多余的二级弹窗，直接在卡片内完整内联展示全部审核要素——包括此前缺失的**拦截具体原因**（如目标初次纳管、大版本策略限制等友好文案）与**完整配置指纹**，以及运行时身份、恢复边界和有效期；原按钮调整为清晰的「刷新计划」，右侧直接保留「确认并继续部署」一键放行。
8. 修复了部署计划过期后陷入无限死循环（报错“该计划已失效，请刷新计划后重新确认”，点击刷新后再次确认仍被 409 拦截，无法推进部署）的底层缺陷：
   - **成因**：计划存在 30 分钟有效时限。过期后操作员点击刷新，后端路由此前仅静态查询数据库旧记录原样返回，从未重新计算与签发新 TTL；再次确认时后端依然判定时间过期而报 409 拒绝；若执行器此前已在前端删除，任务更会成为永久挂起的孤儿待办。
   - **修复**：
     1. `GET /api/tasks/{task_id}/deployment-plan` 接入自动重算机制：对处于待审批且计划已过期的任务，自动采集运行时实时证据并签发具备全新 30 分钟 TTL 的新计划；若执行器已被删除，自动将孤儿任务标记为 `superseded (executor_deleted)` 退出待审批。
     2. `POST /api/tasks/{task_id}/approve` 增加无漂移自动刷新：若操作员提交时计划刚好过期但目标容器与配置指纹未发生变化，后端自动重算签发并直接批准放行，不再反复弹窗阻断。
     3. 执行器删除（`delete_executor_config`）与系统启动（`reconcile_orphaned_executor_tasks`）增加待办任务级联失效：执行器删除后其未完成任务自动转为 `superseded`。
   - **测试**：在 `test_deployment_admission.py` 补充 5 项单元回归测试全部通过；在真实后端环境注入过期计划实机测试，GET 自动换发新计划（200）、POST 自动重签批准（202）全流程闭环顺畅。

## 快照配置完整性与恢复前校验展示

- 去除「分享前复核」及按 `unredacted_persisted` 展示的警告。该标记表示已采集的原始配置保留，不是配置缺失或自动脱敏失败。改用真实 `integrity_status` 展示存储校验（`verified` / `invalid` / `legacy_unverified` / 未取得结果），明确存储摘要校验不等于所有配置字段全覆盖，快照不包含应用数据。
- 恢复确认界面接入现有只读 `POST /executors/{id}/rollback/preview`，指定快照 ID，显示必要字段/目标/镜像验证失败的实际原因。校验中、请求失败、快照不匹配、非 verified 或验证不通过均禁用恢复；允许「重新校验」但不会提交变更。原版本字段标明「上次记录的版本（非实时配置）」。临时 preview adapter 在 finally 中释放，兼容无 close 的实现。
- 此轮仅校验展示，未新增全量运行配置 Diff（后续见下节），不宣称所有环境变量/挂载/端口逐项相等校验。恢复允许当前配置与历史快照不同，人工确认后覆盖快照支持范围内的配置；执行期仍按已有逻辑再次校验、在当前目标存在时保存恢复前快照、完成后核验原生状态。身份/必要证据/不可变镜像/完整性不满足则拒绝，不因文案修改弱化保护。
- 定向检查：前端两组件 **24 passed**、恢复服务 **18 passed**；前后端 lint/type/format/diff 检查通过。生产构建 Chromium 四种中英/桌面/窄屏 fixture **4 passed，0 retry**，验证具体 create_config 错误阻止恢复、重新校验可用、没有提交恢复写入请求。浏览器 API 使用隔离 mock，不当作真实业务实例恢复验收；没有访问或更改在线数据库/已有业务实例。
- 证据：`/tmp/rt-snapshot-config-ui-final.log`、`/tmp/rt-snapshot-config-browser-final.log`；用例 `frontend/e2e/snapshot-recovery-validation.spec.ts`。

## 恢复配置 changed-only Diff 与审核漂移防线

- `rollback/preview` 的 `include_diff=true` 返回实际实时配置到历史快照恢复配置的 `- / +` 差异；只投影各运行时实际恢复范围，忽略 inspect 状态/计数/时间，环境变量和具名容器归一化，Compose 按语义 YAML 而不是文本格式比较。未变字段/数组成员不显示。容器重建与网络、Stack 配置、Kubernetes workload_spec/仅镜像、Helm revision 分别标明范围；Helm 没采集全量 values，不把 revision 比较冒充全量 Diff。SSH Compose 仍是独立文件恢复流程，此轮未接入通用快照 Diff。
- 原始值先比较再脱敏，环境/命令/标签等保守隐藏，嵌套秘密/URL口令不返原值；隐藏值变化仍以两条 +/- 显示。审核 HMAC 绑定临时读取的实时目标身份与恢复配置、连接、执行器和具体快照；不返回可猜测低熵秘密的普通hash。超大/截断差异不给审核token，不允许假称看全后恢复。
- 页面就地展示变更；确认、任务队列执行和写入前重算审核指纹，不一致返回409或任务superseded，无恢复写入；409刷新差异并清空确认输入，要求重新审核。已有调用不传review_fingerprint时保留原API兼容，不声称所有外部API调用都强制审核。
- 本次真实原生验证发现JSON将Docker端口 `(host_ip, port)` 元组变成list，SDK误当成多绑定并额外发布随机端口。Docker SDK参数准备现在重建IP/port tuple，普通多端口列表不转换；已覆盖IPv4/IPv6、多绑定和JSON roundtrip。
- 后端适配器/API/恢复/差异相关 **234 passed**；前端两个组件 **26 passed**；生产浏览器中英/1280与390px fixture **4 passed，0 retry**。该浏览器fixture的API是mock，不当成原生验收。
- 额外原生页面验收：新建一次性DinD与NGINX、独立临时SQLite/合成密钥，浏览器实际点击恢复并转发到真实FastAPI执行器router；差异/恢复响应不mock。显示18081→18080和已隐藏的FEATURE变化，确认后原生inspect核对唯一18080端口、原环境、不可变镜像ID及NGINX running全部通过，pageerror=0。未连接已有业务容器/数据库，专用engine和临时DB清理确认通过。测试鉴权和无关侧栏API为隔离fixture，不算正常登录或完整调度队列实机验收。
- 日志与截图：`/tmp/rt-config-diff-backend-final.log`、`/tmp/rt-config-diff-browser.log`、`/tmp/rt-native-config-diff-final.log`、`/tmp/rt-native-recovery-config-diff.png`。

## 正常更新实时配置审核与同 tag artifact 修复

- 正常部署任务的准入/计划/批准/实际写入前都读取实时配置，不以执行历史当实际配置。`deployment_diff.py` 复用安全 changed-only 展示，HMAC 覆盖已采集重建配置、网络、HostConfig、Pod关系及原生目标/镜像身份；只有脱敏后的变更行和HMAC进入公开计划。Stack 声明之外再查原生 Config/HostConfig/Mounts/网络，防止仅比较Compose文本忽略原生外部编辑。
- 任务卡片直接内联 `- 当前 / + 将应用`，未变项不显示；手动更新需审核（即使目标已纳管），自动更新保留原版本/维护窗口策略，实际配置偏离纳管基线时需人工重审。Diff截断计划被阻断，不能批准。刷新显式 `refresh=true` 重新读取并重签；刷新失败不返回旧计划冒充成功。有效TTL内确认也重新检查当前证据，漂移409，旧指纹不放行。
- SDK pull后stop/PUT之前做原生状态复核；未写入的已知漂移返回superseded而非需核验阻塞。Kubernetes镜像patch使用最新UID/resourceVersion，防止检查后外部修改被覆盖。Docker/Podman/Portainer没有可锁住所有外部控制器的统一原生CAS，仍存在最后读取与写入之间的极短外部竞争窗口，不宣称本地指纹可锁住远端。
- 已知container digest的队列更新使用 `tag@digest`（或已有 `@digest`），冻结被审核的实际artifact，保留executor保存的tag/digest选择模式；不因tag字符串相同漏掉新artifact。读不到完整容器配置阻断，避免降级成只有image的“全配置”审核。旧schema的基线首次重新采集后可能要求重新审核，这是保守纳管，不是自动续用旧不完整批准。
- 范围：容器/Compose已采集重建配置，Kubernetes已采集workload spec，Stack更新diff为Compose声明配置（并绑定实际原生config证据），SSH为当前项目文件与渲染Compose；Helm只显示已有chart/revision范围，未渲染新Chart全部values/manifests，也不把新镜像自身默认字段当已知Diff。应用/卷数据不在配置Diff或恢复范围。
- 独立实机页面验收：新DinD内新registry+NGINX，同一 `nginx:stable` 发布新imageID；浏览器点击执行、查看+-、现场替换专用容器端口但保留同tag、旧批准409、刷新、新批准202、真实TaskQueue执行`succeeded`；native inspect核对新imageID、现场18081端口、原环境和NGINX running全通过，pageerror=0。任务/计划/更新响应不mock，鉴权与无关API仅隔离fixture；未连接已有业务实例/在线数据库，专用engine及临时DB清理通过。
- 相关回归：新增正常部署14项；后端队列/适配器/API等最终定向384项通过；另有Stack相关105项通过（套件有重叠，不累加当完整全量）。前端Task/run/rollback 41项通过，lint/types通过。生产浏览器新增中英/1280/390四项及任务队列相关10项均通过；首轮12通过+2旧fixture因错误plan状态失败，修正为真实pending契约后只重跑失败2项通过，无重试。
- 证据：`/tmp/rt-native-update-review-final.log`、`/tmp/rt-native-update-review.png`、`/tmp/rt-deploy-diff-backend-complete.log`、`/tmp/rt-deploy-diff-ui-complete.log`、`/tmp/rt-deploy-diff-browser.log`、`/tmp/rt-deploy-diff-browser-conflicts.log`。本轮未运行完整后端全量，也未部署或提交。

## 执行器编辑向导实时草稿配置 Diff

- 编辑/创建向导的最后「复核」步骤在原镜像预览位置显示实时 `- 当前 / + 将应用` 配置差异。请求携带当前尚未保存的目标、服务绑定与镜像策略，不从旧保存对象生成计划；返回上一步修改后丢弃旧结果并取消请求，迟到响应不能覆盖新预览。「刷新对比」再次读取，显示读取时间，失败/无可部署版本/截断分别说明，不当作无差异。
- 新管理员只读 `POST /api/executors/configuration-preview` 复用静态绑定验证和实际native diff；不保存执行器、不创建任务、不批准部署、不保存快照，原生临时adapter在finally释放；SSH预览跳过保存时的ownership.prepare，不做纳管写入。未保存源/渠道按实时发布投影解析，Diff隐藏敏感值与私有SDK异常。保存仍只改策略；真正执行由既有队列重新读取并审核，预览不作部署凭据。允许预览失败后保存以修复绑定/连接设置，界面明确未完成运行配置比对。
- 真实页面验收：新DinD内新NGINX/临时registry及临时SQLite；浏览器进入编辑最后步骤，tag preview、刷新、返回改未保存digest策略、重新计算并显示新+-、390px检查、保存全部通过。preview/配置读取/保存响应使用真实FastAPI router，不mock。原生检查镜像ID、18080端口、环境变量完全未改变，任务仍0，保存仅变image_reference_mode。合成鉴权与无关API使用隔离fixture，不当成正常生产登录验收；自有engine与临时DB清理确认通过。
- 定向后端5项新增及API/normal-deploy相关合计84项通过；前端新增4项及编辑相关24项通过，最终前端全量 **441 passed / 53 files**，lint/type通过。生产构建浏览器Kubernetes/Portainer中英+1280/390px编辑/刷新/保存防污染 **8 passed，0 retry**（API fixture与上述真实NGINX验收分开）。未运行完整后端全量、未提交部署或修改已有业务实例。
- 证据：`/tmp/rt-native-sheet-review-final.log`、`/tmp/rt-native-sheet-diff-desktop.png`、`/tmp/rt-native-sheet-diff-mobile.png`、`/tmp/rt-sheet-diff-backend-final.log`、`/tmp/rt-sheet-diff-frontend-full.log`、`/tmp/rt-sheet-diff-browser.log`。

临时 DinD、Portainer、合成证书和此前副本已清理；保留公开镜像缓存。完整历史过程在 `/tmp/rt-retest-summary.md`，临时日志并非长期归档。
