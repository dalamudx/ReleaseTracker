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

## 移动端数据展示与跨页面适配

- 前一批运行时/配置审核/编辑/行点击历史改动已先提交 `5e71ba5f`。本轮仅前端移动布局，不改统计/发布投影业务数据，不部署/访问已有业务实例。
- 浏览器复现仪表盘叠排区仍用桌面剩余高度：图表/Recent卡片只有约50–62px而内容溢出，卡片宽498px超过390px视口。移动/平板改自然高度单列，图表300/340px明确高度，桌面xl保留双列；Recent按卡片实际宽度使用容器断点，完整名称/version换行、数据全部可滚动、说明/来源44px点击区域。多渠道图例可换行；移动KPI去掉重复外层padding，首屏空间更合理。
- Trackers在小于xl宽度只显示全高列表，点击进入详情、有「返回追踪器列表」，搜索/分页/删除当前项返回列表；桌面可拖动双面板保留。fixed table列/完整名称换行/多源badge换行/版本限宽，检查和菜单44px触摸区，不把操作挤出320px视口。
- 跨页审查发现History长版本及通知/仓库Webhook长字段将操作挤出屏幕，已改移动固定操作列及换行，将隐藏的URL/状态/事件保留在主单元格。Webhook和Settings的Tabs曾受共享h-9约束，wrapped行挤在内容上；两列自适应真实高度，全部标签留在tablist边界内。Executors/Tasks/RuntimeConnections等列表没有同类数据高度消失，保留现有布局；凭证页仅用合成fixture做浏览器读展示检查，不读取受限数据/截图。
- 验收：真实生产构建Chromium页面+合成非空read-only API fixture，覆盖320/390/768/844横屏/1280桌面、图表28统计值/卡片边界/全部6发布、15tracker最后项可滚动选择返回、中英7页共28路由viewport审查+仓库Webhook动作。新9个布局场景与相邻12项浏览器回归合计21 passed、0 retry；另移动Release Notes打开/关闭1 passed。前端全量442 passed / 53 files，lint/types/diff通过。API是隔离fixture，不声称真实手机Safari或线上接口验收；未修改在线数据库或创建业务部署。
- 日志：`/tmp/rt-mobile-regression-browser.log`、`/tmp/rt-mobile-notes-probe.log`、`/tmp/rt-mobile-frontend-final.log`。UI复现基线与修复截图在 `/tmp/rt-mobile-*`；长期回归用例为 `frontend/e2e/mobile-data-layout.spec.ts`。

## 备份生命周期管理与页面直接恢复评估

- 既有后端已有最近N/UTC每日/每周自动保留（默认7，成功且验证通过才淘汰），本轮补页面完整策略/归档数量/总占用及手动删除，而非错误声明从无保留能力。策略仍由环境变量管理，不引入另一套配置优先级。
- 新管理员 `DELETE /api/backups/{name}` 要求confirm_name一致；有限错误码，任意路径/缺失/目录/symlink拒绝，至少保留一份合法本地归档（数量保护不等同完整性验证）。管理沿用InstanceBackup锁；完整FileResponse下载lease保护归档，自动淘汰跳过在下载文件，下次成功备份再清理。unlink状态更新在protectedworker中，重复取消完成再释放锁；disk error不暴露私密路径。列表/latesttime/verification/prune统一合法归档集，未管理文件不误删或冒充新恢复点。协调范围是单应用实例，不假称multi-worker锁。
- 前端：删除确认名称/不可撤销性，失败保留弹窗可重试并主动刷新清单；下载中/最后份/创建校验busy禁用。统计数量/空间与实际daily/weekly保留策略，中英文桌面/320px生产浏览器删除取消→busy→重试→成功刷新→最后份禁用4项通过。
- 真实页面验收：全新临时迁移SQLite/合成密钥 + FastAPI backup真实router；浏览器在390px实际创建ZIP、下载并保存在私有临时目录、删除2个旧ZIP、最后份禁用，0 pageerror。非backup设置及鉴权为隔离fixture，不访问在线备份/数据库。下载ZIP经过真实完整性/解密校验及恢复到全新目录，验证源库新值不变、restore.review_required存在、sessions清空、tasks仍0，全部临时资源清理通过。没有切换或恢复现库，不当成在线恢复验收。
- 直接恢复评估：技术可行，推荐页面发起→预检→当前安全副本→全局维护排空→配对新目录切换/持久journal→受控重启→重登录复核。现有恢复校验/隔离旧意图/门禁/shutdown可复用；全局写入门禁、跨进程所有权、崩溃可恢复切换、外部重启协调与结果回执尚缺，不能直接os.replace在线db/keys或只暂停TaskQueue。本轮未实现网页restore接口、未降低既有offline防线。详细中英评估：`docs/operations/backup-and-upgrade{,.en}.md#managed-restore-assessment`。
- 最终：backend备份管理/原恢复/main定向46 passed；frontend全量446 passed / 53files，lint/types/diff/black/ruff通过；productionbrowser最终4 passed/0retry。旧排序测试只调整fixture suffix为真实8hex，不放宽下载allowlist。MkDocs模块未安装，完整文档构建未跑。日志`/tmp/rt-backup-management-backend-complete.log`、`/tmp/rt-backup-management-frontend-all.log`、`/tmp/rt-backup-management-browser-complete.log`、`/tmp/rt-backup-management-native.log`，真实页面`/tmp/rt-backup-management-native.png`。


