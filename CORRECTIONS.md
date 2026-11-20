# 已释放日批的勘误交班

V1 入库、冻结、首执、双域放行、退卷及离线读回见 README.md。本次办理以一个已放行的 V1 父批、一项改值和两个指定目标卷为边界。已存旧件按各自原号领取，V1 退卷照常。

## 办理

新增 `custody.corrections.publish_correction(catalogue, request_id, parent_volume, parent_copy, correction, targets, *, after_event=None)`，返回 `(batch_id, receipt)`。

- `catalogue`、`parent_volume` 和目标卷连接均由调用者打开、关闭。`request_id` 是非空字符串。`parent_copy` 为 `media.volume.CopyIdentity`。首次办理时它须是该目录已放行父批的已验有效副本，本站实读须与目录原首执吻合。卷、站、批、域或副本绑定不合时拒绝。办理不访问热源。
- `correction` 是 `(station, row_number, millilitres, correction_source)`，沿用 V1 的行键、整数毫升或 `None` 以及 `SourceReference`。只更改所指行的读值，保留该行原出处和原序，另存改前原行与勘误出处。未改行原样保留。等值改动也保留这次勘误的出处。
- `targets` 是两个 `(volume, copy_id)` 的序列，卷须在本目录登记。两个副本各自保存完整候选。验收以实读完整正文和谱系为准，核对指定副本及登记故障域。同域、一域或属于不同候选的两份材料不能联合签发。分别验证整数毫升及缺测，合计相同不能替代逐行和谱系核对。
- 只有两个目标均验收同一候选后才允许在目录正式固定后继号、原办理号、父批和父首执关系及正式首执。后继号须可与 V1 批号并存。`audit.receipts.load_receipt(catalogue, batch_id)` 在正式提交前对拟用后继号抛 `KeyError`，提交后返回该正式首执。首执为 `audit.receipts.Receipt`，绑定完整正文和谱系，合计为精确整数毫升。
- 正式发布后，卷仍须持久取得与本候选及该卷相符的正式发布依据才可独立领取。两个目标卷都能领取后，办理才返回成功。沿用现有可信中心及可信卷站边界。允许全量实读及复用单库事务。

## 领取和续办

沿用 `audit.retrieve.read(batch_id, volume=volume, expected_copy=copy)`，`expected_copy` 仍可省略。后继报告暴露 `batch_id`、`receipt`、`rows`、`total_millilitres` 以及 `request_id`、`parent_copy`、`parent_receipt`、`before`、`correction_source`。`before` 为更正前的 `ObservationRow`。从卷独立核对正文、原首执关系、勘误出处及正式发布依据。理由相符且该卷已接纳正式依据时，读回持久的同一正式首执。已有候选但该卷仍待发布时抛 `media.readback.PendingCorrectionError`，其为 `ValueError` 的子类。一个卷从未准备该号时仍按缺号抛 `KeyError`。原卷内对外读回入口同样受领取门槛约束。

已固定的办理号绑定旧副本、勘误和两个目标副本，重开可同参续办或复领。异参重投抛 `ValueError`。非法输入、身份或内容冲突抛 `ValueError`，缺批或缺原行沿 V1 抛 `KeyError`，底层写入异常原样传播。失败的提交须回滚，其前已完成的合法阶段可保留。已提交的正式首执在回包丢失后仍可重取。

`after_event(event, batch_id, target)` 用于观察或中断已提交的边界。`batch_id` 是本次拟用后继号，成功时为正式号。卷端按 `targets` 顺序推进，已提交阶段可跳过。观察器异常原样传播。

| event | 提交后的状态 | target |
| --- | --- | --- |
| `prepared` | 目标卷已持久准备完整候选，可供办理验收 | 该目标的 `CopyIdentity` |
| `published` | 目录的双域签发已提交，正式首执可按号读出 | `None` |
| `available` | 目标卷已持久取得发布依据，可离线领取 | 该目标的 `CopyIdentity` |

## 样例与运行

`correction_cases.seed_day(root)` 在新建的工作目录中准备旧批及 C、D 两卷，返回旧报告和具体旧副本身份，退出时已关闭热源等连接。旧行依次是 `7000, None, 11000`，出处为 `station-ledger` 第 21、22、23 行。用返回的 `copies['C']` 作父副本，打开 `catalogue.sqlite`、`C.sqlite`、`D.sqlite`，以办理号 `late-1`、模块里的 `CORRECTION` 和 `((c, 'new-C'), (d, 'new-D'))` 办理。改前第一行是 7000 mL，勘误出处为 `late-note` 第 5 行。新账为 `8000, None, 11000`，原出处顺序不变，合计 19000 mL，父首执等于旧卷的原首执。

`StopAfter('prepared', 'station-C')` 可模拟首卷准备后中断。此时目录无新首执，C 按拟用号报待发布，D 缺号，旧批两卷均仍可领。停在 `published` 后可重领中心首执，两卷尚待发布。停在 `available` 的 `station-C` 后断开目录，C 可领，D 仍待发布。停在 `available` 的 `station-D` 后则两卷均可领，同参重投返回原号与原首执。另可用 `seed_day(root, add_same_domain=True)` 准备同域的 C2，与 C 同作目标时须拒签。将第二行由缺测改为实测零时，总量仍是 18000 mL，原位状态和勘误出处须保真。

`python3 -B correction_cases.py` 运行 V1 的正常对照。原运行清单是 `.pointwise-runtime.json`，沿该清单保留并运行完整 V1 回归，并接入本次断源勘误核验与交班演练。核验发现业务偏差须以非零状态退出。演练展示首卷准备时的待发布，以及重开后从两个卷分别领取的 19000 mL 新账、谱系、首执和 18000 mL 旧账。
