# 公司服务器部署准备

**当前状态与边界：** 2026-11-10已将项目完整迁入`pdd_dashboard`，运行版本为0be4c48（构建42716481711）；沿用受控manage.py发布入口完成兼容版与连接切换。API/Web健康、Worker已恢复；HTTPS及既有匿名访问策略、项目网络和资源限额保持。旧库32张领域表数据已按授权清空，仅保留空结构、版本记录和公司对象；管理员后续删库。迁移验收与实际切换影响见执行记录（2026-11-10本地验收记录）。

## 固定资源边界

| 项目           | 唯一允许值及限制                                                                                   |
| -------------- | -------------------------------------------------------------------------------------------------- |
| SSH端点        | songbing@121.40.167.37:10022；不再接受Variables或命令参数覆盖                                      |
| 服务器项目目录 | /home/songbing/pdd-operations-platform；需预先准备，不由发布脚本创建                               |
| 数据库         | pg.kktree.cn:5432/pdd_dashboard，账号songbing；预装扩展及monitor保留                               |
| 应用域名       | pdd-operations.kktree.cn，发布控制器固定；改变需代码审查                                           |
| Compose项目    | pdd-operations-production；仅操作本项目api、web、获准worker及只读database维护服务                  |
| Docker凭据     | 项目内.docker/config.json；不读写用户默认~/.docker配置                                             |
| Docker连接     | 本机unix:///var/run/docker.sock；不允许环境变量/context改换daemon                                  |
| 共享资源       | 仅接入既有traefik-network，使用websecure/myresolver；不修改共享Traefik、网络或其他容器，不全局清理 |

Docker自身仍负责系统镜像/容器存储；发布程序不直接写这些宿主目录，也不修改其他项目资源。这是发布工具的操作约束，不会收回songbing已有Docker管理权限，也不能阻止同一账号或管理员另行运行命令。

## 访问方式（2026-11-04用户决定）

用户已明确要求取消网站账号口令，在新登录功能建立前允许匿名访问。发布目标为HTTPS页面及业务API无需Basic认证；这意味着知道或发现地址的人可访问，并非只允许songbing。此决定只改变Web访问方式，不改变SSH、PostgreSQL及来源账户权限，也不开放额外端口。

新Web镜像移除Basic认证指令、口令复制入口及Compose secret依赖；发布控制器不再要求`.htpasswd`存在。服务器已有`.htpasswd`继续作为旧版本回退材料保留，存在时仍检查权限与链接，回退旧镜像可能恢复登录框。旧Basic Authorization头仍不转发给API，避免缓存口令进入后端；后续应用登录须重新明确认证和代理传递契约。不能用隐瞒网址替代访问控制。

实际上线提交、构建/部署结果与匿名访问验证以当天验证记录为准。main推送仍只触发CI，构建和部署保留独立手动入口。

## Web传输与缓存

Web镜像自身负责响应压缩与静态资源缓存，不依赖或修改共享Traefik。JS/CSS等文本及JSON协商gzip；客户端不支持时保持原始响应。只对`/assets/`中带八位内容哈希的JS/CSS构建资源使用一年immutable缓存；无哈希文件和入口HTML重验证，SPA路由回到入口后沿用入口规则。缺失资源返回404，避免把旧脚本请求误回HTML。API代理不增加缓存、不改路由、参数或业务结果，仍清除旧Basic Authorization。