## 页面受控在线恢复：实现与最终隔离验收

- 上述评估阶段已在本轮落地。备份页单个归档「在线恢复」→私密暂存/严格校验/版本时间指纹预览→完整文件名与数据丢失确认→202只读回执→维护排空→当前安全ZIP→关闭旧资源→journal配对切换→原进程资源重建→重新登录→复核新操作。普通request503保留Retry-After与安全header，status header capability最长1h，绝无审批/数据库读取权限。cap可sessionStorage延续且storage受限时内存降级。
- 新在线恢复默认POSIX开启，可`RELEASETRACKER_ONLINE_RESTORE=0`保留旧生命周期。持有数据目录级及DB局部flock，拒绝其他worker及同目录不同DB名，因为共享keys；本地Linux普通文件/具备锁和替换语义卷。在线source仅当前schema兼容可信本地归档，不支持上传/镜像自动降级。目录权属不代替外部CLI/人工写入排他，仍要求停机使用它们。
- 不只TaskQueue pause：共享scheduler追踪callback，维护时禁止新callback并drain全部已提交工作、三Outbox独立子worker、Webhook子worker和只读fetch；正在deploy/recover/原生SDKborrow/未核销needsattention/download拒绝。readonlyfetch可自然等最多30秒，不强取消；timeout保留现库，不开始切换。10min计划过期在hash确认过程中不会删除仍用stage；审批及shutdown重复取消仍等ownedworker。退出要等admitted requests才能释放目录owner。
- 安全ZIP独立保存，与raw原pair/checksum/journal在私密`.online-restore-<db filename>`；普通备份仍遵守配置retention，已审核sourcepin防淘汰。fsync journal先于第一替换；reload失败回原始pair，无法证明cleanup/rollback则failclosed。启动自动恢复中断原pair，entrypoint在dbmate前调用recover-online-restore。正常路径无kill、宿主业务socket或人工重启。
- 恢复不复活旧任务/approval/observation/desiredstate/session；本轮额外修复旧restore helper遗漏第三审核Outbox与未展开admissionevents及Webhook父请求的隔离，旧pending/sending→discarded、历史delivered/failed保留，旧refresh→ignored。复核前暂停全部自动任务及通知，仅管理员显式review启用新操作。CLIreview后需重启恢复已paused调度器。
- 真实页面：全新临时SQLite/schema、合成JWT/encryptionkeys、真实FastAPIruntime_lifespan/TaskQueue/3Outbox/backup/authbrowsercookie与CSRF；390px真实表单选择归档确认→202→实际DB/key重载→旧session失效→新登录→review→安全ZIP下载验证。恢复值=`archived`，安全ZIP值=`live`，JWT回archivedsecret，0新增deploymenttasks、0pageerrors。非业务dashboard剩余readAPI为fixture；不使用任何旧业务实例。全部临时server、目录和连接清理通过。实际日志`/tmp/rt-online-restore-native-final.log`，截图`/tmp/rt-online-restore-native-{confirm,result,reviewed}.png`。
- 回归：相关完整backend254 passed，之后retention设置补充再次通过online两文件27项（重叠，不累加冒充完整suite）；frontend54files453 passed；productionbrowser中英320/1280恢复4项+旧删除4项共8passed/0retry。一次资源负载下reload白屏场景的同桌面单项复验及最终8项全部过，不消除断言/提高原30s上限。格式/lint/types/shellsyntax/diff通过。文档中英已更新；完整MkDocs构建仍未安装工具未执行。
- 日志`/tmp/rt-online-restore-backend-complete.log`、`/tmp/rt-online-restore-retention-final.log`、`/tmp/rt-online-restore-frontend-complete.log`、`/tmp/rt-online-restore-browser-complete.log`；耐久用例`backend/tests/test_online_restore{,_files}.py`、`frontend/src/test/online-restore.test.tsx`、`frontend/e2e/online-restore.spec.ts`。未commit/deploy或恢复现有业务数据库，之前移动返回按钮改动保持不变。


