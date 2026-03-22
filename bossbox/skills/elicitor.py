"""Skill elicitor — implemented in Step 18."""
class ElicitationResult:
    pass
class ElicitationSession:
    def start(self): raise NotImplementedError("Implemented in Step 18")
    def respond(self, msg): raise NotImplementedError("Implemented in Step 18")
    def finalize(self): raise NotImplementedError("Implemented in Step 18")
