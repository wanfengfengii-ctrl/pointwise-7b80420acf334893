from dataclasses import dataclass

from capture.origin import CapturedBlock, origins_match
from capture.retention import MaterialStore, MaterialUnavailable


@dataclass(frozen=True)
class ScanMaterialRef:
    shift_id: str
    scan_id: str
    material_id: int


class MaterialReader:
    def __init__(self, materials: MaterialStore):
        self.materials = materials

    def reference_for_scan(self, shift_id: str, scan_id: str) -> ScanMaterialRef:
        material_id = self.materials.material_id(shift_id, scan_id)
        if material_id is None:
            raise MaterialUnavailable(f'no sealed material for {shift_id}/{scan_id}')
        return ScanMaterialRef(shift_id, scan_id, material_id)

    def read_scan_material(self, reference: ScanMaterialRef) -> tuple[CapturedBlock, ...]:
        blocks = self.materials.read_sealed(reference.material_id, reference.shift_id, reference.scan_id)
        if not origins_match(blocks, reference.shift_id, reference.scan_id):
            raise MaterialUnavailable('sealed material origin does not match its scan')
        return blocks