## Socket容器配置保真修复及真实UnixSocket验收

- 审计使用合成inspect和真实Docker SDK转换，24项初始回归全部复现失败：HostConfig安全/资源/namespace字段被保存却未消费、复杂bind mode压缩、同source双目标被dict覆盖、匿名volume实际Name缺失、Podman空/混合Binds清空structuredMounts、Docker StopTimeout与明确空值丢失。原业务socket、DB、工作负载均未访问。
- 共享创建映射deepcopy，保留已有Env/argv空list、Labels空dict；观察到的安全/CPU/内存/swap/swappiness0/PIDs-1/DNS/设备/namespace等明确字段按native支持保留。所有卷必须保留实际Name；已有卷不存在就拒绝，不让API创建空替代卷。随机HostPort空/0依据实际NetworkSettings绑定冻结，不重新随机分配。mount选项完整保留，Podman按规范化destination幂等合并，tmpfs权限/选项不裁成size。未知mounttype/部分无法安全映射的选项明确拒绝。
- Docker调用禁用SDK默认proxy ENV注入；低层保留StopTimeout、MaskedPaths/ReadonlyPaths及AttachStdin/AttachStdout/AttachStderr/StdinOnce，不由SDK detach默认重算。实际Socket暴露了SDK `volumes=list` 对复杂mode的 `_host_volume_from_bind` 误解析：mode被当dest导致多余匿名卷；已校正native request的Config.Volumes为真实dest且保留Binds整串，加入专门回归，不通过隐藏差异放行。
- 同一native参数render用于单容器、Compose分组及恢复的prestop验证；全部member先验证，再删除任何member。镜像pull后仍沿用实时配置/HMAC检查。真实SDK写入后重新inspect语义比对，image/3个纳管标签例外，ReadOnly省略等价false；env重复键不合并，argv顺序保持。失真不报成功，无自动回滚；错误只固定顶层字段名，无私密值。自定义非native SDK facade保留其原接口契约。
- 保守限制：新目标镜像若新增默认Env/Labels/ExposedPorts/Volumes，或填入之前为空的启动/健康字段，会在stop前拒绝；不是已实现新image默认配置合并/审批策略。Podman不能准确表达的security/namespace/mount option不猜测映射；依赖本次被替换container-ID的Docker namespace不盲目沿用。应用/卷数据本身仍不包含在快照里。
- 真实UnixSocket通过：默认skip的 `backend/tests/test_real_container_fidelity.py` 显式启用RT_RUN_REAL_DIND_TESTS，在全新 `rt-owned-fidelity-*` 独立DinD私有tmp UnixSocket，私有cgroup-v2 namespace委派；无宿主业务socket/旧证书/生产DB。自有build的额外ENV镜像验证拒绝且旧container仍running（该唯一tag由测试pullfacade提供prebuilt artifact；inspect/拒绝真实）。正向公开NGINX真实pull、更新、JSON roundtrip快照恢复、不可变native image ID均通过；readonlyroot、双bind/source/mode、匿名卷Name及`retained-data`、解析后的实际端口、DNS/groups/cap/resource、stdio/StopTimeout均核对。全部ownedengine及其匿名卷删除检查通过，没有全局prune。
- 夹具失败排查记录：起初cgroup domain失效只影响新的fixture初始启动，改为其private namespace专有daemon/workload树委派；旧图像新增ENV被guard正确拒绝，正向fixture创建时明确配置其目标ENV键；SDK新collection导致local-tag pullfacade未生效，改为唯一tag的class方法并仍真实pull所有其他tag。native writeback发现ReadOnly omitted/false语义等价及SDK complexBind导致匿名卷，已修生产wire并给fixture正确initial rawdest，最终1 passed/0skip，73.49秒。临时仅synthetic mount诊断代码已移除，未消除任何readback保护。
- 最终当前代码相关后端 **407 passed**（包括44个新保真用例），不是完整backend suite。Black/ruff/diff通过。前端无功能改动未重复测试；Podman真实socket/Portainer真实页面更新仍未在本轮计通过。新增可复现说明 `backend/tests/RUNTIME_ACCEPTANCE.md`；日志 `/tmp/rt-container-fidelity-complete.log`、`/tmp/rt-container-fidelity-native-final.log`。未commit/deploy或操作任何已有业务工作负载。


