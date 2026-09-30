# 本机—GitHub—服务器同步

目标结构：本机和服务器各有独立 Git clone，通过私有 GitHub 仓库同步代码、来源材料、人物配置和实验设置。`runs/` 的输出与服务器密钥不走 Git。

仓库预定名称 `RoleSimulationFramework`。当前 GitHub CLI 尚未登录时，远端创建必须等待账号授权；有本地提交并不代表已有远端仓库。以下命令中的 `OWNER` 应替换为实际创建仓库的账号。

## 首次建立 GitHub 仓库

```bash
gh auth login --hostname github.com --git-protocol https --web
gh auth status
gh repo create OWNER/RoleSimulationFramework --private --source . --remote origin --push
```

创建前需确认本地已有 `main` 初始提交。不要勾选另外初始化 README，以免出现两套起始历史。若远端已创建但首次 push 失败，应先检查远端与 `origin`，恢复同一次推送，不重复新建仓库。

也可以在仓库根目录运行 `bash scripts/publish_private_repo.sh`：脚本检查干净工作区、main 分支、当前登录账号与私有可见性后创建/同步同名仓库。它不会修改可见性或覆盖不匹配的远端。

## 服务器首次接入

先为服务器配置该私有仓库的读取权限。仅运行实验的服务器可使用仓库级只读 deploy key；如果需要从服务器提交代码，使用具备适当权限的账号凭据并走独立分支。具体凭据由服务器环境管理，不写进仓库。

```bash
git clone git@github.com:OWNER/RoleSimulationFramework.git
cd RoleSimulationFramework
python3.11 -m unittest discover -s tests -v
python3.11 -m rolesim run --out runs/server-smoke
```

运行时无第三方 Python 依赖，先用离线模式验证环境，再配置服务器模型接口。当前目录与服务器地址未指定，因此本次未对任何服务器执行部署或远程登录。

## 日常开发与同步

本机：创建功能分支 → 修改 → 测试 → 提交 → 推送 → 合并到 main。源码、来源和机制定义改变时需保留版本记录。心理学资料是研究材料，默认在私有仓库内协作，不能因为引用了开源代码就自动转为公开资料。

服务器：结束依赖当前代码的实验 → 确认工作区无修改 → `bash scripts/sync_from_github.sh`。脚本仅接受 main，使用 fetch 和 fast-forward 更新并拒绝本地未推送提交；不会 reset、覆盖或自动 stash。更新后运行测试，最后打印所用提交 SHA。

如果服务器也开发：另开功能分支并 push，经评审合并后再让运行环境更新。避免两端同时在 main 修改同一份人物配置。正在运行的长实验应固定一个提交或独立 clone，不能在中途拉取新代码。

## 实验结果

每次运行保留 manifest、输入快照和日志。当前 manifest 含配置哈希与模型，不含 Git SHA；正式实验登记时另保存 `git rev-parse HEAD` 与实际模型服务版本。输出另存受控目录或对象存储，按需传递完整运行目录，不能只同步最终分数。

如需在 Git 中登记结果，可提交小型摘要，写清：提交 SHA、输入哈希、模型、运行模式、样本规模、完成/失败数和数据存放位置。不要提交 API key、私有模型凭据、完整敏感轨迹或大型缓存。

GitHub Actions 配置已提供。只有实际 push 后远端任务成功，才能称“GitHub CI 通过”；本机测试不替代远端运行结果。