配置依据[Nginx响应头规则](https://nginx.org/en/docs/http/ngx_http_headers_module.html)，以固定镜像真实HTTP验证压缩前后字节相等、缓存边界和代理行为。发布后另核HTTPS响应；验收与实测大小见[11/11记录](../validation/2026-11-11.md)。

## 实现与发布保护

- `deploy/manage.py`统一账号、目录、文件、凭据、上传、发布和回退边界。有效用户须为songbing；逐层lstat并核对realpath，项目目录必须属于当前账号、父目录不可被组/其他用户写入。私密文件、状态文件和待覆盖文件拒绝符号链接、硬链接、错误所有者及不安全权限。私密文件须600、.docker须700；不再通过chmod去“修正”未知目标。
- `release.sh`和`rollback.sh`为固定目录入口，使用隔离模式Python。Actions侧`dispatch.py`固定SSH端点、禁用本地SSH配置覆盖，发送白名单JSON；远端直接执行来自同一构建提交的控制器，在检查后写入4个批准文件，不使用scp覆盖未知路径。
- 文件写入使用目录描述符、O_NOFOLLOW、独占临时文件及原子替换；发布/上传/回退共用fcntl锁。目录及文件检查用于防误配置和链接越界；不是对抗拥有同一UID或root权限的恶意并发进程的安全沙箱。
- 每次Docker调用使用新建的最小环境，固定--config、--host、Compose项目名、project-directory和--env-file /dev/null。忽略外部COMPOSE_FILE、DOCKER_CONTEXT、RUNTIME_ENV_FILE、镜像及目录覆盖值。项目Docker配置只允许ghcr.io的auths，不允许credsStore、credHelpers、context或项目cli-plugins覆盖；不自动登录或生成凭据。
- 镜像仅接受本仓库api/web/worker的sha256 digest。先检查配置、运行中的worker和只读数据库状态；数据库不是already_initialized就停止，不执行--apply。启动只针对api/web。worker运行时须按单独流程先停止，避免新旧版本混用。
- 成功后将提交、三个镜像digest及对应Compose内容一起原子写入`.release.json`；上一份成功快照为`.release.previous.json`。每次更新前把当前成功快照保存到`.release.recovery.json`，成功提交新状态后清除；发布失败后回退优先恢复这份刚才正常的版本，无失败标记时才回退previous。正常回退自身失败也可再次调用恢复。回退从选定完整快照生成受检的`.compose.rollback.yaml`再启动，不拼接不同版本配置。失败不宣称自动恢复；容器可能已部分更新，需要按记录处理。首次发布没有上一版可回退。旧`.release.env`格式不自动迁移，本项目首次发布现已完成。脚本落盘权限600，人工回退使用`bash /home/songbing/pdd-operations-platform/rollback.sh`，不直接执行`./rollback.sh`。

## 构建与部署是两个独立入口

1. **仅构建发布镜像：`.github/workflows/build.yml`。** 只允许workflow_dispatch，先调用CI，再构建linux/amd64的API/Web/Worker并推送GHCR，上传`release-manifest` artifact，保存30天。清单含提交SHA及三个镜像digest。没有SSH、服务器访问或部署job。“仅构建”仍包含发布镜像到GHCR，并非纯本机编译；2026-11-04运行41764958544已成功。
2. **部署已构建版本：`.github/workflows/deploy.yml`。** 只允许workflow_dispatch，必须输入上述成功构建的run_id。整个job引用production环境。通过只读GitHub API确认构建属于本仓库、build.yml、workflow_dispatch且已成功结束；下载该运行的清单，checkout该构建提交，再确认清单SHA与checkout一致，才允许SSH。不会重新构建镜像，也不自动初始化数据库。
3. 不存在workflow_run串联、推送标签自动部署或构建成功自动部署。若artifact过期或不存在，部署停止；需重新构建并审阅新的清单，不靠猜tag恢复。

[手动工作流](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)须先存在于默认分支。[环境审批](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)受套餐和仓库可见性限制，不能假设私有仓库必有required reviewers，也不能把environment:production当成必然暂停审批。即使没有审批功能，两个入口仍须分别手动触发。

## GitHub配置与待上传文件核对

2026-11-04已通过gh 2.103.3只读核实：当前sunny-orb为仓库管理员，私有仓库默认main；Actions开启且allowed_actions=all，默认workflow权限read。最初Secrets、Variables、Environments均0，无本项目GHCR镜像包；远端仅旧ci.yml。随后获准配置production及两个SSH Secrets，最新状态见下表。身份保存在本机keyring。

| 配置                                                       | 后续需要                                                             | 当前实际状态                                                |
| ---------------------------------------------------------- | -------------------------------------------------------------------- | ----------------------------------------------------------- |
| build.yml、deploy.yml与更新的CI/发布代码                   | 审查、获准后进入GitHub                                               | 已进入main，e4d7406；构建及部署成功                         |
| production Environment                                     | 配置允许发布的分支/标签；套餐允许时配置审批                          | 已创建，仅允许main；GitHub返回422，当前套餐不支持必需审批人 |
| SSH_PRIVATE_KEY                                            | 可登录songbing的私钥，建议production环境Secret                       | 已配置：现有加密私钥                                        |
| SSH_KNOWN_HOSTS                                            | 已核实的[121.40.167.37]:10022条目                                    | 已配置，使用已核实主机条目                                  |
| SSH_KEY_PASSPHRASE                                         | 当前加密私钥需要；未加密专用密钥不需要                               | 已配置，2026-11-04只读列表核实                              |
| SSH_HOST/SSH_PORT/SSH_USER/DEPLOY_DIR/APP_DOMAIN Variables | 不再需要；目标已固定在代码中，设置这些值也不会改变目标               | 目前0个Variables                                            |
| GITHUB_TOKEN                                               | GitHub自动提供；build job显式packages:write，deploy job actions:read | 构建及部署job均已成功运行                                   |
| GHCR服务器拉取身份                                         | 仅可读所需镜像的classic PAT，read:packages                           | 已登录；只读验证Token有效且仅read:packages                  |

页面入口：[Secrets](https://github.com/sunny-orb/pdd-operations-platform/settings/secrets/actions)、[Environments](https://github.com/sunny-orb/pdd-operations-platform/settings/environments)、[Actions权限](https://github.com/sunny-orb/pdd-operations-platform/settings/actions)。[GHCR认证说明](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)区分Actions的GITHUB_TOKEN和服务器拉取身份；不为拉取额外授予删除包权限。

| 文件                                                        | 服务器项目目录内位置    | 说明                                                                |
| ----------------------------------------------------------- | ----------------------- | ------------------------------------------------------------------- |
| compose.production.yaml、release.sh、rollback.sh、manage.py | 根目录                  | 白名单4文件；Actions同版本上传                                      |
| .env.example → .env                                         | 根目录，建议600         | 仅供人工维护Compose，镜像占位须换为构建清单digest；正式发布忽略.env |
| .env.runtime.example → .env.runtime                         | 根目录，600             | 真实数据库URL等私密配置，raw格式，不加外层引号                      |
| .htpasswd                                                   | 根目录，600             | 单独站点访问口令，不使用数据库口令                                  |
| .docker/config.json                                         | .docker目录700、文件600 | 专用registry登录；只允许ghcr.io的auths                              |
| compose.sync.yaml、runtime/、lark-home/                     | 根目录下                | 可选；同步启用另行准备并授权，不由本次发布创建或修改                |

本机预览为`/Users/sunny/Documents/ChatGPT/部署/upload-preview-20261104`，含required/templates/optional及逐文件SHA-256。无私钥、真实.env、Token、来源登录状态或业务数据。`dispatch.py`在GitHub runner运行，不上传服务器。Dockerfile、Nginx配置、维护脚本和应用源码构建入镜像，不另传node_modules、.venv、dist、var或整个源码备份。

## 配置准备实际进度

2026-11-04：服务器父目录只读核查通过，项目目录原不存在，现已创建为songbing所有、750；4个发布文件、.env、.env.runtime均600；.docker为700，config.json为600且仅空auths。未覆盖既有项目文件。数据库URL已填入分配的pdd_operations/songbing连接，秘密值经stdin传输，无本地凭据文件或Git记录。4个发布文件SHA-256与本地一致，服务器manage.py check通过。未拉取镜像、运行容器或连接数据库。

GitHub production仅允许main；SSH_PRIVATE_KEY及SSH_KNOWN_HOSTS已设置，读取列表核实存在。GitHub明确以套餐原因拒绝required reviewers（422），未尝试修改套餐或绕过限制；不把main分支限制描述为人工审批。构建和部署依然独立、手动触发。

仍需用户完成本机configure-private-values.sh向导：SSH_KEY_PASSPHRASE存入production；网站账号/口令生成bcrypt cost12的.htpasswd存入项目目录；classic PAT的read:packages用于项目专用Docker登录。秘密值不进聊天、命令参数、本机文件或日志。向导仅做配置，不推送、触发Actions、初始化或部署。随后用户完成向导，.htpasswd及GHCR配置已只读复核通过；.env镜像仍为占位，不能直接发布。

## 首次操作顺序（网段确认后执行）

1. 本地审查与测试通过，获准后提交/推送配置；先让build.yml和deploy.yml可从默认分支访问，不触发部署。普通分支推送只走CI。
2. 获准后仅在项目目录准备上述文件、私密配置及目录权限。专用Docker登录显式使用`docker --config /home/songbing/pdd-operations-platform/.docker --host unix:///var/run/docker.sock login ghcr.io`，采用交互/标准输入提供Token，不放命令正文。不使用默认docker login位置。控制器会拒绝继承其他配置的凭据助手或context。
3. 配置GitHub环境与SSH Secrets。DNS须指向正确服务器，Traefik入口和证书resolver须按公司配置匹配；如需改共享设施交由管理员处理。本项目不能用Actions替代DNS或修改共享代理。
4. 单独批准运行“仅构建发布镜像”，取得run_id和清单。保存同次构建的发布文件，并在人工维护.env填入对应API/Web digest。构建不会接服务器。
5. 先运行`python3 -I -B manage.py check`检查固定目录边界。人工维护Docker也必须显式选择项目.docker、本机socket和固定Compose项目名；默认database服务只读检查。仅在另行明确批准后，用同一API镜像运行`python scripts/init-deployment-db.py --apply`首次初始化。禁止把release.sh当作初始化入口。
6. 初始化和配置验收后，单独运行“部署已构建版本”，输入对应build_run_id。维护未完成则拒绝启动。容器就绪后继续验收HTTPS、站点认证、SPA路由和业务接口；健康检查不代表业务数据已齐备。

## 数据库扩展适配与边界

历史revision不改写，本机多角色schema.sql保留；公司schema-songbing.sql由同一迁移链派生，固定songbing/pdd_dashboard/PG17，由songbing创建并拥有应用对象。空库维护CLI默认只读，显式--apply才允许首次初始化。初始化与版本标记在同一事务中，不创建角色或修改数据库和monitor权限；已有库走[增量入口](#deployment-upgrade-command)。

按[PG17 pg_depend](https://www.postgresql.org/docs/17/catalog-pg-depend.html)的e成员及i内部依赖识别预装扩展，不按schema名或函数前缀放行未知对象；n/a/x依赖不等于扩展成员。前置与基线关系检查均保留，模板变化停止生成。现行重复核验仅在RPA表符合保护契约时排除该表及附属对象，并用应用目录指纹校验结构和归属。

本机PG17专项26项通过，覆盖扩展、monitor保留、未知对象拒绝与失败回滚；公司实际库已获准只读核查通过empty，含pg_repack的repack目录，前后元数据一致，见[只读证据](../validation/2026-11-04.md#company-db-readonly-preflight-20261104)。未实际建表。正式初始化前应再次只读预检，避免状态已变。

<a id="database-upgrade-15"></a>

## 已有线上库同步至16的发布顺序（2026-11-11已授权，执行结果另记）

2026-11-11用户授权合并当前main与本聊天分支，通过CI/CD后上线。只读确认当前线上连接为songbing/pdd_dashboard、head为20261104_12、镜像提交0be4c48；main为ef44834且仍是12。当前候选代码目标为`20261111_16`，包括13保留维护、14内容复用、15每周对账及16售后历史汇总期间索引。16新增legacy return_records的部分期间索引（不含金额/数量INCLUDE），并仅将两保留guard的结构绑定适配16，生产写入硬关闭不变；[索引设计与成本](database-performance.md#return-summary-index-16)为权威。目标结构和字段以[现行字典](../database-schema/README.md)为准。生产实际head必须执行前只读确认，不能用聊天中的历史版本代替；不得跳过中间迁移。此处记录发布前置和顺序，不表示生产升级已验收。

1. 先完成专用设计与字段字典，追加Alembic迁移，运行`npm run db:schema`生成并审核`schema.sql`和`schema-songbing.sql`，完成结构一致性、真实PG升级/恢复及应用回归。本轮只在随机本机PG完成上述验证。
2. 只读确认连接目标严格为`pg.kktree.cn:5432/pdd_dashboard`、账号`songbing`，核对实际迁移head、应用对象、公司扩展与monitor、当前发布指针和任务。来源数仓不参与迁移；不修改共享服务器网络或全局配置。
3. 先适配并审核实际生产使用的备份/恢复工具清单，使其支持实际起点和目标head的全部表、视图、函数与触发器；仓库外旧工具不能仅凭本地测试视为已适配。完成加密全库及必要运行文件备份，并在新建隔离库演练恢复、核对内容。恢复目标不得覆盖现有生产数据。
4. 如起点为13或14且存在离线版本，先按原head及原回执恢复后再升级。15的升级保护会拒绝未恢复的离线状态；不能跳过守卫、改写旧回执或删除归档解决。若实际head超出已审核路径，先补专门兼容验证。
5. 在已授权维护窗口，仅暂停本项目Worker及其他写入口；协调API维护访问、取得本项目数据库锁。记录原运行状态，失败时保留备份与旧数据，不操作其他项目。
6. 使用与待发布镜像/提交绑定的[受控增量升级入口](#deployment-upgrade-command)在单事务中升级到`20261111_16`。空库初始化入口和`schema*.sql`仍只用于首次初始化。
7. 先核对新结构、所有权/权限、扩展/monitor保留及原事实/报告/指针未被改写，再发布相容API/Web/Worker。普通数据读取、固定历史报告、状态接口和周计划分别验收后恢复原任务。内容复用与清理写入口仍按原保护关闭，升级结构不等于授权开启或清理数据。
8. 若迁移失败，按实际事务结果核对并保留旧运行版本；若应用失败，只有经验证与当前schema相容的旧应用才可回退。已有新状态或归档证据时不自动降级删表；优先保留结构并用修复迁移处理。恢复生产备份属于另行批准的恢复操作，不能自动覆盖。

<a id="deployment-upgrade-command"></a>

### 12→16数据库维护入口

在现有发布锁和备份恢复门禁下，使用待发布提交构建的API镜像运行 `python scripts/upgrade-deployment-db.py`。入口读取现有私密 `DATABASE_URL`，固定 `postgresql+psycopg://songbing@pg.kktree.cn:5432/pdd_dashboard?sslmode=require` 的驱动、账号、主机、端口、库名和加密要求，连接后再次核对会话角色、库名和PG17。默认以只读事务检查目录、RPA结构、monitor归属及已登记公司扩展，报告起点或目标已就绪。当前发布无已审核的数据库级事件触发器，预检遇到此类回调即停止。外键内部触发器的约束身份与启用状态纳入目录校验。确认停写后追加 `--apply`。当前已达16时仍使用只读验证，13、14、15、未知版本和对象漂移会停止。

写事务依次获取项目同步、各发布域、归档维护及空库初始化互斥锁，拒绝仍有运行任务，再核对实际12目录。Alembic离线输出固定为12:16并校对审定摘要，剥离已审核的47条本机角色授权后以原连接单事务执行；保留注册和视图兼容均沿已审定迁移。提交前复核16目录、既有角色权限、公司扩展、monitor、RPA来源表/序列以及当前指针和报告关系。来源表由其自身采集任务维护，发布只核对其结构和权限。锁冲突、SQL错误或复核异常导致整次写入回滚。进程只输出状态、固定错误代码或异常类型。

16升级在项目锁内拒绝任何offline版本；旧15回执保持原样，在16拒用。显式16→15须无offline及任何已登记16回执，仅删除新索引并还原两guard到15，保留对账表、所有旧回执和事实。16普通CREATE INDEX仍受维护语句60秒和锁等待5秒限制；60秒不是整笔事务的墙钟上限。构建超时/取消时连同13—16版本更新整体回滚，不改API每条SQL5秒、不调全局配置、不用CONCURRENTLY绕事务。公司CLI不增加15起点；已15的固定恢复副本仅用已审定15:16增量另留证。随机本机PG验证直接12→16原子路径，原12→15及新增15→16证据不得冒称同一次直接升级；原失败和回执保留，新验收绑定新候选镜像。

新增对象的关系、字段、保留和回退边界沿[离线保留](../database-schema/database-retention.md)、[内容复用](../database-schema/content-reuse.md)及[每周对账](../database-schema/weekly-reconciliation.md)设计。增量入口只推进结构和运维元数据；原事实继续使用旧行表示，历史报告沿原输入边及比较报告读取。保留与内容复用生产写入门禁保持关闭。

周日17点和失败后300秒重试策略随相容Worker发布生效；首次登记及中断恢复语义见[周对账设计](../database-schema/weekly-reconciliation.md)。本轮实际发布、Worker配置和有限线上接口核验见[11/11记录](../validation/2026-11-11.md)；网站沿用户授权匿名访问，3项原有同步异常和长期容量仍待处理。

## 同步、证书与容量限制

Worker为独立sync profile，发布api/web不会自动启用。现已准备UID10001可写runtime/lark-home、专用飞书身份及数仓网页登录，同次构建worker digest来自.release.json。数仓口令独立保存于runtime/local-db/sources.env（600），不注入API环境；production同步显式使用SYNC_DATABASE_URL。登录仅通过新建专用窗口取得并只上传数仓域状态，本机中转文件已删除。

服务器compose.sync.yaml与仓库配置一致：3GiB内存、1CPU、512进程、256MiB共享内存、只读根目录和512MiB临时目录；只有runtime/lark-home两个可写绑定，只接本项目app网络。Docker日志上限10MiB×3，restart=unless-stopped。上述是实际限制，不代表长期容量验收；同步失败保留已发布版本，按来源在失败后间隔至少 5 分钟重试。每次执行仍受资源、锁和租约保护。

常驻进程每轮完成后等待60秒，沿config/sync.toml：数仓8源每日08:30/09:40/10:10/10:50/13:10/15:10/16:10，飞书2源08:20/10:10；聚水潭/天气等沿15分钟检查及10:00/10:30/11:00完整任务，全部串行。漏点只补当天最近槽，不伪造时间或跨日补跑。本机cn.kocotree.pdd-operations.sync在11月4日接管时停用，原plist在部署验收目录保存；11月10日用户为本地验证明确要求恢复本机独立同步，本地数据库与归档保留，恢复验证见本机调度（2026-11-10本地验收记录）。

后续升级前须等当前任务完成，再只停止本项目worker；现有发布控制器会拒绝worker运行时发布。api/web更新成功后，从新的.release.json取得worker digest重新启动，并验证真实任务、来源检查点及业务接口。回退调度同样先确认无任务；只恢复本项目触发器。2026-11-10用户批准本机与服务器分别为独立数据库执行计划拉数，作为本地验证例外；不得把本机任务指向生产库。两边锁不跨库协调，上游读取负载会增加，既有串行及有限重试保持，不据此认定共享并发容量已验收。

数据库模板sslmode=require只加密、不校验服务器身份；公司CA和证书域名确认后再适配verify-full。AMD64 Actions构建、服务器API/Web镜像拉取已验证。HTTPS入口当前返回自签名证书，尚未通过可信证书校验；公司首次初始化和实际站点访问仍未执行。本机Linux测试以隔离容器验证账号/文件/环境边界，Docker调用使用替身，不冒充生产端到端验证。

同步镜像本机约2.34GB，正式部署前需复核服务器容量及镜像保留策略，不运行全局清理。基础镜像固定多架构digest，应用按构建digest发布；API/Worker暂沿requirements-dev.lock保证版本一致，仍含测试工具。仓库COMMON-ISS-06历史文档计数失败单列，不因本次保护测试通过而关闭。

配置向导最终复核：production共3个SSH Secrets，部署分支仍仅main；网站账号songbing，bcrypt cost12，.htpasswd为600。项目.docker目录700、config.json为600；服务器内仅向GitHub官方API作只读认证，确认Token属于sunny-orb且权限只有read:packages，无额外权限。4个发布文件摘要仍与本地一致，运行配置600，边界检查通过。未读取GitHub Secret值，SSH解锁口令匹配及真实站点登录仍待后续实际运行验证；.env镜像仍占位，无.release.json，未部署。此前口令长度校验中止及补填过程保留在验证记录。

## 网段确认前已完成的准备与恢复执行顺序

2026-11-04：构建41764958544对应提交8f4fb718064e2dbc39b0f20ac3ea107037b2e072，云端前端340、后端772项测试通过。发布清单有效至2026-12-04T03:20:28Z。服务器API/Web均为linux/amd64，RepoDigests匹配清单；未拉取可选Worker镜像，未启动同步。复核时剩余空间约14GiB，项目容器和网络均为空。运行配置中两个数据库URL均固定songbing/pdd_operations，sslmode=require，连接池3、溢出0、连接超时5秒，CORS为正式HTTPS域名。

DNS A为121.40.167.37，本次AAAA查询无结果；HTTP返回308跳转HTTPS。服务器本机443入口按正式域名SNI执行可信TLS校验，返回self-signed certificate，故HTTPS尚未验收。暂不更改共享代理或证书配置；应用路由发布后检查证书是否成功签发，仍失败则记录原因并交由共享代理管理员处理。不能关闭证书校验作为验收通过。

阻塞原因：创建pdd-operations-production_app时Docker默认地址池耗尽。只读检查36个网络及路由，候选10.254.240.0/28无当前可见重叠，内存中Compose语法验证通过；公司VPC/VPN/预留网段尚待管理员确认。未修改实际Compose文件或创建候选网络。空网络也可能属于别人，不删除、不复用。

确认后按以下顺序恢复：

1. 记录获准网段，重新只读检查是否与最新Docker分配、宿主路由冲突；只给本项目app网络配置IPAM，保持服务及外部traefik网络定义不变。先做Compose语法、边界回归和差异核对。
2. 提交并推送获准的项目配置，重新运行独立构建，取得新run_id。**不能直接使用旧构建41764958544部署新网段**：deploy工作流会checkout构建提交并上传其中的Compose，旧构建会把新配置覆盖回无IPAM版本。保留同提交发布代码与镜像清单的完整关系。
3. 对比将上传的4个文件及服务器权限，维护步骤使用新构建的Compose和API digest。在固定项目目录、项目凭据、项目名及发布锁下先运行数据库只读预检。状态不是empty或already_initialized时停止；仍为空才执行已审查的首次初始化，所有DDL在同一事务中，随后按目标head检查版本、应用表/字段数、所有者，以及预装扩展和monitor保留。现行本地13交付为37张应用表/559列；不得把这份初始化SQL重放到现有生产库。
4. 手动运行deploy.yml，输入新构建run_id；只启动api/web，等待就绪，核对.release.json提交及digest。不启动worker、不导入或同步业务数据。
5. 验收外网可信TLS、HTTP跳转、首页和/api未认证401、错误口令401、正确口令访问首页与API、SPA子路径及容器健康。用户登录口令不进入聊天、命令参数或日志。私密口令缺失时由用户在本机隐藏输入或手动验收，不重设口令。
6. 核对空库展示不冒充真实经营数据，数据库连接池与运行日志正常。数据同步需要专用来源身份、数据范围、备份恢复及频率验收，按单独任务准备；本次只上线站点不等于业务数据已齐备。

首次发布没有上一份成功快照，不能调用rollback假定有历史版本。失败先保留证据，只处理本项目api/web；不删除数据库、不执行全局prune、不移除共享网络。后续已存在成功快照时才按受保护rollback入口恢复。

仍无法提前完成的验证：GitHub runner真实SSH解锁和连通、候选网络创建、镜像内数据库连通/初始化、实际可信证书、正确口令端到端登录与业务接口响应。已有本机及云端单元/集成验证不能替代这些运行验收。

## 首次部署实际结果（2026-11-04）

构建41775111241成功，随后安装同版本Compose并创建本项目10.254.240.0/28网络，维护入口返回initialized、复核already_initialized。32张业务表、524列及所有权检查通过。初始化前后受保护对象摘要一致（16个扩展及monitor等目录对象），未改公司预装对象。

部署41776451188成功；.release.json指向e4d7406b65ff09797d589dd8d2c2402686455b07，API/Web digest均与清单一致，两容器running/healthy，无Worker。HTTPS最初遇到ACME 500 Unable to validate JWS，后续只读复查证书已签发成功，未修改共享代理或ACME账户。证书为Let's Encrypt YR2，域名匹配，2027-02-02 04:06:03 UTC到期；服务器及本机可信校验均通过，首页/API未认证401，healthz为200，API内部ready且数据库connected。

入口https://pdd-operations.kktree.cn/，账号songbing，使用用户私密向导中设置的网站口令。用户已确认正确口令登录；业务库首轮20项数据集已发布，Worker首轮16来源调度验收通过；system/status含固定bootstrap/planned字段，不用于判定实际模块实现程度。

管理员后续表示网段可能不合适，用户明确要求保持当前进度并完成现有部署核验。用户随后已最终确认管理员批准当前网段；如需换网段，只在批准的维护窗口更新项目配置并重建本项目网络/容器，保留数据库与凭据，不执行全局清理。

## 项目备份与隔离恢复

恢复后应对本项目隔离副本的明确表清单执行ANALYZE，再做接口对照；PG17逻辑备份不替代查询统计的重建。维护只限隔离库，保持业务行、约束、版本引用和报告不变；不因此修改生产库、共享PG配置或放宽应用超时。受限副本的耗时不直接当作生产SLO，失败必须保留协议码、SQLSTATE与耗时等安全证据。

备份边界是当前`pdd_dashboard`应用对象（2026-11-10前历史备份来自`pdd_operations`）与`/home/songbing/pdd-operations-platform`内的运行材料。所有版本—事实—输入—报告关系整体保存，不能只备份当前指针。公司扩展与`monitor`只核对元数据，不备份其数据或在恢复中覆盖。PostgreSQL 17原生custom格式限定`public`并排除扩展，按实际head核对完整表、视图、函数清单。12基线含33表（含Alembic），13为38、14为46、15为47表；各head不可混用清单。用户确认新增`aftersales_reports_rpa`为本项目来源，单独登记并完整备份，因此本轮12起点共34表；保留其`dbrole_admin`所有者、身份序列和权限，不把它纳入应用迁移或业务指标。

本轮发布使用服务器后台执行的`backup-create-server-20261111.py`（配套controller/snapshot脚本）与`backup-restore-server-20261111.py`，固定本次12起点和已确认RPA对象。备份快照、逐表摘要、pg_dump和运行材料归档均在本项目服务器内完成，本机只负责下载并流式加密；本机连接中断不会终止服务器协调器，协调器成功、失败或到达超时上限均恢复原Worker。旧的本机持有长事务工具及仓库外工具不得不经适配直接用于15备份。摘要算法为`sha256-pk-ordered-row-sha256-v1`，由`scripts/backup-table-digests.py`按主键分批查询并在客户端汇总行SHA-256，避免对全表`string_agg`排序触发公司临时空间限制。低内存隔离恢复可使用等价的顺序读取：先物化仅主键与32字节行SHA，再按同一主键排序，由服务器游标分批汇总；执行前检查查询计划不排序完整业务行。2GiB临时空间上限与16MiB工作内存仅设置在隔离恢复连接内，不修改公司库或全局参数。两种遍历方法须通过真实PG等价测试，最终行数与摘要仍逐表严格一致。备份与恢复校验必须使用相同PG主版本、UTF8编码、排序规则与UTC时区；本轮实际locale为builtin/C.UTF-8。恢复成功与上线结果另记验收，不因工具已准备而标为完成。

执行步骤：

1. 核对账号、路径、链接、空间、镜像digest及公司受保护对象摘要。仅在本项目同步互斥锁可取得且无running任务时，暂停本项目Worker；API/Web保持运行。
2. 建立只读REPEATABLE READ事务并导出snapshot，`pg_dump --snapshot`与逐表行数、排序行内容SHA-256使用同一快照。不修改生产数据，不使用`pg_dumpall`。
3. Worker暂停期间将数据库、`runtime`、`lark-home`、运行配置、认证配置、发布清单和控制脚本一起归档。记录每个文件的SHA-256、大小及原权限；排除临时数据库口令文件和备份目录自身。服务器归档成功或本批失败时恢复原Worker；原始归档与阶段文件临时保存在项目内0700批次目录，文件600，验证完成后清理。本机下载时校验服务器归档SHA-256和字节数并流式age加密，传输失败只重试同一批次下载，不重新暂停Worker或重做快照。包含秘密的运行配置不提交Git。
4. 密文在服务器项目`backups/<批次>/`和本机仓库`var/production-backups/<批次>/`各留一份，对照SHA-256。age恢复密钥只保存在本机`var/production-backups/recovery-identity.txt`，权限600，不发送服务器。需由负责人另存密码管理器或受控离线介质；只有密文而丢失密钥无法恢复。
5. 在全新的`restore-drills/<批次>/`演练恢复。可从加密包解密提取；本轮下载较慢时，先核对服务器原始归档SHA-256／字节数，从同一归档并行提取和恢复，但只签发pending回执。提取时拒绝路径穿越、链接、重复文件及特殊文件，逐个核对清单。不得提取到生产`runtime`或现有目录。
6. 在新建PostgreSQL容器、新数据目录及空库`pdd_restore`中恢复；`--network none`、无端口发布、无共享卷、无生产连接配置，限制CPU/内存。临时库日志保留2份，兼容服务器已有压缩设置，不改全局日志配置。恢复前检查目标为空；从归档清单中仅移除默认`public`的CREATE/COMMENT两项，保留新库已有的空schema，所有应用结构与数据仍恢复。`pg_restore --no-owner --no-acl --exit-on-error --single-transaction --use-list`；不使用生产库连接或`--clean`。
7. 对照该head全部应用表、Alembic表及已登记外部来源表的行数和内容摘要，检查约束与索引，使用同一API镜像只读连接演练库，核对版本、销售、退款、目标及归档引用。API只共享演练容器的断网命名空间，不挂载飞书身份或来源口令，不启用同步。
8. 下载完成后，本机重新校验密文并用恢复密钥实际解密流，SHA-256与字节数须和用于演练的原始归档完全相同；服务器密文副本也须一致。只有完整加密链、文件清单、全表内容及隔离实例都通过，才写最终恢复回执，不能用pending绕过发布检查。保存核验报告并完成上线验收后，只删除本批次新建演练容器与明文临时文件。复核原API/Web、Worker、项目网络及公司保护对象，保留密文和报告。禁止全局prune、清理其他网络/容器/目录、覆盖生产库或自动清除历史备份。

这是一次恢复能力验证，不等于生产数据回退授权。真实故障时，先核对备份SHA-256、解密密钥、镜像digest、数据时间与恢复容量，重复隔离恢复和业务对账；确认差异及维护窗口后，再单独批准连接切换或原库恢复。公司预装对象和全局权限不从应用备份重建。

周期、保留天数、自动告警及密钥托管尚未配置；未批准保留规则前不自动删除。单次备份只能恢复至该快照，不能承诺连续时间点恢复，也不能替代长期容量与业务验收。

## 本次合并发布实施顺序（2026-11-11）

### Task 1: 合并功能并验证兼容

在隔离发布工作区合并ef44834与620510b。保留main最新更新弹窗、历史覆盖/时效、筛选与请求恢复，同时保留周日17点对账、失败结束后300秒重试、普通16:10只检查、独立对账状态及安全1064诊断。保留既有保留/内容复用代码及历史读取，生产内容复用和清理写入继续关闭。解决冲突并跑针对性测试；不丢弃任一侧功能。

### Task 2: 受控增量升级入口

先补实施说明，再新增独立默认只读、显式apply的12→15 Alembic入口。严格限定songbing/pdd_dashboard（本机测试仅随机隔离库），校验实际起点、固定目标、PG17、应用对象、公司扩展/monitor、已确认外部RPA来源表、同步与发布锁。用单事务升级，失败回滚；保护既有事实/历史报告/发布指针。旧head及已有状态异常拒绝，重复目标可只读验证，不能重放schema.sql或调用初始化apply。真实PG覆盖保留、失败回滚、错误身份/版本、重复执行。同步维护备份/恢复清单，保留设计与派生SQL一致。采用Alembic离线12:15生成的增量SQL，在受控连接内单事务执行；精确移除本地pdd_app/pdd_ingest授权语句且拒绝其他角色引用，不新增或修改公司角色，不开放通用迁移连接。

### Task 3: CI/CD与上线验收

完整check、前端/真实PG测试、build及审查通过后推送合并main、构建API/Web/Worker不可变镜像。仅项目范围取得全量加密备份并在独立容器恢复核验，保留历史报告与运行材料。确认Worker空闲后仅暂停本项目，执行已验证增量迁移，再经受控Actions发布并恢复Worker。检查实际镜像、HTTPS、页面资源、业务接口/前端契约、历史报告、调度状态与加载耗时；不改共享网络/全局配置、不删除业务数据。浏览器访问被Work设置禁用，不能把HTTP验收冒充手工交互验收。