## Socket配置保真实机复验：Docker页面＋原生Podman

- 当前Docker UnixSocket更新/JSON快照恢复显式实机重跑1 passed/0skip，76.48秒，日志`/tmp/rt-container-fidelity-retest.log`。全部在新自有DinD运行，匿名卷proof、双bind、端口、安全/资源/ENV/stdio回读通过；不是已有业务容器验收。
- 新增`backend/tests/test_real_podman_fidelity.py`：默认skip，显式`RT_RUN_REAL_PODMAN_TESTS=1`，专有tmp graphroot/runroot/vfs/cgroupfs/nativeUnixSocket服务。新建NGINX真实CLI配置，真实SDKpull/update，JSONroundtrip immutable恢复。末次1 passed/0skip，130.87秒；readOnly、Config.StopTimeout明确37、argv/entrypoint/labels/env、DNS/groups/caps/memory/swap/cpushares/restart/tmpfs、实际loopback绑定端口、原匿名卷名称/数据及双bind propagation均对比，不mock任何SDK。finally仅在私有store删除新容器卷，验证零残留并停止ownedservice。文档已补本地复现命令。
- 实机发现并修复Podman两边界：inspect `MemorySwappiness=-1`是未设置，但libpod OCI `uint64`会拒绝；只省略该哨兵，明确0及0..100保留，非法值在preflight拒绝。JSON `[IP,port]`之前作为两个绑定遍历，恢复会丢loopbackIP；现正确识别IPv4/IPv6/空IP单pair、嵌套pairs，普通数字多hostports不改。分别新增9＋3项边界回归。
- 新真实页面验收：专用DinD UnixSocket、独立Registry与新正常提供HTTP的NGINX、临时SQLite/keys，当前devfrontend通过私有FastAPI真实executors/tasks/rollback路由、真实调度队列，合成admin依赖和无关readAPI fixture，不当作生产登录验收。不读取在线DB。页面执行→查看真实+-→外部仅改测试容器端口→旧confirm409→refreshtrue→confirm202→deploysucceeded→原生checkpoint核对选定imageID、保留完整有效配置/卷proof/NGINX HTTP→点击条目历史/快照→真实恢复diff及名称确认→recover202/succeeded→原镜像与快照端口/配置/卷proof/NGINX HTTP核对。2个真实任务均成功，0pageerrors，ownedengine/privateDB/registry/volumes清理通过。
- 页面发现修复：MANAGED_MARKERS实际包含第4个`releasetracker.io/deployment-id`，原保真project只排除3身份字段，真实队列写后会误报labels（directadapter无法覆盖）。现在读取本次context精确核验4个系统值，并将本次合法标记加入expected副本，不忽略任意reservedprefix；错误deployment-id或任何新增businesslabel仍拒绝，审核证据不被原地改写。新增本轮queue-label regression。
- 页面初次失败为上述合法deployment-id falsepositive，修后update成功；后续恢复入口测试selector把姓名span当button，已按真实行文本点击修夹具，未为通过改变页面生产交互。最终加强原生checkpoint的日志`/tmp/rt-socket-page-retest-final.log`完整PASS；探针`/tmp/rt-socket-native-page-retest.{py,mjs}`；实际截图`/tmp/rt-socket-page-retest-{update,recovery}.png`。前端代码无修改。
- 最新相关后端回归**420 passed**（含本轮57个保真用例），不是完整suite；ruff/black/diff通过。当前更新保守image-defaults拒绝策略未放宽；应用数据仍不做快照。Portainer/Kubernetes的真实更新页面本轮未计通过。未commit/deploy，不使用已有业务容器或修改在线数据库。


