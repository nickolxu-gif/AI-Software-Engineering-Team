class TeamControlError(Exception):
    code = "TEAM_CONTROL_ERROR"


class BoundaryError(TeamControlError):
    code = "BOUNDARY_ERROR"


class GitStateError(TeamControlError):
    code = "GIT_STATE_ERROR"


class ContractError(TeamControlError):
    code = "CONTRACT_ERROR"


class SchemaMigrationRequiredError(TeamControlError):
    code = "SCHEMA_MIGRATION_REQUIRED"


class TransitionError(TeamControlError):
    code = "TRANSITION_ERROR"


class ApprovalError(TeamControlError):
    code = "APPROVAL_ERROR"


class ReconciliationError(TeamControlError):
    code = "RECONCILIATION_ERROR"
