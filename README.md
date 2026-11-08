# 赛前盘口看板

每 6 小时自动更新：选定的 20 个联赛（见 board/leagues.py）未来 48 小时的比赛，每场显示
- **大小球**：盘口线和"市场预期进球"从开盘到现在怎么变
- **亚盘**：主队让球线和"主队净胜球预期"的变化
- **胜平负**：去水后的主/平/客概率变化
- **球队近况**：近 10 场进失球、大 2.5、双方进球、零封、主客场拆分
- **信号**：盘口大幅移动、软庄赔率高于 Pinnacle 公平价、基本面和盘口方向矛盾

数据：OddsPapi（免费历史赔率接口，Pinnacle 等庄家的完整变盘记录）+ ESPN 赛果。

## 设置
1. Settings → Secrets and variables → Actions → New repository secret：名字 `ODDSPAPI_KEY`，值填你的 OddsPapi key。
2. Settings → Pages → Source 选 **GitHub Actions**。
3. Actions → update-board → Run workflow（第一次手动跑；之后每 6 小时自动跑）。

可选的仓库变量（Settings → Variables）：`HOURS`（默认 36）、`MAX_FIXTURES`（默认 700）、`BOOKS`（默认 pinnacle,bet365,1xbet）。

只提供信息，不是投注建议。
