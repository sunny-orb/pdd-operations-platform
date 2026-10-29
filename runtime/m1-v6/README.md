# 评分参照与离线工具

这里保留来源算法、试行标准及离线复核材料；现行页面评分由 `apps/api/app/modules/product_scoring/` 实现，运行入口为 `app.jobs.score_product_board`。

| 文件                                          | 用途                                     |
| --------------------------------------------- | ---------------------------------------- |
| `viral_tiers.py`                              | 原 V6 参照算法，保留取证依据             |
| `verify_offline.py`                           | 原算法的离线复核                         |
| `trial_baseline.py`、`test_trial_baseline.py` | 新试行分布参照及离线测试                 |
| `run_local_candidate.py`                      | 获准本地输入的候选试算，不是项目定时任务 |

私有 `inputs/`、`outputs/`、`data/` 不提交且不在整理时删除。原参数、新试行规则与正式放量边界见[模型说明](../../docs/implementation/m1-model-reuse.md)，不得以候选得分替代已验证模型。
