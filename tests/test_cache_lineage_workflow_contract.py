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


def _save_steps(text: str):
    """找出所有**保存缓存**的步骤块。

    判据是步骤体内出现 `key:` 且值含 `hs300-db`（actions/cache@v4 的保存写法），
    而不是步骤名里有没有「缓存」两个字 —— deploy-site / weekly-evolve 的步骤名
    是英文的，靠名字匹配会漏。

    区分 restore 与 save：restore 步骤带 `restore-keys`（读缓存），save 步骤没有。
    **必须忽略注释行** —— 步骤里常有「否则 restore-keys 仍然会命中它」这类说明性
    注释，按整块做子串匹配会被注释里的字样误判成 restore 步骤。
    """
    blocks = []
    for line in text.splitlines():
        if not line.strip().startswith("- name:"):
            continue
        block = _step_block(text, line.strip()[len("- name:"):].strip())
        if not block:
            continue
        code_lines = [ln for ln in block.splitlines()
                      if not ln.strip().startswith("#")]
        code = "\n".join(code_lines)
        if re.search(r"^\s*key:\s*.*hs300-db", code, re.M) and "restore-keys" not in code:
            blocks.append(block)
    return blocks


# ---------------------------------------------------------------- 不变量 1


def test_no_workflow_uses_a_fixed_cache_key():
    """缓存 key 必须每次唯一（带 run_id）。

    固定 key 的后果是永久冻结：actions/cache 不允许覆盖已存在的 key，
    之后每天从同一份旧库增量同步，数据逐渐落后（2026-10-26 的真实事故）。
    """
    for name in SHARING_WORKFLOWS:
        text = _text(name)
        for block in _save_steps(text):
            keys = re.findall(r"^\s*key:\s*(.+)$", block, re.M)
            assert keys, f"{name}: 缓存保存步骤必须显式给 key"
            for key in keys:
                assert "github.run_id" in key, (
                    f"{name}: 缓存 key 必须是每次唯一的（含 github.run_id），实际 {key!r}"
                )


# ---------------------------------------------------------------- 不变量 2


def test_restore_keys_use_the_run_id_prefix_with_trailing_dash():
    """restore 前缀必须是 `hs300-db-`（带尾横线）。

    `'hs300-db'.startswith('hs300-db-')` 是 False —— 少了尾横线就够不到旧条目，
    谱系会**断**而不是**接上**（2026-10-29 查明的关键细节）。
    """
    for name in SHARING_WORKFLOWS:
        text = _text(name)
        prefixes = re.findall(r"^\s*hs300-db-?\s*$", text, re.M)
        for p in prefixes:
            assert p.strip() == "hs300-db-", (
                f"{name}: restore-keys 前缀必须是 'hs300-db-'（带尾横线），实际 {p.strip()!r}"
            )


# ---------------------------------------------------------------- 不变量 3


def test_degraded_cache_key_does_not_match_restore_prefix():
    """隔离用的 `degraded-` 前缀**不能**以 `hs300-db` 开头。

    否则 restore-keys 仍然会命中被隔离的坏库 —— 隔离形同虚设。
    实际 key 形如：
      ${{ cond && format('hs300-db-{0}', run_id) || format('degraded-hs300-db-{0}', run_id) }}
    两条分支的键名前缀分别是 `hs300-db-`（合格）与 `degraded-hs300-db-`（隔离）。
    隔离分支整串以 `degraded-` 开头 → restore-keys 的 `hs300-db-` 匹配不到它。
    """
    for name in SHARING_WORKFLOWS:
        text = _text(name)
        keys = re.findall(r"^\s*key:\s*(.+)$", text, re.M)
        degraded_keys = [k for k in keys if "degraded-" in k]
        assert degraded_keys, f"{name}: 应当有 degraded- 前缀的隔离缓存 key"
        for key in degraded_keys:
            # 取出所有 format('...') 的键名模板
            prefixes = re.findall(r"format\('([^']+)'", key)
            assert prefixes, f"{name}: 隔离缓存 key 应当用 format() 给出键名：{key!r}"
            isolated = [p for p in prefixes if p.startswith("degraded-")]
            assert isolated, f"{name}: 应当有以 'degraded-' 开头的隔离分支：{key!r}"
            for prefix in isolated:
                # 判据是 restore-keys 的真实匹配语义：
                #   缓存键 K 会被 `restore-keys: hs300-db-` 命中 ⟺ K.startswith("hs300-db-")
                # 隔离键以 "degraded-" 开头 → 必然不命中。
                assert not prefix.startswith("hs300-db-"), (
                    f"{name}: 隔离分支的键名不能以 'hs300-db-' 开头，"
                    f"否则 restore-keys 仍会命中：{prefix!r}"
                )
                assert prefix.startswith("degraded-"), (
                    f"{name}: 隔离分支的键名必须以 'degraded-' 开头：{prefix!r}"
                )


