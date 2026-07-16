class SaathiError(Exception):
    pass


class AllocationError(SaathiError):
    pass


class VerificationError(SaathiError):
    pass


class NeedsHumanReviewError(VerificationError):
    pass


class InvalidStateTransitionError(SaathiError):
    pass