## 备份三项配置迁移至全局配置

- 移除备份目录、最近份数与间隔的环境读取，统一持久化为`system.backup_directory`（空表示持久化数据目录下的 `/app/backend/data/backups`）、`system.backup_retention`（默认7，1–100）、`system.backup_interval_hours`（默认0，0表示关闭，放宽支持 1–8760 小时，覆盖每天24h、每周168h、每月720h至1年8760h）。输入占位符规范为 `/app/backend/data/backups（留空使用默认目录）`，杜绝非持久挂载路径的误导。默认值在GET全局设置明确返回；POST严格校验，DELETE恢复默认；admin权限不变。Daily/weekly额外保留环境配置按请求范围未移除。
- 系统设置全局页的“存储与历史保留”卡片内，将备份存储目录、备份保留份数及自动备份间隔这三项配置全面重构为标准 SettingItem 组件（采用与基础环境、版本抓取等配置完全一致的左右两列两级栅格布局、统一的圆角图标徽章及标准标题/描述排版），彻底去除原有的 fieldset 临时容器，并在中英文双语下对描述文案和输入占位进行了标准化规范；保存时仅提交修改过的脏键并按锁安全顺序持久化。备份/校验/下载/恢复预检占用时拒绝相关更改；切换不移动、不删除旧文件，可切回查看。间隔保存热更新共享scheduler job，0移除；按当前目录最新归档续算，不重置计时。保留数用于下一次已验证备份剪裁，不保存立即删除。
- 启动、重启及在线恢复重载读取DB配置，手动/自动/恢复前安全归档读同一实时策略。启动前pre-migration CLI用只读SQLite读取目录，旧库没有settings表使用默认，不修改旧schema。旧部署env值不会自动导入，应升级时在全局页面重新填写；两份运维文档及双语页面说明同步。生产backend/src、frontend/src、docs三个旧env正则搜索0匹配。
- 新增9个后端配置行为测试与1个前端保存/校验用例，覆盖旧env忽略、默认展示、目录/调度热生效、真实ZIP剪裁、旧目录保留、restart/readonlypre-migration、复位、非法/不可写/下载/restorepin阻止。最终相关backend **216passed**，前端全量 **454passed/54files**；lint/tsc/ruff/black/diff通过。不是后端fullsuite结果。
- 真实浏览器：全新临时SQLite/keys与FastAPI服务，真实global-settings/backups路由、SchedulerHost、ZIP创建/验证/剪裁；只有合成管理员与无关读API fixture，不计生产登录验收。中文390px、英文1280px均完成页面保存三项→reload仍一致→备份页按新目录显示→连续创建3ZIP仅保留2；原中文目录两份归档在切换英文目录后仍存在；job实际24小时。0pageerrors/无横向溢出。日志`/tmp/rt-backup-global-browser-confirm.log`，截图`/tmp/rt-backup-global-{zh-390,en-1280}.png`。全部临时服务/数据归档清理；未读取/改写线上业务DB。
- 首轮恢复测试在seed关闭fixture storage后又为了零间隔重开该storage，teardown超时；改为使用默认零而不重开closedpool，生产恢复与取消边界复验通过。lifecycle double更新load/reschedule await接口、移除旧不存在main.backup_options monkeypatch；不修改生产停机保护。浏览器探针修正toHaveValue字符串契约/中文按钮名称，以及只dirty设置导致英文不再发重复interval POST的错误等待条件；生产功能未为通过修改。未commit/deploy。


