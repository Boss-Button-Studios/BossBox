"""VRAM Budgeter — implemented in Step 7."""
class VRAMBudgeter:
    def request_load(self, model: str) -> bool:
        raise NotImplementedError("Implemented in Step 7")
    def current_allocation(self) -> dict:
        raise NotImplementedError("Implemented in Step 7")
    def available(self) -> float:
        raise NotImplementedError("Implemented in Step 7")
