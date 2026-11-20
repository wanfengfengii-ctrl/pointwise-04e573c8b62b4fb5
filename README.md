# 流域观测资料中心

水文站把日测与在班勘误交给录入员。归档员要腾出热源，同时在不同故障域留下两份完整日批。夜班可轮换卷站，审计员按冻结批号从指定存续卷复核汇交毫升数及出处。

## 一天的交班

1. 接纳带站点、原行号和原出处的站测，形成日批的初始修订。
2. 按明确的修订冻结快照。勘误从指定旧修订产生新修订，在原行位置更正值，另存勘误出处。旧冻结批仍按自己的快照领取。
3. 核对原序和毫升总量，首次封存时固定首执。样例依次是 7000 mL、缺测、11000 mL，合计为 18000 mL。零是实际观测，全缺测的合计为零。读值与总量按规范十进制文本存储，按 Python 整数累计，支持跨 SQLite 整数上界的精确值。
4. 把完整快照、封签、原序行及首执一次提交到独立卷站，逐卷实读验收并登记故障域。
5. 两个独立域都有合格副本时，目录记录热源可释放，归档员据此关闭热源。任一所选卷可独立领取该批。
6. A、B、C 三域均已验时，夜班申请退 A。许可一经提交，A 就退出保底集合。此时退 B 会留下单域。独立域 D 补入并验收后，B 可换新号再申请。
7. 卷站凭匹配的许可脱机退出指定副本。站库记下首个结果，目录收到匹配结果再结清原号。回包丢失时可重领许可和站端结果。
8. 从 C 和 D 各自领取原序、原出处、精确合计及原首执。补副和退卷沿用原冻结身份及首执。

## 运行

仅需 Python 3.11 标准库。在工程目录运行完整回归和交班演练。完整回归包含 V1 套件与晚到勘误套件（断源、单域拒领、待发布门槛、中断重开及两卷各领新旧账）；`demo.py` 在 V1 交班演练后追加勘误交班演练，`correction_cases.py` 另给 V1 正常对照与独立勘误演练。

```sh
python3 -B check_project.py
python3 -B demo.py
```

控制台物化工程时提供 `check_project.py`。独立使用这些源码时，可运行 `python3 -B -m unittest discover -s tests -t .` 发现完整套件。测试使用独立临时库。演练默认使用临时工作目录，也可用 `python3 -B demo.py --directory ./sample-work` 保留档案。指定目录首次成功演练后保留 `sample-day`，重跑直接读取其存续卷。使用新目录可重演整天。成功退出码为 0，演练的业务断言失败退出码为 2 并输出业务标记，文件和 SQLite 异常沿 Python 错误路径退出。

## Python 接口

初始化函数接收文件路径。`batch.storage.initialize_source_store(path, source_id)`、`custody.catalogue.initialize_catalogue(path, catalogue_id)`、`media.volume.initialize_volume(path, identity, catalogue_id, permit_key)` 分别初始化热源、目录和卷站。以相同身份及配置重复初始化保留原数据，以冲突身份初始化拒绝。对应的 `open_source_store`、`open_catalogue`、`open_volume` 打开既有库，调用者负责关闭连接。控制台样例给各卷使用 `secrets.token_bytes(32)` 生成独立的许可密钥。实际初始化应保管好每卷的密钥与数据库。中心和本站持有同一卷的许可核验材料，站库无需连接中心即可验许。读取完整性以持久首执和实读封签为依据，卷数据库由可信站端维护。

- `batch.intake.intake(source, day_id, rows, unit='mL')` 返回初始修订号。`rows` 由 `ObservationRow(station, row_number, millilitres, SourceReference(document, line))` 组成。读数是整数或 `None`。勘误传入 `previous_revision=...` 和 `correction=(station, row_number, value, correction_source)`，省略 `rows`。`batch.revisions.load_revision` 可查看勘误来源。
- `batch.freeze.freeze(source, day_id, revision)` 返回 `FrozenBatch`。`audit.retrieve.read(batch_id, source=source, catalogue=catalogue)` 从冻结快照出具或重领首执，返回含冻结批及首执的 `ArchiveReport`。
- `custody.registry.register_volume_domain(catalogue, volume)` 登记卷站身份与故障域。卷配置的 `VolumeIdentity(site, volume, domain)` 持久且固定。`media.replicate.replicate(report, volume, copy_id)` 返回 `CopyIdentity`，同一批在同卷有一份当前副本。退卷后的副本号保留为历史，重新补入用新副本号。
- `custody.verification.verify(catalogue, volume, copy)` 按指定副本验收并登记。`custody.release.release(catalogue, batch_id)` 返回放行布尔值。已验副本按有效故障域去重，放行只在两个独立域成立时持久登记。
- `retire.permits.permit(catalogue, RetireRequest(request_id, batch_id, copy))` 返回 `RetirementDecision`。保管不足的原请求为 `rejected`，许可为空。可用新请求号在补副验收后办理。获准为 `permitted`，已结清为 `settled`。同号同参重领，异参报错。`preview_retirement` 提供建议性剩余域，`after_precheck` 可观察预查与提交之间的事件，实际许可始终按目录写事务中的资格裁决。所有退卷都要求退出后仍有双域，含热源尚未释放时的申请。
- `retire.execution.execute(volume, decision.permit)` 验证许可并返回站端 `ExitResult`。原许可重投会重领结果。`retire.reconciliation.reconcile(catalogue, result)` 核对并结清原请求，重复回包重领原结果。
- `audit.retrieve.read(batch_id, volume=volume, expected_copy=copy)` 从所选站库领取，`expected_copy` 可省略。领取按实际行复核首执及合计。缺批、缺修订或未登记对象以 `KeyError` 拒绝，输入、身份、完整性或状态冲突以 `ValueError` 拒绝，底层存储故障保留 SQLite 异常。拒绝时无部分报告。

## 已释放日批的晚到勘误

`custody.corrections.publish_correction(catalogue, request_id, parent_volume, parent_copy, correction, targets, *, after_event=None)` 在不访问热源的前提下办理勘误：父材料从已放行父批的已验存续卷实读，两个独立故障域的目标卷各自准备完整候选并经实读验收后，目录才固定后继号与正式首执（`audit.receipts.Receipt`），随后把发布依据逐卷送达。返回 `(batch_id, receipt)`。候选在取得发布依据前经 `audit.retrieve.read` 领取会抛 `media.readback.PendingCorrectionError`（`ValueError` 子类），卷从未准备该号时仍为 `KeyError`。中断后以同号同绑定续办，异参重投抛 `ValueError`；详见 CORRECTIONS.md。

## 回归范围

覆盖日批原序及缺测、合法勘误与旧批、零读数、跨整数范围计量、双域放行、同域及未验副本、缺行或出处失配、写入回滚、断源首执、正常退卷、错卷、许可重领、旧预查、补副、重开、丢回包和终局重投。晚到勘误另覆盖断源办理、待发布与缺号门槛、同域及错父件拒签、跨候选材料、逐行谱系封签、写入回滚续办、`prepared/published/available` 中断重开、异参拒绝及两卷离线领取同一新账。