## 通知弹窗「清理最近任务」与任务队列隔离

- 移除TaskNotificationPopover对`api.clearFinishedTasks`的调用及任务缓存失效操作。按钮仅隐藏弹窗当前显示的最近5条中的已读结束任务（id+updated_at），不改任务队列/cleared_at/执行历史，也不清理列表外已读任务。运行/审批/needs_attention不隐藏；有新updated_at的同一任务重新出现。任务页自己的「清除已结束任务」仍保持原独立操作，不改后端API。
- 沿用既有已读key，并增加独立本地dismissed版本存储（每个最多1000条），支持刷新/重开/跨tab更新，存储被禁用时本tab内存降级；通知描述明确只当前浏览器，非多设备服务端清理。通知摘要计算不包含已隐藏结果，但活动任务始终保留。隐藏不会原地修改query结果，任务页缓存/列表完全不变。
- 定向前端27 passed（popover13+任务页14），lint/tsc/diff通过。生产构建浏览器6项passed/0retry：中390/en1280各检查清理已显示3条后7条任务全留在队列，未显示2条与运行/异常2条保留，刷新/reload隐藏持续、新结果重现，任务POST/DELETE请求为0；另原任务页清除确认和通知导航4项正常。不以fixtureUI测试宣称线上数据库操作验收，未访问/修改在线数据。
- 回归`frontend/src/test/task-notification-popover.test.tsx`、`frontend/e2e/task-notification-dismissal.spec.ts`；日志`/tmp/rt-notification-clear-regression.log`、`/tmp/rt-notification-clear-browser.log`、`/tmp/rt-notification-clear-final.log`。本轮未跑全量，不commit/deploy，保留原有未提交改动。

## 开发启动后端就绪等待

- `npm run dev` 先执行 `frontend/scripts/wait-for-backend.mjs`，真实公开 `/api/auth/oidc/providers` 返回200 JSON数组才启动Vite；reloader打印监听、TCP连通、401/503/重定向或HTML不计就绪。覆盖`make dev`、`make -j2 run-backend run-frontend`及`make run-frontend`现有入口。固定IPv4回源127.0.0.1:8000，与原Host/Origin保留策略一致，不新增依赖或后端health接口。
- 默认60秒整体截止，每次请求（包含body）最多1秒；可`DEV_BACKEND_WAIT_TIMEOUT_SECONDS`设为0以上、最多600秒。超时以非零退出阻止Vite启动并提示排查backend日志。日志不打印OIDC配置。纯UI开发用`npm run dev:ui`明确绕过；后端热重载断连仍可能发生，不屏蔽真实运行错误，也不会停止已启动的backend进程。
- Node隔离测试8passed：延迟监听拒绝后就绪、HTTP状态/无效响应、挂起body/连接整体timeout、外部取消、非法CLI值非零退出、真实Vite回源/Host及Make/npm登记。已有devwatcher1passed、lint/types/diff通过。公开API实际只读探测就绪通过，未重启服务或写业务数据。相关日志`/tmp/rt-dev-startup-tests.log`、`/tmp/rt-dev-startup-types.log`。未提交/部署。

临时 DinD、Portainer、合成证书和此前副本已清理；保留公开镜像缓存。完整历史过程在 `/tmp/rt-retest-summary.md`，临时日志并非长期归档。
