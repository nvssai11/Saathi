class SaathiError(Exception):
    pass


class AllocationError(SaathiError):
    pass


class VerificationError(SaathiError):
    pass


class NeedsHumanReviewError(VerificationError):
    def __init__(self, message: str, *, thread_id: str | None = None) -> None:
        super().__init__(message)
        self.thread_id = thread_id


class InvalidStateTransitionError(SaathiError):
    pass


class OtpError(SaathiError):
    pass


class WorkshopNotFoundError(OtpError):
    pass


class OtpInvalidError(OtpError):
    pass


class OtpExpiredError(OtpError):
    pass


class OtpAttemptsExceededError(OtpError):
    pass
