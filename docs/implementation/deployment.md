# 公司服务器部署准备

**当前状态与边界：** 提交e4d7406已通过构建41775111241和部署41776451188上线。数据库已初始化，API/Web健康，可信HTTPS、未认证访问拦截及用户登录通过。用户已确认管理员批准10.254.240.0/28。首轮接数已发布20项当前数据集，服务器Worker已启动且首轮16来源调度验收通过；不修改共享网络或全局配置。验证与剩余验收见[生产接数记录](../validation/2026-11-04.md#production-first-sync-20261104)。

## 固定资源边界

| 项目           | 唯一允许值及限制                                                                                   |
| -------------- | -------------------------------------------------------------------------------------------------- |
| SSH端点        | songbing@121.40.167.37:10022；不再接受Variables或命令参数覆盖                                      |
| 服务器项目目录 | /home/songbing/pdd-operations-platform；需预先准备，不由发布脚本创建                               |
| 数据库         | pg.kktree.cn:5432/pdd_operations，账号songbing；预装扩展及monitor保留                              |
| 应用域名       | pdd-operations.kktree.cn，发布控制器固定；改变需代码审查                                           |
| Compose项目    | pdd-operations-production；仅操作本项目api、web、获准worker及只读database维护服务                  |
| Docker凭据     | 项目内.docker/config.json；不读写用户默认~/.docker配置                                             |
| Docker连接     | 本机unix:///var/run/docker.sock；不允许环境变量/context改换daemon                                  |
| 共享资源       | 仅接入既有traefik-network，使用websecure/myresolver；不修改共享Traefik、网络或其他容器，不全局清理 |

Docker自身仍负责系统镜像/容器存储；发布程序不直接写这些宿主目录，也不修改其他项目资源。这是发布工具的操作约束，不会收回songbing已有Docker管理权限，也不能阻止同一账号或管理员另行运行命令。

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

历史revision不改写，本机多角色schema.sql保留；公司schema-songbing.sql由同一迁移链派生，由songbing创建并拥有应用对象，不创建角色、不改数据库/monitor权限。维护CLI默认只读，显式--apply才允许首次初始化，目标固定pdd_operations/songbing/PG17；全部DDL与版本标记在同一事务中。已有旧版本拒绝重放，后续升级另出增量方案。

按[PG17 pg_depend](https://www.postgresql.org/docs/17/catalog-pg-depend.html)的e成员及i内部依赖识别预装扩展，不按schema名或函数前缀放行未知对象；n/a/x依赖不等于扩展成员。前置与基线关系检查均保留，模板变化停止生成。重复核验排除扩展表，仍检查应用表列数量和所有权；数量检查不代替完整结构审计。

本机PG17专项26项通过，覆盖扩展、monitor保留、未知对象拒绝与失败回滚；公司实际库已获准只读核查通过empty，含pg_repack的repack目录，前后元数据一致，见[只读证据](../validation/2026-11-04.md#company-db-readonly-preflight-20261104)。未实际建表。正式初始化前应再次只读预检，避免状态已变。

## 同步、证书与容量限制

Worker为独立sync profile，发布api/web不会自动启用。现已准备UID10001可写runtime/lark-home、专用飞书身份及数仓网页登录，同次构建worker digest来自.release.json。数仓口令独立保存于runtime/local-db/sources.env（600），不注入API环境；production同步显式使用SYNC_DATABASE_URL。登录仅通过新建专用窗口取得并只上传数仓域状态，本机中转文件已删除。

服务器compose.sync.yaml与仓库配置一致：3GiB内存、1CPU、512进程、256MiB共享内存、只读根目录和512MiB临时目录；只有runtime/lark-home两个可写绑定，只接本项目app网络。Docker日志上限10MiB×3，restart=unless-stopped。上述是实际限制，不代表长期容量验收；OOM或同步失败保留旧版并检查任务租约，不无限重试。

常驻进程每轮完成后等待60秒，沿config/sync.toml：数仓8源每日08:20/09:10/10:10/10:50/13:10/15:10/16:10，飞书2源08:20/10:10；聚水潭/天气等沿15分钟检查及10:00/10:30/11:00完整任务，全部串行。漏点只补当天最近槽，不伪造时间或跨日补跑。本机cn.kocotree.pdd-operations.sync已停用并移除触发文件，原plist在部署验收目录保存；本地数据库与归档保留。

后续升级前须等当前任务完成，再只停止本项目worker；现有发布控制器会拒绝worker运行时发布。api/web更新成功后，从新的.release.json取得worker digest重新启动，并验证真实任务、来源检查点及业务接口。回退调度同样先确认无任务；只恢复本项目触发器，不允许本机和服务器同时承担计划拉数。

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
3. 对比将上传的4个文件及服务器权限，维护步骤使用新构建的Compose和API digest。在固定项目目录、项目凭据、项目名及发布锁下先运行数据库只读预检。状态不是empty或already_initialized时停止；仍为空才执行已审查的首次初始化，所有DDL在同一事务中，随后检查版本、32张业务表、524列、所有者，以及预装扩展和monitor保留。
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