def test_cache_save_is_conditional_in_every_workflow():
    """每个 workflow 保存缓存时都要有资格判定，不能无脑 `if: always()` 就写谱系根。

    `if: always()` 让一次失败的 run 也成为谱系根 → 缺口被固化并逐日传递。
    """
    for name in SHARING_WORKFLOWS:
        text = _text(name)
        blocks = _save_steps(text)
        assert blocks, f"{name}: 应当有保存缓存的步骤"
        for block in blocks:
            assert "if:" in block or "steps." in block, (
                f"{name}: 缓存保存步骤必须带条件（不能无条件成为谱系根）"
            )


def test_weekly_evolve_has_a_lineage_qualification_step():
    """weekly-evolve 原本保存缓存**完全没有 if 守卫** —— 不受控的谱系根写入者。

    它只跑 `stock_cli.py evolve`，不跑体检/补抓，所以必须自带「库是否合格」的判定。
    """
    text = _text("weekly-evolve.yml")
    blocks = _save_steps(text)
    assert blocks
    has_guard = any("daily_prices" in b or "谱系" in b for b in blocks)
    assert has_guard, \
        "weekly-evolve 必须自带谱系资格判定（检查 daily_prices 最新日）"


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
    restore_at = text.find("- name: Restore hs300.db from cache")
    freshness_at = text.find("- name: 检查是否需要跑")
    assert restore_at != -1 and freshness_at != -1
    assert restore_at < freshness_at, (
        "Restore 必须在 freshness 之前：判据要查 CI 自己的库，得先把库恢复出来"
    )


def test_deploy_site_splits_skip_sync_from_publishing():
    """sync 与 publish 必须分开决策。

    把两者混成一个 skip 是旧实现的错误：库已新鲜但线上站点缺当天时，
    所有人都会跳过 → 当天没人发布。
    """
    text = _text("deploy-site.yml")
    assert "skip_sync" in text, "应当有独立的 skip_sync 输出"
    assert "site_stale" in text, "应当有独立的 site_stale 输出（决定要不要发布）"
    # 发布条件不能只看 skip_sync：site_stale=true 时库新鲜也要发布
    deploy_block = _step_block(text, "部署 gh-pages")
    assert "run == 'true'" in deploy_block, (
        "部署条件应当用汇总后的 run 输出（兼顾「刚同步」与「站点缺当天」两种情况）"
    )


def test_deploy_site_does_not_resave_unchanged_db():
    """没同步时不该再存一份缓存。

    密集排班 8 班/天 × 200MB × 7 天保留 ≈ 11GB，会打爆 actions/cache 的 10GB
    配额（超额会淘汰别的条目，包括早班的正常缓存）。
    """
    text = _text("deploy-site.yml")
    blocks = [b for b in _save_steps(text) if "actions/cache" in b]
    assert blocks
    assert any("skip_sync" in b for b in blocks), (
        "缓存保存条件里必须有 skip_sync != 'true'（没同步就不要重复存）"
    )
