from dataclasses import dataclass

from archive.results import ResultHeader, ResultIntegrityError, ResultSnapshot
from capture.origin import origins_match
from quality.scanbook import ScanStatus
from quality.spread import integer_spread


@dataclass(frozen=True)
class RegisterRow:
    status: ScanStatus
    result_ids: tuple[int, ...]


def link_published_results(scans: tuple[ScanStatus, ...], results: tuple[ResultHeader, ...]) -> tuple[RegisterRow, ...]:
    by_scan = {(status.shift_id, status.scan_id): status for status in scans}
    links: dict[tuple[str, str], list[int]] = {key: [] for key in by_scan}
    for result in results:
        key = (result.shift_id, result.scan_id)
        status = by_scan.get(key)
        if (
            status is None or status.state != 'complete'
            or result.profile != status.profile or result.spread != status.spread
        ):
            raise ResultIntegrityError(f'result {result.result_id} disagrees with its registered scan')
        links[key].append(result.result_id)
    return tuple(RegisterRow(status, tuple(links[status.shift_id, status.scan_id])) for status in scans)


def verify_result_snapshot(snapshot: ResultSnapshot) -> ResultSnapshot:
    header = snapshot.header
    blocks = snapshot.blocks
    if tuple(block.origin.slot for block in blocks) != header.profile.slots:
        raise ResultIntegrityError('historical result slots disagree with its stored profile')
    if not origins_match(blocks, header.shift_id, header.scan_id):
        raise ResultIntegrityError('historical result has inconsistent original identities')
    if integer_spread(block.value for block in blocks) != header.spread:
        raise ResultIntegrityError('historical result spread disagrees with its stored readings')
    return snapshot
