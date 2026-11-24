from dataclasses import dataclass

from capture.origin import CapturedBlock
from capture.recovery import MaterialReader, ScanMaterialRef
from capture.retention import MaterialUnavailable
from quality.scanbook import ScanStatus


@dataclass(frozen=True)
class RequestBinding:
    shift_id: str
    scan_id: str
    material: ScanMaterialRef


def bind_scan(status: ScanStatus, reader: MaterialReader) -> RequestBinding:
    if status.state != 'complete':
        raise MaterialUnavailable('only a complete scan can be bound')
    reference = reader.reference_for_scan(status.shift_id, status.scan_id)
    return RequestBinding(status.shift_id, status.scan_id, reference)


def read_bound_material(binding: RequestBinding, reader: MaterialReader) -> tuple[CapturedBlock, ...]:
    if (binding.shift_id, binding.scan_id) != (binding.material.shift_id, binding.material.scan_id):
        raise MaterialUnavailable('bound material refers to a different scan')
    return reader.read_scan_material(binding.material)
