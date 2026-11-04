"""缓存谱系守卫的结构回归（四个共享 hs300.db 缓存的 workflow）。

背景（2026-10-29 查明）：
  `actions/cache` 的 `restore-keys` 取**最新**的匹配条目 —— 谁最后保存，谁就是下一个
  run 的起点。这条规则把「缓存」变成了**谱系根**：一个半修复/陈旧的库一旦被保存，
  缺口就会被固化并逐日传递。

  真实事故：deploy-site.yml 建档时用了固定 key `hs300-db`（2026-10-25），
  10-26 首次运行把该 key 写死（actions/cache 不允许覆盖已存在的 key）→ 之后每天
  从同一份旧库增量；10-26 17:49 改成 `hs300-db-<run_id>` + `restore-keys: hs300-db-`
  之后，`'hs300-db'.startswith('hs300-db-')` 为 False，旧条目**永久不可达** →
  CI 行情谱系断在 2026-10-13，而站点其实一直由**本地** `a-finder.sh site --push`
  发布到 10-29。两条谱系漂移，直到 daily-audit 首次运行才暴露。

这里守住的四条不变量：
  1. 没有任何 workflow 用固定 cache key（key 必须含 run_id）。
  2. 没有任何 workflow 用**裸** `hs300-db` 作为 restore 前缀之外的固定键；
     `restore-keys` 只认 `hs300-db-`（带尾横线）。
  3. 谱系根的保存必须**有条件**：不合格的库存到 `degraded-` 前缀，
     且该前缀**不能**以 `hs300-db-` 开头（否则 restore-keys 仍然命中，等于没隔离）。
  4. 部署路径必须有单调性守卫：产物最新日不得比线上更旧。

不使用 YAML 解析器（venv 里没有 PyYAML）：按缩进切 step 文本块做文本断言，
与 tests/test_daily_audit_workflow.py 的既有做法一致。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

# 四个共享 hs300.db 缓存的 workflow（2026-10-29 发现 weekly-evolve 也在其中，
# 且它的保存步骤原本**完全没有 if 守卫** —— 是个不受控的谱系根写入者）。
SHARING_WORKFLOWS = (
    "daily-audit.yml",
    "deploy-site.yml",
    "gap-heal.yml",
    "weekly-evolve.yml",
)


def _text(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _step_block(text: str, marker: str) -> str:
    """切出某个 `- name: X` 步骤的文本块（按缩进，遇到同级条目就停）。

    步骤名用**精确相等**匹配（不用 endswith）：名字里带 `「」` 等括号时
    endswith 会失配，导致「找不到步骤」却返回空串。
    """
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines)
                  if line.strip() == f"- name: {marker}"), None)
    if start is None:
        return ""
    indent = len(lines[start]) - len(lines[start].lstrip())
    out = [lines[start]]
    for line in lines[start + 1:]:
        if line.strip() and (len(line) - len(line.lstrip())) <= indent:
            break
        out.append(line)
    return "\n".join(out)



def _code_only(block: str) -> str:
    """去掉注释行与行尾注释，只留真正会被执行的 YAML/Python 内容。

    为什么必须有：这些测试是**文本断言**，而本仓库的注释极详尽 ——
    修复时写的「为什么」注释里往往正好含有被断言的字样（例如
    `audit_rc == '0'`、`restore-keys`、`degraded-`）。于是「把代码改回 bug 版本、
    只留注释」也能让测试通过 —— 测试变成自我安慰。实测已发生：把 daily-audit 的
    key 回退成 bug 版本、保留解释注释，旧断言仍然通过。
    """
    out = []
    for line in block.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # 去掉行尾注释（YAML 里 `key: x  # 说明` 的注释部分）
        idx = line.find(" #")
        if idx != -1:
            line = line[:idx]
        out.append(line)
    return "\n".join(out)


def _save_steps(text: str):
    """找出所有**保存谱系**的步骤块。

    2026-11-04 起谱系存储从 actions/cache 换成 GitHub Release 资产，
    保存动作统一是 `python3 scripts/db_lineage.py save ...`。
    判据用**这条命令**而不是步骤名：步骤名会改，命令不会。
    """
    blocks = []
    for line in text.splitlines():
        if not line.strip().startswith("- name:"):
            continue
        block = _step_block(text, line.strip()[len("- name:"):].strip())
        if not block:
            continue
        if "db_lineage.py save" in _code_only(block):
            blocks.append(block)
    return blocks


def _restore_steps(text: str):
    """找出所有**恢复谱系**的步骤块（`db_lineage.py restore`）。"""
    blocks = []
    for line in text.splitlines():
        if not line.strip().startswith("- name:"):
            continue
        block = _step_block(text, line.strip()[len("- name:"):].strip())
        if not block:
            continue
        if "db_lineage.py restore" in _code_only(block):
            blocks.append(block)
    return blocks


# ---------------------------------------------------------------- 不变量 1


def test_no_workflow_still_uses_actions_cache():
    """谱系已迁到 Release 资产：四个 workflow 不该再有 actions/cache 的**步骤**。

    2026-11-04 查明 actions/cache 在本仓库**双向都不工作**：Post Save / Post Restore
    一直 skipped，条目永久停在 10-27/10-29 两条 5.2 MB 残库，而缓存用量才 ~200 MB
    （不是配额问题）。后果是 gap-heal 拿到空库 → 体检 30 天全 0 只。
    旧方案的 key / restore-keys / degraded- 前缀语义全部作废，
    改用 scripts/db_lineage.py（单一名义资产 + 覆盖 + 单调性守卫）。
    """
    for name in SHARING_WORKFLOWS:
        code = _code_only(_text(name))
        assert "uses: actions/cache" not in code, (
            f"{name}: 谱系已改走 Release 资产，不该再使用 actions/cache 步骤"
        )
        assert _restore_steps(_text(name)), (
            f"{name}: 必须有 db_lineage.py restore 步骤"
        )
        assert _save_steps(_text(name)), (
            f"{name}: 必须有 db_lineage.py save 步骤"
        )


# ---------------------------------------------------------------- 不变量 2


def test_restore_treats_rc2_as_cold_start_not_failure():
    """restore 的 rc=2 表示「没有谱系」，必须当正常冷启动，不能判红。

    旧方案里「没有缓存」是 cache miss（步骤仍然 success），新方案是显式退出码，
    所以调用方必须区分：rc=2 = 首次/被删（正常），其余非 0 = 真出错（告警）。
    判错的后果是全新仓库第一次跑就红，而红的原因只是「还没有库」。
    """
    for name in SHARING_WORKFLOWS:
        blocks = _restore_steps(_text(name))
        assert blocks, f"{name}: 应当有 db_lineage.py restore 步骤"
        code = _code_only(blocks[0])
        assert "rc" in code, f"{name}: restore 步骤应当读取退出码区分冷启动"
        assert "= \"2\"" in code or "= '2'" in code, (
            f"{name}: restore 步骤必须显式处理 rc=2（没有谱系 = 正常冷启动）"
        )


# ---------------------------------------------------------------- 不变量 3


def test_unqualified_db_never_overwrites_lineage_root():
    """不合格的库**绝不能覆盖**谱系根 —— 这是隔离语义的新实现。

    旧方案把坏库存到 `degraded-` 前缀，让它不被 restore-keys 命中。
    新方案在写入前判定（scripts/db_lineage.py 的 decide_save）：不合格直接跳过，
    线上谱系根保持不动。效果一样，但不需要再维护前缀命名规则。

    因此这里守两件事：
      1. 每个 workflow 的保存步骤都显式传 --lineage-ok（不是无条件写）；
      2. 传的值来自上一步的真实判定，**不能硬编码 true**。
    """
    for name in SHARING_WORKFLOWS:
        blocks = _save_steps(_text(name))
        assert blocks, f"{name}: 应当有 db_lineage.py save 步骤"
        for block in blocks:
            code = _code_only(block)
            assert "--lineage-ok" in code, (
                f"{name}: 保存谱系必须显式传 --lineage-ok（否则坏库会覆盖谱系根）"
            )
            assert '--lineage-ok "true"' not in code, (
                f"{name}: --lineage-ok 不能硬编码 true，必须来自上一步的真实判定"
            )


def test_cache_save_is_conditional_in_every_workflow():
    """每个 workflow 保存缓存时都要有资格判定，不能无脑 `if: always()` 就写谱系根。

    `if: always()` 让一次失败的 run 也成为谱系根 → 缺口被固化并逐日传递。
    """
    for name in SHARING_WORKFLOWS:
        blocks = _save_steps(_text(name))
        assert blocks, f"{name}: 应当有 db_lineage.py save 步骤"
        for block in blocks:
            code = _code_only(block)
            # 资格必须以「真实判定结果」的形式传进去：要么引用上一步的 output，
            # 要么是本地先算出来的变量（如 daily-audit 的 OK）。
            assert "steps." in code or "OK=" in code or "lineage-ok" in code, (
                f"{name}: 保存谱系必须带资格判定（不能无条件成为谱系根）"
            )


def test_weekly_evolve_qualification_comes_from_verify_step():
    """weekly-evolve 原本保存缓存**完全没有守卫** —— 不受控的谱系根写入者。

    它只跑 `stock_cli.py evolve`，不跑体检/补抓，所以谱系资格必须来自它自带的
    「检查库是否仍是合格的谱系根」那一步（strictly-latest + 历史深度）。
    """
    text = _text("weekly-evolve.yml")
    blocks = _save_steps(text)
    assert blocks
    code = _code_only(blocks[0])
    assert "steps.verify.outcome" in code, (
        "weekly-evolve 的谱系资格必须取自「检查库是否仍是合格的谱系根」那一步"
    )


# ---------------------------------------------------------------- 不变量 4


def test_deploy_steps_have_a_monotonicity_guard():
    """部署路径必须有单调性守卫：产物最新日不得比线上更旧。

    只校验「有没有数据」时，孤儿 CI 库导出的 dates.json=["2026-10-13"] 能通过
    `--min-dates 1` 的门禁 → CI 会把比线上旧 16 天的站点 force-push 上去，
    页面看着正常、数据停在半个月前，比 2026-10-27 的空站点事故更隐蔽。
    """
    for name in ("daily-audit.yml", "deploy-site.yml"):
        text = _text(name)
        assert "--not-older-than" in text, (
            f"{name}: 部署步必须用 --not-older-than 守住单调性（不许发布比线上更旧的产物）"
        )
        assert "LIVE_LATEST" in text, (
            f"{name}: 应当以线上 dates.json 的最新日作为产物下限"
        )


def test_monotonicity_guard_falls_back_to_expected_day_not_no_floor():
    """查不到线上日期时，兜底必须是期望交易日，**不是**「无下限」。

    「无下限」等于回到空站点事故的同款漏洞。
    """
    for name in ("daily-audit.yml", "deploy-site.yml"):
        text = _text(name)
        assert "--expect-latest" in text, (
            f"{name}: 查不到线上日期时必须用 --expect-latest 兜底，不能无下限放行"
        )


# ---------------------------------------------------------------- 不变量 5
# 同步判据必须是「CI 自己的库」，不能是「线上站点」。
#
# 2026-10-29 两谱系事故的根因：freshness 读线上 dates.json。而站点可以被本地
# `a-finder.sh site --push` 发布 —— 一旦本地先把当天发上去，CI 判 skip →
# 跳过同步、也跳过缓存保存 → 行情谱系永远不推进。
# 而且 export_json.py 的 dates.json 来自 daily_picks/trade_plan，**不看
# daily_prices** →「站点有这天」根本不等价于「CI 库有这天的行情」。


def test_deploy_site_freshness_queries_its_own_db_not_the_site():
    """deploy-site 的同步判据必须查自己的库（db_freshness.py）。"""
    text = _text("deploy-site.yml")
    block = _step_block(text, "检查是否需要跑（判据 = CI 自己的库，不是线上站点）")
    assert block, "deploy-site 应当有 freshness 步骤"
    assert "db_freshness.py" in block, (
        "同步判据必须用 db_freshness.py 查 CI 自己的库，不能只看线上站点"
    )
    assert "--db hs300.db" in block, "判据要读 CI 自己的 hs300.db"


def test_deploy_site_restore_precedes_freshness():
    """Restore 必须在 freshness 之前 —— 否则要查自己的库时库还没拿回来。"""
    text = _text("deploy-site.yml")
    restore_at = text.find("scripts/db_lineage.py restore")
    freshness_at = text.find("- name: 检查是否需要跑")
    assert restore_at != -1 and freshness_at != -1
    assert restore_at < freshness_at, (
        "Restore 必须在 freshness 之前：判据要查 CI 自己的库，得先把库恢复出来"
    )


def test_deploy_site_splits_skip_sync_from_running():
    """skip_sync 与 site_stale 必须是**两个独立判据**，不能合并。

    把两者混成一个 skip 是旧实现的错误：库已新鲜但线上站点缺当天时，
    所有人都会跳过 → 当天没人跑下游。

    2026-11-03 起 deploy-site **不再发布**（gh-pages 由本机的
    refresh_agent + site --push 发布），但这两个判据仍然分开：
    site_stale 仍然影响要不要跑下游（同步/计算/缓存）。
    """
    text = _text("deploy-site.yml")
    assert "skip_sync" in text, "应当有独立的 skip_sync 输出"
    assert "site_stale" in text, "应当有独立的 site_stale 输出"
    # 下游门控必须用汇总后的 run（兼顾「刚同步」与「站点缺当天」）
    plan_block = _step_block(text, "汇总本班要做的事")
    assert "skip_sync" in plan_block and "site_stale" in plan_block, (
        "汇总步应同时读 skip_sync 与 site_stale 才能正确决定跑不跑下游"
    )
    assert "run=false" in plan_block and "run=true" in plan_block
    # 而且 CI 不得再发布（否则与本机双写）
    assert "peaceiris/actions-gh-pages" not in text, (
        "deploy-site 已不应再发布（本机是唯一日常写者）"
    )


def test_deploy_site_does_not_reupload_unchanged_db():
    """没同步时不该再上传一份谱系。

    密集排班 8 班/天，而库（瘦身后）也有近百 MB —— 没同步时再传一次只是
    白耗时间与带宽，而且会让谱系根频繁变动，增大并发覆盖的风险。
    """
    text = _text("deploy-site.yml")
    blocks = _save_steps(text)
    assert blocks
    assert any("skip_sync" in b for b in blocks), (
        "谱系保存条件里必须有 skip_sync != 'true'（没同步就不要重复上传）"
    )


def test_daily_audit_treats_gap_free_db_as_clean_lineage_root():
    """体检**无缺口**时也必须算「干净」，不能因为复检没跑就隔离健康库。

    复检步骤的 if 是 `audit_rc == '1'`（有缺口才复检）。所以体检无缺口时
    `recheck_rc` 是**空字符串**，`'' == '0'` 为假 —— 只看 recheck_rc 的三元表达式
    会把一个干干净净的库判成 degraded 隔离掉，于是「体检通过反而让谱系不推进」。
    这是本仓库改这行时真实踩过的反向 bug，用测试钉住。
    """
    text = _text("daily-audit.yml")
    blocks = _save_steps(text)
    assert blocks, "daily-audit 应当有 db_lineage.py save 步骤"
    save = _code_only(blocks[0])
    assert "audit_rc" in save, (
        "谱系资格必须看 audit_rc（体检无缺口时复检没跑，只看 recheck 会错判）"
    )
    assert "recheck_rc" in save, (
        "谱系资格也必须看 recheck_rc（修复后复检干净也算合格）"
    )
    # 两个条件是「或」：任一为 '0' 即合格
    assert "||" in save, "两个合格条件之间应当是 ||（任一满足即可）"


def test_daily_audit_quarantines_when_audit_could_not_judge():
    """体检 rc=2（无法判定）时不得当成合格 —— OK 必须默认 false。

    判据只认 '0'：audit_rc='2' 与 recheck_rc='' 都不等于 '0' → OK 保持 false
    → db_lineage save 跳过，线上谱系根不被覆盖。
    这里显式写下语义，防止将来有人把 `= "0"` 放宽成「非空即合格」。
    """
    text = _text("daily-audit.yml")
    save = _code_only(_save_steps(text)[0])
    assert 'OK="false"' in save, "合格标志必须默认 false（rc 缺失/异常时不得判合格）"
    assert "!= ''" not in save, (
        "不得用「非空即合格」的判据：audit_rc=2 会被误判为合格"
    )
    assert '= "0"' in save, "判据只认 rc == 0"


# ---------------------------------------------------------------- 不变量 6
# 「管道 + $?」必须开 pipefail，否则 rc 永远是最后一环（tee）的 0。


def test_gap_heal_post_check_captures_real_exit_code():
    """gap-heal 补抓后体检的 rc 必须来自 check_gaps，不是 tee。

    `python3 ... | tee f; rc=$?` 里 `$?` 是 **tee** 的状态（恒 0），所以：
      - 「补抓后仍有缺口则失败」这道红灯永不亮；
      - 缓存 key 的 clean 恒为 true → 谱系守卫等于没生效。
    实测（30 天全缺的库）：旧写法 rc=0；开 pipefail 后 rc=1。
    """
    text = _text("gap-heal.yml")
    block = _code_only(_step_block(text, "体检（补抓后，仍有缺口则失败）"))
    assert block, "gap-heal 应当有补抓后的体检步骤"
    assert "pipefail" in block, (
        "必须在管道前开 set -o pipefail：GitHub 默认 shell 是 `bash -e {0}`，"
        "不带 pipefail，rc=$? 会取到 tee 的 0"
    )
    assert "clean=false" in block, "体检不过关必须写出 clean=false"


def test_weekly_evolve_requires_strictly_latest_db():
    """weekly-evolve 只让**严格最新**的库当谱系根，不许「落后 ≤3 天」也算合格。

    它与 deploy-site 都在周五 08:30 UTC 跑；若它恢复到的缓存比 deploy-site 本轮
    要保存的更旧，它保存的库就会**倒退**谱系 1~3 天（restore-keys 取最新条目）。
    """
    text = _text("weekly-evolve.yml")
    code = _code_only(text)
    assert "lag > 0" in code, (
        "谱系根资格必须是 lag == 0（严格最新）；放宽成 lag > 3 会让根倒退"
    )
    assert "lag > 3" not in code, "不得再容忍落后 3 天"
