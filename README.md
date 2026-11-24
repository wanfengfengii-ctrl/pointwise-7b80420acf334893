# 带材线阵质检交班站

本站为一条带材线的单设备质检班保存可离线核对的原读。设备员播放逐行 JSON 的设备录制，交班员监视结论，质检员按申请领整带，下班质检员按班次及成果号复核。Python 3.11 标准库和本地 SQLite 即可运行。命令从项目根目录执行。

```sh
python3 -B check_project.py
python3 -B -m handover.workday demo --recording examples/shift.jsonl
```

`check_project.py` 由运行环境提供。演示用独立临时工作库播放给定样例，执行实际申请、签存、断源复领和清单查询。

## 一天工作

1. 开班 J1，固定槽序 L、R。
2. S1 开扫并接收 L=7。交班员能看到进行中，此时第一次取片返回待件。
3. S1 接收 R=11 后关带。材料与完整结论同时确立，极差为 4。
4. S2 开扫并将 L 覆写为 19。设备先在同步通知内提供旧块。质检员在覆写后提交 Q1，申请仍选 S1。
5. S2 收到坏尾块并作废。质检员仍可选 S1，交班员可查坏尾原因。
6. S3 两槽各读 6 并完整关带。两个设备出处分别保存，极差为 0。Q2 选择 S3。
7. 先兑现 Q2。Q1 保持待兑现，生产继续。
8. 停源并重新打开工作库。按 Q1 签存 S1，按原成果号读回 Q2。两带各保留原读和出处。
9. 读取 J1 清单。扫描行次为 S1、S2、S3，各行附上自己的已提交成果号。

## 设备录制与数据口径

每行一个 UTF-8 JSON 对象。`kind` 为 `start`、`write` 或 `end`。每笔都有非空字符串 `scan`、`event_id` 和正整数 `seq`，原序上限为 SQLite 有符号整数上限。`write` 另有非空字符串 `slot` 和 `read`，可用非空字符串 `bad` 声明设备报告的坏件原因。值为 JSON 整数时有效，布尔、空值和其他值作为坏件事实处理。槽位缺失属于结构错误。`EventPosition` 保存录制标识及从 1 开始的位置，文件读入使用文件名和物理行号。班次内一次最多有一带在采。拒绝结构错误、原序倒退、重复开扫和当前进行中扫描以外的写入或关带，拒绝事件可用原序更正重投。设备通知在旧槽仍占用时送达，通知返回后读取窗口失效。关闭源端会清空本地设备槽并结束交付。

原件由班次、扫描、槽和设备事件原序标识，出处另含事件号及录制位置。整带依固定规格槽序输出。极差是最大原读减最小原读，单位为设备原读计数，允许任意精度的负整数和零。整数读值和极差在库中采用规范十进制文本。内容坏件、重复槽、缺槽或空带使整带作废并保留原因。作废带的暂存取片材料随关带撤下。相同读值的不同槽仍是独立原件。

## Python 接口

```python
from device.events import EventPosition
from handover.workday import open_workday

with open_workday("station.sqlite", "J1", ("L", "R")) as day:
    start = {"kind": "start", "seq": 1, "scan": "S1", "event_id": "E1"}
    day.record_event(start, EventPosition("bench", 1))
    print(day.scan_status("S1").state)
    print(day.request_scan("Q1"))  # None，待件
```

- `open_workday(path, shift_id, slots=None, online=True)` 初始化工作库或打开既有班次。新在线班次须给槽序，老班次可省略槽序，重复指定须相同。离线打开用 `online=False` 并使用已知班次。在线重开将沿已接受原序继续，设备槽属于当前连接。调用方为一个班次安排一个在线设备。不同档案连接可同时读取或提交。
- `record_event(payload, position)` 返回已接受事件。接口错误抛 `EventRejected`，带 `position` 和 `reason`。可用 `device.recording.read_recording(path)` 逐笔取得输入及位置。源连接关闭后拒绝新录制。
- `scan_status(scan_id)` 返回进行中 `in_progress`、完整 `complete` 或作废 `void` 的 `ScanStatus`，未知扫描返回 `None`。完整带有整数 `spread`，作废带有 `reason`。
- `request_scan(request_id)` 在受理事务内选择本班最近完整带，持久绑定原扫描及材料。待件返回 `None` 且可用同号稍后重试。已受理号再次提交返回原申请。申请号在工作库内唯一，跨班使用已受理号报冲突。
- `fulfill_request(request_id)` 返回稳定的整数成果号，未知申请返回 `None`。材料不全、原据不符或存储失败会抛异常并回滚。可停源后按原申请号重试。
- `shift_register(shift_id)` 返回按开扫原序的终态清单，已签存成果挂在自己的扫描行。未知班次返回 `None`。`read_result(result_id)` 按成果号读固定快照，未知号返回 `None`。`snapshot.header` 包含扫描、规格和极差，`snapshot.blocks` 按规格槽序保存原读与出处。

扫描状态、材料确立、申请受理及签存各有清晰的持久边界。已签存结果的离线复核使用成果快照。初始化可重复执行，保留所有既有工作数据。只有显式演示命令会播放样例。
