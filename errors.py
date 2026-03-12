class SyncError(Exception):
    pass


class FetchError(SyncError):
    pass


class InvalidConfigError(SyncError):
    pass
